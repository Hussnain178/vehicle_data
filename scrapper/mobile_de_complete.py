import time
from typing import List, Tuple, Dict, Any, Optional
import json
from urllib.parse import urlencode, quote
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.key_mapping import convert_vehicle_data
from utils.filters import *
from configuration.config import Config
from database.db import VehicleDatabase
from logger.logger_setup import LoggerSetup


@dataclass
class ScraperConfig:
    """Configuration for the scraper"""
    scrape_do_token: str = Config.SCRAPE_DO_TOKEN
    max_results_per_range: int = 1000
    max_retries: int = 5
    delay_between_requests: float = .1
    min_response_size: int = 6000
    price_start: int = 0
    price_end: int = 90000
    initial_chunk_size: int = 100


@dataclass
class ScraperStats:
    """Track scraper statistics"""
    total_listings: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    ranges_processed: int = 0
    pages_processed: int = 0
    duplicates_skipped: int = 0
    list_process_per_page: int = 0


class MobileDeScraper:
    """Robust scraper for Mobile.de with dynamic range splitting"""

    FIELD_MAPPING = {
        "attr_cn": "Country Code",
        "attr_z": "Postal Code",
        "attr_loc": "City",
        "attr_fr": "First Registration",
        "attr_pw": "Power (HP)",
        "attr_ft": "Fuel Type",
        "attr_ml": "Milage",
        "attr_cc": "Displacement",
        "attr_tr": "Transmission Type",
        "attr_gi": "Last inspection",
        "attr_ecol": "Exterior Color",
        "attr_door": "# of doors",
        "attr_sc": "# of Seats",
        "HU": "Last inspection",
        "Envnkv.energyConsumption": "Fuel Consumption per 100km",
        "envkv.co2Emissions": "CO2 in g per km",
        "attr_co2class": "EU CO2 Class",
        "attr_eu": "Country version",
        "envkv.consumptionDetails.fuel": None,
        "envkv.emission": None,
        "attr_csmpt": None,
        "attr_emiss": None,
        "availability": None,
        "countryVersion": None,
        "envkv.co2Class": None,
        "envkv.consumption": None
    }

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.log = LoggerSetup("mobile_de_complete.log").get_logger()
        self.unique_features = mobile_features
        self.mobile_car_filters = mobile_car_filters
        self.db_obj = VehicleDatabase(logger=self.log)
        self.thread_limit = Config.MOBILE_THREAD_COUNT

    def _make_request(self, url: str, use_proxy: bool = True) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
                # time.sleep(self.config.delay_between_requests)

                if use_proxy:
                    target_url = quote(url)
                    proxy_url = f"http://api.scrape.do/?url={target_url}&token={self.config.scrape_do_token}"
                    response = requests.get(proxy_url, timeout=30)
                else:
                    response = requests.get(url, timeout=30)

                self.stats.total_requests += 1

                if response.status_code == 200 and len(response.text) > self.config.min_response_size:
                    return response
                elif response.status_code == 410:
                    self.log.info(f"⚠️  HTTP {response.status_code} Returning because Page is not available!")
                    return None
                else:
                    self.log.info(f"⚠️  HTTP {response.status_code} on attempt {attempt + 1}/{self.config.max_retries}")

            except requests.exceptions.Timeout:
                self.log.error(f"⏱️  Timeout on attempt {attempt + 1}/{self.config.max_retries}")
            except requests.exceptions.ConnectionError:
                self.log.error(f"🔌 Connection error on attempt {attempt + 1}/{self.config.max_retries}")
            except Exception as e:
                self.log.error(f"❌ Error on attempt {attempt + 1}/{self.config.max_retries}: {str(e)[:100]}")

            if attempt < self.config.max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff

        self.stats.failed_requests += 1
        return None

    def _extract_json_from_html(self, html_text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON data (window.__INITIAL_STATE__) from HTML response using BeautifulSoup"""
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            scripts = soup.find_all("script")

            for script in scripts:
                # Get script content safely
                script_content = script.string or script.get_text()
                if not script_content:
                    continue

                # Check for the target variable
                if '__INITIAL_STATE__' in script_content:
                    # Extract JSON part before PUBLIC_CONFIG (if present)
                    if 'window.__PUBLIC_CONFIG__' in script_content:
                        json_str = script_content.split('window.__PUBLIC_CONFIG__')[0]
                    else:
                        json_str = script_content

                    # Clean prefix and trailing semicolon
                    json_str = (
                        json_str.replace('window.__INITIAL_STATE__ =', '')
                        .strip()
                        .rstrip(';')
                        .strip()
                    )

                    try:
                        data = json.loads(json_str)
                        return data
                    except json.JSONDecodeError as e:
                        self.log.error(f"❌ JSON decode error: {str(e)[:100]}")
                        return None

            self.log.info("⚠️  No __INITIAL_STATE__ found in HTML")
            return None

        except Exception as e:
            self.log.error(f"❌ Error extracting JSON: {str(e)[:100]}")
            return None

    def get_search_response(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get search results with parameters"""
        full_url = f"{url}?{urlencode(params)}"
        response = self._make_request(full_url)

        if response:
            return self._extract_json_from_html(response.text)
        return None

    def get_detail_response(self, url: str) -> Optional[Dict[str, Any]]:
        """Get product detail page"""
        response = self._make_request(url)

        if response:
            return self._extract_json_from_html(response.text)
        return None

    def generate_price_ranges(self) -> List[Tuple[int, int]]:
        """Generate initial price ranges"""
        ranges = []
        for i in range(self.config.price_start, self.config.price_end, self.config.initial_chunk_size):
            end = min(i + self.config.initial_chunk_size, self.config.price_end)
            ranges.append((i, end))
        return ranges

    def split_range_dynamically(self, price_range: Tuple[int, int], num_results: int) -> List[Tuple[int, int]]:
        """Dynamically split a price range based on number of results"""
        start, end = price_range
        range_size = end - start

        if range_size <= 1:
            self.log.info(f"⚠️  Cannot split range further: ({start}, {end})")
            return [price_range]

        # Calculate chunks with 20% buffer
        estimated_chunks = max(2, int((num_results / self.config.max_results_per_range) * 1.2))
        new_chunk_size = max(1, range_size // estimated_chunks)

        new_ranges = []
        for i in range(start, end, new_chunk_size):
            chunk_end = min(i + new_chunk_size, end)
            if i < chunk_end:
                new_ranges.append((i, chunk_end))

        self.log.info(f"📊 Split range ({start}, {end}) with {num_results} results into {len(new_ranges)} chunks")
        return new_ranges

    def parse_basic_listing(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """Parse basic listing data from search results"""
        parsed = {
            'id': listing.get('id'),
            'url': 'https://suchen.mobile.de' + listing.get('relativeUrl', ''),
            'title': listing.get('title', ''),
            'vc': listing.get('vc', ''),
            'category': listing.get('category', ''),
            'price': '',
            'seller_name': ''
        }

        # Parse price
        if 'price' in listing and listing['price']:
            parsed['price'] = listing['price'].get('gross', '')

        # Parse seller name
        if 'contactInfo' in listing and listing['contactInfo']:
            parsed['seller_name'] = listing['contactInfo'].get('name', '')

        # Parse attributes
        if 'attr' in listing and listing['attr']:
            for key, value in listing['attr'].items():
                parsed[f'attr_{key}'] = value

        return parsed

    def parse_detail_listing(self, basic_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse detailed listing data from product page"""
        listing_id = basic_data.get('id')

        # Check for duplicate
        if self.db_obj.check_id_exists(listing_id, 'mobile'):
            self.db_obj.touch_updated_at(listing_id, 'mobile')
            self.log.info(f"⏭️  Skipping duplicate ID: {listing_id}")
            self.stats.duplicates_skipped += 1
            return None

        try:
            product_response = self.get_detail_response(basic_data['url'])

            if not product_response:
                self.log.info(f"⚠️  Failed to get details for: {basic_data['url']}")
                return basic_data

            # Navigate to ad data
            ad_data = (product_response.get('search', {})
                       .get('vip', {})
                       .get('ads', {})
                       .get(str(basic_data['id']), {})
                       .get('data', {})
                       .get('ad', {}))

            if not ad_data:
                self.log.info(f"⚠️  No ad data found for ID: {basic_data['id']}")
                return basic_data
            basic_data['vehicle_make'] = ad_data.get('make', None)
            basic_data['vehicle_model'] = ad_data.get('model', None)
            basic_data['vehicle_modelVersionInput'] = ad_data.get('subTitle', None)
            # Parse additional attributes
            skip_tags = ["firstRegistration", "power", "fuel", "mileage", "cubicCapacity",
                         "transmission", "hu", "doorCount", "numSeats", "emissionClass",
                         "numberOfPreviousOwners"]

            for attribute in ad_data.get('attributes', []):
                tag = attribute.get('tag')
                if tag and tag not in skip_tags:
                    basic_data[tag] = attribute.get('value')

            # Parse description
            html_desc = ad_data.get('htmlDescription', '')
            if html_desc:
                soup = BeautifulSoup(html_desc, "html.parser")
                basic_data['description'] = soup.get_text().strip()
            else:
                basic_data['description'] = ''

            # Parse images
            gallery_images = ad_data.get('galleryImages', [])
            image_urls = []
            for img in gallery_images:
                if 'srcSet' in img:
                    src_set = img['srcSet'].split(',')[-1].strip()
                    url = src_set.split(' ')[0]
                    image_urls.append(url)
            basic_data['images'] = json.dumps(image_urls)

            # Parse features
            features = ad_data.get('features', [])
            self.unique_features.update(features)

            for feature in self.unique_features:
                basic_data[feature] = feature in features

            # Apply field mapping
            for old_key in list(basic_data.keys()):
                if old_key in self.FIELD_MAPPING:
                    value = basic_data.pop(old_key)
                    new_key = self.FIELD_MAPPING[old_key]
                    if new_key:
                        basic_data[new_key] = value


            self.log.info(f"✅ Parsed: {basic_data.get('title', 'Unknown')[:50]} - €{basic_data.get('price', 'N/A')}")
            return basic_data

        except Exception as e:
            self.log.error(f"❌ Error parsing details for {basic_data.get('url', 'Unknown')}: {str(e)[:100]}")
            return basic_data

    def process_listings(self, listings: List[Dict[str, Any]]):
        """Process multiple listings using thread pool"""
        lock = threading.Lock()  # for thread-safe list & counter updates

        def process_single(listing):
            try:
                if listing.get('type') != 'ad':
                    return None

                basic_data = self.parse_basic_listing(listing)
                detailed_data = self.parse_detail_listing(basic_data)

                if detailed_data:
                    detailed_data['interior_color'] = None
                    detailed_data['interior_type'] = None
                    final_data = convert_vehicle_data(detailed_data, 'mobile')

                    with lock:  # ensure safe updates
                        self.db_obj.insert_vehicle(final_data)
                        self.stats.total_listings += 1
                        self.stats.list_process_per_page += 1

            except Exception as e:
                self.log.error(f"❌ Error processing listing: {e}")

        # 🔹 Use ThreadPoolExecutor with max 5 workers
        with ThreadPoolExecutor(max_workers=self.thread_limit) as executor:
            # Submit all tasks and collect futures
            futures = [executor.submit(process_single, listing) for listing in listings]

            # Collect results as they complete
            for future in as_completed(futures):
                future.result()

    def process_price_range(self, price_range: Tuple[int, int], extra_params: Optional[Dict[str, Any]] = None) -> None:
        """Process a single price range with dynamic chunking"""
        self.log.info(f"\n{'=' * 60}")
        self.log.info(f"💰 Processing price range: €{price_range[0]} - €{price_range[1]}")

        # Build search parameters
        params = {
            "dam": "false",
            "isSearchRequest": "true",
            "od": "up",
            "p": f"{price_range[0]}:{price_range[1]}",
            "pageNumber": "1",
            "ref": "srpNextPage",
            "refId": "af514bfd-2f32-dde4-c01b-1f966561549f",
            "s": "Car",
            "sb": "p",
            "vc": "Car"
        }

        if extra_params:
            params.update(extra_params)

        # Get first page
        url = "https://suchen.mobile.de/fahrzeuge/search.html"
        response = self.get_search_response(url, params)

        if not response or 'search' not in response:
            self.log.info(f"❌ Failed to get response for range {price_range}")
            return

        # Get search results metadata
        search_results = response.get('search', {}).get('srp', {}).get('data', {}).get('searchResults', {})
        num_results = search_results.get('numResultsTotal', 0)

        self.log.info(f"📈 Found {num_results} results")

        if num_results == 0:
            self.log.info("⏭️  No results, skipping range")
            return

        # Handle range splitting if needed
        if num_results > self.config.max_results_per_range and not extra_params:
            if price_range[1] - price_range[0] == 1:
                self.log.info(f"🔄 Single price point with {num_results} results, trying sorting variations")
                # for sort_params in [{'sb': 'doc', 'od': 'up'}, {'sb': 'doc', 'od': 'down'}]:
                # for sort_params in [{'sb': 'doc', 'od': 'up'}, {'sb': 'doc', 'od': 'down'}]:
                for filter in self.mobile_car_filters:
                    key = list(filter.keys())[0]
                    value = list(filter.values())[0]
                    sort_params = {"ms": value}
                    self.log.info(f"Fetching info of car {key} with range {price_range}")
                    self.process_price_range(price_range, sort_params)
                return

            self.log.info(f"⚠️  Too many results ({num_results}), splitting range...")
            sub_ranges = self.split_range_dynamically(price_range, num_results)

            for sub_range in sub_ranges:
                self.process_price_range(sub_range, extra_params)
            return

        # Process all pages
        num_pages = search_results.get('numPages', 1)
        self.log.info(f"📄 Processing {num_pages} page(s)")

        for page in range(1, num_pages + 1):
            self.log.info(f"  📖 Page {page}/{num_pages}")

            if page == 1:
                current_response = response
            else:
                params['pageNumber'] = str(page)
                current_response = self.get_search_response(url, params)

            if not current_response or 'search' not in current_response:
                self.log.info(f"  ⚠️  Failed to get page {page}")
                continue

            # Extract listings
            listings = (current_response.get('search', {})
                        .get('srp', {})
                        .get('data', {})
                        .get('searchResults', {})
                        .get('items', []))

            self.process_listings(listings)

            self.stats.pages_processed += 1
            self.log.info(f"  ✅ Parsed {self.stats.list_process_per_page} listings (Total: {self.stats.total_listings})")
            self.stats.list_process_per_page = 0
        self.stats.ranges_processed += 1

    def run(self):
        """Main execution method"""
        self.log.info("🚀 Starting Mobile.de scraping...")
        self.log.info(f"⚙️  Config: €{self.config.price_start}-€{self.config.price_end}, "
              f"chunk size: €{self.config.initial_chunk_size}")
        start_date = datetime.now().strftime("%d-%m-%Y")
        start_time = time.time()
        price_ranges = self.generate_price_ranges()

        self.log.info(f"📊 Generated {len(price_ranges)} initial price ranges")

        for i, price_range in enumerate(price_ranges, 1):
            try:
                self.log.info(f"\n{'#' * 60}")
                self.log.info(f"Range {i}/{len(price_ranges)}")
                self.process_price_range(price_range)

            except KeyboardInterrupt:
                self.log.error("\n\n⚠️  Scraping interrupted by user")
                break
            except Exception as e:
                self.log.error(f"❌ Error processing range {price_range}: {str(e)[:200]}")
                continue

        elapsed_time = time.time() - start_time
        self.db_obj.mark_unavailable_before(start_date, 'mobile')
        # self.log. final statistics
        self.log.info(f"\n{'=' * 60}")
        self.log.info("📊 SCRAPING COMPLETED")
        self.log.info(f"{'=' * 60}")
        self.log.info(f"✅ Total listings collected: {self.stats.total_listings}")
        self.log.info(f"⏭️  Duplicates skipped: {self.stats.duplicates_skipped}")
        self.log.info(f"📄 Pages processed: {self.stats.pages_processed}")
        self.log.info(f"📦 Ranges processed: {self.stats.ranges_processed}")
        self.log.info(f"🌐 Total requests: {self.stats.total_requests}")
        self.log.info(f"❌ Failed requests: {self.stats.failed_requests}")
        self.log.info(f"⏱️  Time elapsed: {elapsed_time:.2f} seconds")
        if elapsed_time > 0:
            self.log.info(f"⚡ Average: {self.stats.total_listings / elapsed_time:.2f} listings/sec")
        self.log.info(f"{'=' * 60}")


def main():
    """Entry point for the scraper"""
    # Create custom configuration if needed
    config = ScraperConfig(
        price_start=0,
        price_end=90000,
        initial_chunk_size=100,
        max_retries=5,
        delay_between_requests=.1
    )

    # Initialize and run scraper
    scraper = MobileDeScraper(config)
    scraper.run()
