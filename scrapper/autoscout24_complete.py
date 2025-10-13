import time
from typing import List, Tuple, Dict, Any, Optional
import json
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from utils.key_mapping import convert_vehicle_data
from utils.filters import *
import threading
from datetime import datetime
from proxies.webshare import WEBSHARE
from database.db import VehicleDatabase
from logger.logger_setup import LoggerSetup


@dataclass
class ScraperConfig:
    """Configuration for the scraper"""
    max_results_per_range: int = 400
    max_retries: int = 3
    delay_between_requests: float = 1.0
    price_start: int = 0
    price_end: int = 100000
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


class AutoScout24Scraper:
    """Robust scraper for AutoScout24 with dynamic range splitting"""

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.log = LoggerSetup("autoscout24_complete.log").get_logger()
        self.webshare_obj = WEBSHARE()
        self.unique_features = autoscout24_features
        self.autoscout24_car_filters = autoscout24_car_filters
        self.db_obj = VehicleDatabase(logger=self.log)

    def _make_request(self, url: str, params: Optional[Dict[str, Any]] = None,
                      is_pagination: bool = False) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
                # time.sleep(self.config.delay_between_requests)

                if is_pagination:
                    headers = {
                        "accept": "*/*",
                        "accept-language": "en-PK,en;q=0.9,ur-PK;q=0.8,ur;q=0.7,en-GB;q=0.6,en-US;q=0.5",
                        "priority": "u=1, i",
                        "referer": url,
                        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                        "sec-fetch-dest": "empty",
                        "sec-fetch-mode": "cors",
                        "sec-fetch-site": "same-origin",
                        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "x-nextjs-data": "1"
                    }
                else:
                    headers = {
                        "Upgrade-Insecure-Requests": "1",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"'
                    }

                response = requests.get(url, params=params, headers=headers,
                                        proxies=self.webshare_obj.get_proxy(), timeout=30)
                self.stats.total_requests += 1

                if response.status_code == 200:
                    return response
                else:
                    self.log.warning(f"⚠️  HTTP {response.status_code} on attempt {attempt + 1}/{self.config.max_retries}")

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

    def get_pagination_response(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get search results with parameters"""
        response = self._make_request(url, params=params, is_pagination=True)

        if response:
            try:
                return response.json()
            except json.JSONDecodeError:
                self.log.error("❌ Failed to parse JSON response")
                return None
        return None

    def get_detail_response(self, url: str) -> Optional[Dict[str, Any]]:
        """Get product detail page and extract JSON (__NEXT_DATA__) using BeautifulSoup"""
        response = self._make_request(url, is_pagination=False)

        if not response:
            return None

        try:
            soup = BeautifulSoup(response.text, "html.parser")

            # Find the <script> tag with id="__NEXT_DATA__"
            script_tag = soup.find("script", id="__NEXT_DATA__")
            if not script_tag:
                self.log.info("⚠️ No <script id='__NEXT_DATA__'> found on page")
                return None

            # Get the script content
            script_content = script_tag.string or script_tag.get_text()
            if not script_content:
                self.log.warning("⚠️ Script tag found but content is empty")
                return None

            # Parse JSON
            return json.loads(script_content)

        except json.JSONDecodeError as e:
            self.log.error(f"❌ JSON decode error: {str(e)[:100]}")
            return None
        except Exception as e:
            self.log.error(f"❌ Error parsing detail page: {str(e)[:100]}")
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

    def parse_listing(self, data: dict) -> dict:
        """Parse individual listing data"""
        parsed = {}
        try:
            # ID and URL
            parsed["id"] = data.get("id")
            parsed["url"] = data.get("url")

            # Price
            price = data.get("price", {}).get("priceFormatted")
            if price:
                parsed["price"] = price

            # Images
            images = data.get("images")
            if isinstance(images, list) and images:
                parsed["images"] = json.dumps(images, ensure_ascii=False)
            else:
                parsed["images"] = json.dumps([])
            # Vehicle details
            vehicle = data.get("vehicle", {})
            for key, value in vehicle.items():
                if value not in [None, "", [], {}]:
                    parsed[f"vehicle_{key}"] = value

            # Location
            loc = data.get("location", {})
            for key, value in loc.items():
                if value not in [None, "", [], {}]:
                    parsed[f"location_{key}"] = value

            # Seller
            seller = data.get("seller", {}).get('contactName', '')
            parsed['seller_name'] = seller

            # Tracking
            tracking = data.get("tracking", {})
            for key, value in tracking.items():
                if value not in [None, "", [], {}]:
                    parsed[f"tracking_{key}"] = value

            # Tracking Parameters
            for param in data.get("trackingParameters", []):
                key = param.get("key")
                value = param.get("value")
                if key and value not in [None, "", [], {}]:
                    parsed[f"tracking_{key}"] = value

            # Vehicle Details with translations
            translations = {
                "Kilometerstand": "mileage",
                "Getriebe": "transmission",
                "Erstzulassung": "first_registration",
                "Kraftstoff": "fuel",
                "Leistung": "power",
                "Kraftstoffverbrauch": "fuel_consumption",
                "CO₂-Emissionen": "co2_emission",
            }

            for item in data.get("vehicleDetails", []):
                label = item.get("ariaLabel")
                value = item.get("data")
                if label and value not in [None, "", [], {}]:
                    label_en = translations.get(label, label)
                    parsed[f"vehicle_detail_{label_en.replace(' ', '_').lower()}"] = value

            # Clean up
            parsed = {k: v for k, v in parsed.items()
                      if v not in [None, "", [], {}, "N/A", "unknown"]}

            return parsed
        except Exception as e:
            self.log.error(f"❌ Error parsing listing: {e}")
            return {}

    def parse_detail_listing(self, basic_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse detailed listing data from product page"""
        listing_id = basic_data.get('id')

        # Check for duplicate
        if self.db_obj.check_id_exists(listing_id, 'autoscout24'):
            self.db_obj.touch_updated_at(listing_id, 'autoscout24')
            self.log.info(f"⏭️  Skipping duplicate ID: {listing_id}")
            self.stats.duplicates_skipped += 1
            return None

        try:
            # Construct full URL
            url = 'https://www.autoscout24.de' + basic_data.get('url', '')
            basic_data['url'] = url

            product_response = self.get_detail_response(url)

            if not product_response:
                self.log.info(f"⚠️  Failed to get details for: {url}")
                return basic_data

            # Extract description
            try:
                description = product_response['props']['pageProps']['listingDetails']['description']
                if description:
                    soup = BeautifulSoup(description, "html.parser")
                    clean_text = soup.get_text().strip()
                    basic_data['description'] = clean_text
                else:
                    basic_data['description'] = ''
            except:
                basic_data['description'] = ''
            try:
                features = [p['id']['formatted'] for p in
                            product_response['props']['pageProps']['listingDetails']['vehicle']['rawData']['equipment'][
                                'as24']]
            except:
                features = []
            self.unique_features.update(features)

            new_data = {}
            for feature in self.unique_features:
                new_data[feature] = feature in features
            try:
                for key, value in product_response['props']['pageProps']['listingDetails']['vehicle'].items():
                    if isinstance(value, (type(None), str, int, float, bool)):
                        new_data[key] = value
            except:
                pass
            try:
                for key, value in product_response['props']['pageProps']['listingDetails']['vehicle'].items():
                    if isinstance(value, dict) and 'formatted' in value:
                        new_data[key] = value['formatted']
            except:
                pass
            try:
                for key, value in product_response['props']['pageProps']['listingDetails']['vehicle']['wltp'].items():
                    if value:
                        new_data[key] = value['formatted']
                    else:
                        new_data[key] = None
            except:
                pass
            try:
                for key, value in product_response['props']['pageProps']['listingDetails']['vehicle'][
                    'costModel'].items():
                    if value:
                        new_data[key] = value
            except:
                pass
            try:
                new_data['price_text'] = product_response['props']['pageProps']['listingDetails']['prices']['error'][
                    'text']
            except:
                pass
            try:
                new_data['identifier'] = product_response['props']['pageProps']['listingDetails']['identifier'][
                    'offerReference']
            except:
                pass

            basic_data.update(new_data)
            self.log.info(f"✅ Parsed: {basic_data.get('url', 'Unknown')[:50]} - €{basic_data.get('price', 'N/A')}")
            return basic_data

        except Exception as e:
            self.log.error(f"❌ Error parsing details for {basic_data.get('url', 'Unknown')}: {str(e)[:100]}")
            return basic_data


    def process_listings(self, listings: List[Dict[str, Any]]):
        """Process multiple listings concurrently (max 20 threads)"""
        threads = []
        lock = threading.Lock()  # for thread-safe updates
        def process_single(listing):
            try:
                basic_data = self.parse_listing(listing)
                if not basic_data:
                    return

                detailed_data = self.parse_detail_listing(basic_data)
                if not detailed_data:
                    return

                # build title
                detailed_data[
                    'title'] = f"{detailed_data.get('vehicle_make', '')} {detailed_data.get('vehicle_model', '')} {detailed_data.get('vehicle_modelVersionInput', '')}".strip()

                final_data = convert_vehicle_data(detailed_data, 'autoscout24')

                # thread-safe append and counter increment
                with lock:
                    self.db_obj.insert_vehicle(final_data)
                    self.stats.total_listings += 1
                    self.stats.list_process_per_page += 1


            except Exception as e:
                self.log.error(f"❌ Error processing listing: {e}")

        # 🔹 Create and start threads
        for listing in listings:
            t = threading.Thread(target=process_single, args=(listing,))
            t.start()
            threads.append(t)

        # 🔹 Wait for all threads to complete
        for t in threads:
            t.join()

    def process_price_range(self, price_range: Tuple[int, int],
                            extra_params: Optional[Dict[str, Any]] = None) -> None:
        """Process a single price range with dynamic chunking"""
        self.log.info(f"\n{'=' * 60}")
        self.log.info(f"💰 Processing price range: €{price_range[0]} - €{price_range[1]}")

        # Build search parameters
        params = {
            "atype": "C",
            "cy": "D",
            "damaged_listing": "exclude",
            "desc": "1",
            "ocs_listing": "include",
            "powertype": "kw",
            "pricefrom": str(price_range[0]),
            "priceto": str(price_range[1]),
            "search_id": "10ct8w6zph5",
            "sort": "price",
            "source": "listpage_pagination",
            "ustate": "N,U",
            "page": "1"
        }

        if extra_params:
            params.update(extra_params)

        # Get first page
        url = "https://www.autoscout24.de/_next/data/as24-search-funnel_main-20250924171425/lst.json"
        response = self.get_pagination_response(url, params)

        if not response or 'pageProps' not in response:
            self.log.info(f"❌ Failed to get response for range {price_range}")
            return

        page_props = response['pageProps']
        num_results = page_props.get('numberOfResults', 0)

        self.log.info(f"📈 Found {num_results} results")

        if num_results == 0:
            self.log.info("⏭️  No results, skipping range")
            return

        # Handle range splitting if needed
        if num_results > self.config.max_results_per_range and not extra_params:
            if price_range[1] - price_range[0] == 1:
                self.log.info(f"🔄 Single price point with {num_results} results, trying brand filters")
                for car_filter in self.autoscout24_car_filters:
                    brand_name = list(car_filter.keys())[0]
                    brand_id = list(car_filter.values())[0]
                    filter_params = {"mmmv": f'{brand_id}|||'}
                    self.log.info(f"Fetching info for {brand_name} with range {price_range}")
                    self.process_price_range(price_range, filter_params)
                return

            self.log.info(f"⚠️  Too many results ({num_results}), splitting range...")
            sub_ranges = self.split_range_dynamically(price_range, num_results)

            for sub_range in sub_ranges:
                self.process_price_range(sub_range, extra_params)
            return

        # Process all pages
        num_pages = page_props.get('numberOfPages', 1)
        self.log.info(f"📄 Processing {num_pages} page(s)")

        for page in range(1, num_pages + 1):
            self.log.info(f"  📖 Page {page}/{num_pages}")

            if page == 1:
                current_response = response
            else:
                params['page'] = str(page)
                current_response = self.get_pagination_response(url, params)

            if not current_response or 'pageProps' not in current_response:
                self.log.info(f"  ⚠️  Failed to get page {page}")
                continue

            # Extract listings
            listings = current_response['pageProps'].get('listings', [])
            self.process_listings(listings)
            self.stats.pages_processed += 1
            self.log.info(f"  ✅ Parsed {self.stats.list_process_per_page} listings (Total: {self.stats.total_listings})")
            self.stats.list_process_per_page = 0

        self.stats.ranges_processed += 1

    def run(self):
        """Main execution method"""
        self.log.info("🚀 Starting AutoScout24 scraping...")
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
        self.db_obj.mark_unavailable_before(start_date, 'autoscout24')
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
        price_end=100000,
        initial_chunk_size=100,
        max_retries=3,
        delay_between_requests=.01,
        max_results_per_range=4000
    )

    # Initialize and run scraper
    scraper = AutoScout24Scraper(config)
    scraper.run()

