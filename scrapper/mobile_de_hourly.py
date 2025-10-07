import time
from typing import List, Dict, Any, Optional
from scrapy import Selector
import json
from urllib.parse import urlencode, quote
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from utils.key_mapping import convert_vehicle_data
from utils.filters import *
from configuration.config import Config
from database.db import VehicleDatabase


@dataclass
class ScraperConfig:
    """Configuration for the hourly scraper"""
    scrape_do_token: str = Config.SCRAPE_DO_TOKEN
    max_pages: int = 50
    max_retries: int = 5
    delay_between_requests: float = .1
    min_response_size: int = 6000
    max_workers: int = 5


@dataclass
class ScraperStats:
    """Track scraper statistics"""
    total_listings: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    pages_processed: int = 0
    duplicates_skipped: int = 0
    list_process_per_page: int = 0
    consective_no_data_page_count: int = 0


class MobileDeHourlyScraper:
    """Hourly scraper for Mobile.de - fetches latest listings sorted by date"""

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
        self.unique_features = mobile_features
        self.db_obj = VehicleDatabase()

    def _make_request(self, url: str, use_proxy: bool = True) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
                if use_proxy:
                    target_url = quote(url)
                    proxy_url = f"http://api.scrape.do/?url={target_url}&token={self.config.scrape_do_token}"
                    response = requests.get(proxy_url, timeout=30)
                else:
                    response = requests.get(url, timeout=30)

                self.stats.total_requests += 1

                if response.status_code == 200 and len(response.text) > self.config.min_response_size:
                    return response
                else:
                    print(f"⚠️  HTTP {response.status_code} on attempt {attempt + 1}/{self.config.max_retries}")

            except requests.exceptions.Timeout:
                print(f"⏱️  Timeout on attempt {attempt + 1}/{self.config.max_retries}")
            except requests.exceptions.ConnectionError:
                print(f"🔌 Connection error on attempt {attempt + 1}/{self.config.max_retries}")
            except Exception as e:
                print(f"❌ Error on attempt {attempt + 1}/{self.config.max_retries}: {str(e)[:100]}")

            if attempt < self.config.max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff

        self.stats.failed_requests += 1
        return None

    def _extract_json_from_html(self, html_text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON data from HTML response"""
        try:
            selector = Selector(text=html_text)
            script_texts = selector.css('script::text').getall()

            for script in script_texts:
                if '__INITIAL_STATE__' in script:
                    json_str = script.split('window.__PUBLIC_CONFIG__')[0]
                    json_str = json_str.replace('window.__INITIAL_STATE__ =', '').strip()
                    return json.loads(json_str)

            print("⚠️  No __INITIAL_STATE__ found in HTML")
            return None

        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error: {str(e)[:100]}")
            return None
        except Exception as e:
            print(f"❌ Error extracting JSON: {str(e)[:100]}")
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
            print(f"⏭️  Skipping duplicate ID: {listing_id}")
            self.stats.duplicates_skipped += 1
            return None

        try:
            product_response = self.get_detail_response(basic_data['url'])

            if not product_response:
                print(f"⚠️  Failed to get details for: {basic_data['url']}")
                return basic_data

            # Navigate to ad data
            ad_data = (product_response.get('search', {})
                       .get('vip', {})
                       .get('ads', {})
                       .get(str(basic_data['id']), {})
                       .get('data', {})
                       .get('ad', {}))

            if not ad_data:
                print(f"⚠️  No ad data found for ID: {basic_data['id']}")
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
                basic_data['description'] = soup.get_text(separator="\n").strip()
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

            print(f"✅ Parsed: {basic_data.get('title', 'Unknown')[:50]} - €{basic_data.get('price', 'N/A')}")
            return basic_data

        except Exception as e:
            print(f"❌ Error parsing details for {basic_data.get('url', 'Unknown')}: {str(e)[:100]}")
            return basic_data

    def process_listings(self, listings: List[Dict[str, Any]]):
        """Process multiple listings using thread pool"""
        parsed_listings = []
        lock = threading.Lock()

        def process_single(listing):
            try:
                if listing.get('type') != 'ad':
                    return

                basic_data = self.parse_basic_listing(listing)
                detailed_data = self.parse_detail_listing(basic_data)

                if detailed_data:
                    detailed_data['interior_color'] = None
                    detailed_data['interior_type'] = None
                    final_data = convert_vehicle_data(detailed_data, 'mobile')

                    with lock:
                        self.db_obj.insert_vehicle(final_data)
                        self.stats.total_listings += 1
                        self.stats.list_process_per_page += 1


            except Exception as e:
                print(f"❌ Error processing listing: {e}")

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = [executor.submit(process_single, listing) for listing in listings]

            for future in as_completed(futures):
                future.result()



    def run(self):
        """Main execution method - fetch latest listings sorted by date"""
        print("🚀 Starting Mobile.de hourly scraping...")
        print(f"⚙️  Config: Max {self.config.max_pages} pages, sorted by date")

        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"🕐 Run timestamp: {timestamp}")

        url = "https://suchen.mobile.de/fahrzeuge/search.html"
        page_number = 1

        try:
            while page_number < self.config.max_pages:
                print(f"\n{'=' * 60}")
                print(f"📖 Processing page {page_number}")

                # Build search parameters
                params = {
                    "dam": "false",
                    "isSearchRequest": "true",
                    "od": "down",
                    "pageNumber": str(page_number),
                    "ref": "srpNextPage",
                    "s": "Car",
                    "sb": "doc",  # Sort by date
                    "vc": "Car"
                }

                response = self.get_search_response(url, params)

                if not response or 'search' not in response:
                    print(f"❌ Failed to get response for page {page_number}")
                    break

                # Get search results
                search_results = response.get('search', {}).get('srp', {}).get('data', {}).get('searchResults', {})
                num_results = search_results.get('numResultsTotal', 0)
                num_pages = search_results.get('numPages', 0)

                if page_number == 1:
                    print(f"📈 Total results available: {num_results}")
                    print(f"📄 Total pages available: {num_pages}")

                # Extract listings
                listings = search_results.get('items', [])

                if not listings:
                    print("⚠️  No listings found on this page")
                    break

                print(f"🔄 Processing {len(listings)} listings from this page")

                # Process listings
                self.process_listings(listings)

                self.stats.pages_processed += 1

                print(f"✅ Parsed {self.stats.list_process_per_page} listings (Total: {self.stats.total_listings}")
                if self.stats.list_process_per_page == 0:
                    self.stats.consective_no_data_page_count += 1
                else:
                    self.stats.list_process_per_page = 0
                    self.stats.consective_no_data_page_count = 0

                if self.stats.consective_no_data_page_count == 3:
                    print(f"📄 Three pages have no New data Stopping script!")
                    break

                if page_number >= num_pages:
                    print(f"📄 Reached last page ({num_pages})")
                    break
                page_number += 1

                # Add small delay between pages
                time.sleep(self.config.delay_between_requests)

        except KeyboardInterrupt:
            print("\n\n⚠️  Scraping interrupted by user")
        except Exception as e:
            print(f"❌ Error during scraping: {str(e)[:200]}")

        elapsed_time = time.time() - start_time

        # Print final statistics
        print(f"\n{'=' * 60}")
        print("📊 SCRAPING COMPLETED")
        print(f"{'=' * 60}")
        print(f"✅ Total listings collected: {self.stats.total_listings}")
        print(f"⏭️  Duplicates skipped: {self.stats.duplicates_skipped}")
        print(f"📄 Pages processed: {self.stats.pages_processed}")
        print(f"🌐 Total requests: {self.stats.total_requests}")
        print(f"❌ Failed requests: {self.stats.failed_requests}")
        print(f"⏱️  Time elapsed: {elapsed_time:.2f} seconds")
        if elapsed_time > 0:
            print(f"⚡ Average: {self.stats.total_listings / elapsed_time:.2f} listings/sec")
        print(f"{'=' * 60}")


def main():
    """Entry point for the hourly scraper"""
    # Create configuration
    config = ScraperConfig(
        max_pages=50,
        max_retries=5,
        delay_between_requests=.1,
        max_workers=5
    )

    # Initialize and run scraper
    scraper = MobileDeHourlyScraper(config)
    scraper.run()
