import json
import time
from curl_cffi import requests
import scrapy
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from proxies.webshare import WEBSHARE
from database.db import VehicleDatabase
from logger.logger_setup import LoggerSetup
from configuration.config import Config
from utils.key_mapping import convert_vehicle_data


@dataclass
class ScraperConfig:
    """Configuration for the Otomoto scraper"""
    max_retries: int = 5
    timeout: int = 30
    delay_between_requests: float = 0.1
    batch_size: int = 20  # Number of concurrent detail requests
    consective_no_data_page_count: int = 0


@dataclass
class ScraperStats:
    """Track scraper statistics"""
    total_listings: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    pages_processed: int = 0
    duplicates_skipped: int = 0
    details_fetched: int = 0


class OtomotoScraper:
    """Scraper for Otomoto.pl with database integration and logging"""

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.log = LoggerSetup("otomoto_complete.log").get_logger()
        self.webshare_obj = WEBSHARE()
        self.db_obj = VehicleDatabase(logger=self.log)
        self.thread_limit = Config.OTOMOTO_THREAD_COUNT if hasattr(Config, 'OTOMOTO_THREAD_COUNT') else 5

        # Base configuration
        self.base_url = "https://www.otomoto.pl/graphql"

        # Request headers
        self.headers = {
            "accept": "application/graphql-response+json, application/graphql+json, application/json",
            "accept-language": "en-PK,en;q=0.9",
            "referer": "https://www.otomoto.pl/osobowe",
            "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sitecode": "otomotopl",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "x-transaction-id": "yYQgQP3soeuOf6leIKTz8w9RddDoQmD72QTvTjvoHZAKtTD6rNo0-Q=="
        }

        # GraphQL query variables
        self.variables = {
            "experiments": [
                {"key": "MCTA-1463", "variant": "a"},
                {"key": "CARS-79025", "variant": "b"},
                {"key": "CARS-79026", "variant": "a"},
                {"key": "CARS-79505", "variant": "b"},
                {"key": "CARS-64661", "variant": "b"}
            ],
            "filters": [{"name": "category_id", "value": "29"}],
            "includeCepik": True,
            "includeFiltersCounters": False,
            "includeNewPromotedAds": False,
            "includePriceEvaluation": True,
            "includePromotedAds": False,
            "includeRatings": False,
            "includeSortOptions": False,
            "includeSuggestedFilters": False,
            "maxAge": 60,
            "page": 1,
            "parameters": [
                "make", "offer_type", "show_pir", "fuel_type", "gearbox",
                "country_origin", "mileage", "engine_capacity", "engine_code",
                "engine_power", "first_registration_year", "model",
                "version", "year"
            ],
            "promotedInput": {},
            "searchTerms": [],
            "sortBy": "created_at_first:desc"
        }

    def _make_request(self, url: str, method: str = "GET", **kwargs) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
                proxy = self.webshare_obj.get_proxy()
                response = requests.get(

                    url,
                    proxies=proxy,
                    timeout=self.config.timeout,
                    impersonate="chrome",
                    **kwargs
                )
                self.stats.total_requests += 1

                if response.status_code == 200:
                    return response
                else:
                    self.log.warning(
                        f"⚠️ HTTP {response.status_code} on attempt {attempt + 1}/{self.config.max_retries}"
                    )
            except Exception as e:
                self.log.error(f"❌ Error on attempt {attempt + 1}/{self.config.max_retries}: {str(e)[:100]}")

            if attempt < self.config.max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff

        self.stats.failed_requests += 1
        return None

    def fetch_listing_page(self, page_no: int) -> Optional[Dict[str, Any]]:
        """Fetch a single listing page from GraphQL API"""
        self.variables["page"] = page_no
        params = {
            "operationName": "listingScreen",
            "variables": json.dumps(self.variables, separators=(",", ":")),
            "extensions": json.dumps({
                "persistedQuery": {
                    "sha256Hash": "e78bd5939b000e39e9f2ca157b3068e014d4036b7e4af4c05086dd2c185f7a93",
                    "version": 1
                }
            })
        }

        response = self._make_request(
            self.base_url,
            params=params,
            headers=self.headers
        )

        if response:
            try:
                return response.json()
            except json.JSONDecodeError:
                self.log.error("❌ Failed to parse JSON response")
                return None
        return None

    def fetch_advert_details(self, car_url: str, vehicle_id: str) -> Dict[str, Any]:
        """Fetch detailed information for a single vehicle"""
        response = self._make_request(car_url, headers=self.headers)
        if not response:
            self.log.warning(f"⚠️ Failed to fetch details for vehicle {vehicle_id} | {car_url}")
            return {}

        try:
            sel = scrapy.Selector(text=response.text)
            raw_json = sel.css('#__NEXT_DATA__::text').get()

            if not raw_json:
                self.log.warning(f"⚠️ No __NEXT_DATA__ found for vehicle {vehicle_id}")
                return {}

            data = json.loads(raw_json)
            advert = data['props']['pageProps']['advert']

            # Extract images
            images = json.dumps([p['url'] for p in advert['images']['photos']])

            # Extract and clean description
            description = advert.get("description", "")
            if description:
                soup = BeautifulSoup(description, "html.parser")
                description = soup.get_text().strip()

            # Extract details (non-boolean values)
            details_dict = {
                k: d['values'][0]['value']
                for k, d in advert['parametersDict'].items()
                if d['values'][0]['value'] not in ['1', '0'] or k in {
                    'cant_see_my_version', 'engine_power', 'mileage',
                    'number_engines', 'vendors_warranty_valid_until_date'
                }
            }

            # Extract features (boolean values)
            features_dict = {
                k: True if d['values'][0]['value'] == '1' else False
                for k, d in advert['parametersDict'].items()
                if d['values'][0]['value'] in ['1', '0'] and k not in {
                    'cant_see_my_version', 'engine_power', 'mileage',
                    'number_engines', 'vendors_warranty_valid_until_date'
                }
            }

            result = {
                "vehicle_id": advert.get("id"),
                "vehicle_title": advert.get("title"),
                "property_updatedAt": advert.get("updatedAt"),
                "seller_name": advert.get("seller", {}).get("name"),
                "images": images,
                "description": description,
                **features_dict,
                **details_dict
            }

            self.stats.details_fetched += 1
            return result

        except Exception as e:
            self.log.error(f"❌ Error parsing details for vehicle {vehicle_id}: {str(e)[:100]}")
            return {}

    def parse_listing(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Parse basic listing information from GraphQL response"""
        try:
            return {
                "url": node['url'],
                "title": node['title'],
                "property_created_at": node['createdAt'],
                "shortDescription": node.get('shortDescription'),
                "price": node['price']['amount']['value'],
                "vehicle_id": node.get('id')
            }
        except Exception as e:
            self.log.error(f"❌ Error parsing listing: {str(e)[:100]}")
            return {}

    def process_listings(self, listings: List[Dict[str, Any]]) -> None:
        """Process a batch of listings and save to database"""
        if not listings:
            return

        self.log.info(f"  ⚙️ Processing {len(listings)} listings")

        # Filter out listings already in database
        new_listings = []
        for listing in listings:
            vehicle_id = listing.get('vehicle_id')
            if not vehicle_id:
                continue

            if self.db_obj.check_id_exists(vehicle_id, 'otomoto'):
                self.stats.duplicates_skipped += 1
                continue

            new_listings.append(listing)

        if not new_listings:
            self.log.info(f"  ⭐ All {len(listings)} listings already in database")
            self.config.consective_no_data_page_count += 1
            return
        self.config.consective_no_data_page_count = 0
        self.log.info(f"  📝 Found {len(new_listings)} new listings to fetch details")

        # Fetch details concurrently
        all_data = []
        with ThreadPoolExecutor(max_workers=self.config.batch_size) as executor:
            futures = []
            for listing in new_listings:
                future = executor.submit(
                    self.fetch_advert_details,
                    listing['url'],
                    listing['vehicle_id']
                )
                futures.append((future, listing))

            for future, listing in futures:
                try:
                    details = future.result()
                    if details:
                        # Merge basic listing info with detailed info
                        full_data = {**listing, **details}
                        all_data.append(full_data)
                except Exception as e:
                    self.log.error(f"❌ Error fetching details: {str(e)[:100]}")

        # Save to database
        if all_data:
            for data in all_data:
                converted_data = convert_vehicle_data(data, 'otomoto')
                inserted = self.db_obj.insert_vehicle(converted_data)
                if inserted:
                    self.stats.total_listings += 1
                    self.log.info(f"  ✅ Inserted {self.stats.total_listings} listings into database")

        time.sleep(self.config.delay_between_requests)

    def run(self) -> None:
        """Main execution method"""
        self.log.info("🚀 Starting Otomoto.pl scraping...")
        start_date = datetime.now().strftime("%d-%m-%Y")
        start_time = time.time()

        try:
            # Get first page to determine total count
            self.log.info("📊 Fetching first page to determine total count...")
            payload = self.fetch_listing_page(1)

            if not payload or 'data' not in payload:
                self.log.error("❌ Failed to get initial page")
                return

            total_count = payload['data']['advertSearch']['totalCount']
            total_pages = int(total_count / 32) + 1

            self.log.info(f"📈 Found {total_count} total listings across {total_pages} pages")

            # Process all pages
            for page in range(1, total_pages + 1):
                try:
                    self.log.info(f"\n{'=' * 60}")
                    self.log.info(f"📖 Processing page {page}/{total_pages}")

                    payload = self.fetch_listing_page(page)
                    if not payload or 'data' not in payload:
                        self.log.warning(f"⚠️ Failed to get page {page}")
                        continue

                    edges = payload['data']['advertSearch']['edges']
                    if not edges:
                        self.log.info(f"⚠️ No listings found on page {page}")
                        break

                    # Parse listings
                    listings = []
                    for info in edges:
                        node = info['node']
                        listing = self.parse_listing(node)
                        if listing:
                            listings.append(listing)

                    self.stats.pages_processed += 1
                    self.log.info(f"  📋 Parsed {len(listings)} listings from page {page}")

                    # Process listings batch
                    self.process_listings(listings)
                    if self.config.consective_no_data_page_count == 3:
                        self.log.info(f"📄 Three pages have no New data Stopping script!")
                        break
                except KeyboardInterrupt:
                    self.log.error("\n\n⚠️ Scraping interrupted by user")
                    break
                except Exception as e:
                    self.log.error(f"❌ Error processing page {page}: {str(e)[:200]}")
                    continue

        except Exception as e:
            self.log.error(f"❌ Fatal error in scraper: {str(e)[:200]}")

        finally:
            # Mark unavailable listings
            elapsed_time = time.time() - start_time
            # self.db_obj.mark_unavailable_before(start_date, 'otomoto')

            # Print final statistics
            self.log.info(f"\n{'=' * 60}")
            self.log.info("📊 SCRAPING COMPLETED")
            self.log.info(f"{'=' * 60}")
            self.log.info(f"✅ Total listings collected: {self.stats.total_listings}")
            self.log.info(f"📋 Details fetched: {self.stats.details_fetched}")
            self.log.info(f"⭐ Duplicates skipped: {self.stats.duplicates_skipped}")
            self.log.info(f"🔄 Pages processed: {self.stats.pages_processed}")
            self.log.info(f"🌐 Total requests: {self.stats.total_requests}")
            self.log.info(f"❌ Failed requests: {self.stats.failed_requests}")
            self.log.info(f"⏱️ Time elapsed: {elapsed_time:.2f} seconds")
            if elapsed_time > 0:
                self.log.info(f"⚡ Average: {self.stats.total_listings / elapsed_time:.2f} listings/sec")
            self.log.info(f"{'=' * 60}")


def main():
    """Entry point for the scraper"""
    # Create custom configuration if needed
    config = ScraperConfig(
        max_retries=5,
        timeout=30,
        delay_between_requests=0.1,
        batch_size=5
    )

    # Initialize and run scraper
    scraper = OtomotoScraper(config)
    scraper.run()
