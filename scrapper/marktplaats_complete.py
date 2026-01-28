import time
from typing import List, Tuple, Dict, Any, Optional
import json
import requests
import scrapy
from bs4 import BeautifulSoup
from dataclasses import dataclass
from utils.key_mapping import convert_vehicle_data
from utils.filters import marktplaats_features
from datetime import datetime
from proxies.webshare import WEBSHARE
from database.db import VehicleDatabase
from logger.logger_setup import LoggerSetup
from configuration.config import Config
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading


@dataclass
class ScraperConfig:
    """Configuration for the scraper"""
    max_results_per_range: int = 1890
    max_retries: int = 5
    delay_between_requests: float = 1.0
    price_start: int = 0
    price_end: int = 10_000_000
    initial_chunk_size: int = 50_000
    batch_pages: int = 3  # Number of pages to fetch before processing
    listings_per_page: int = 90


@dataclass
class ScraperStats:
    """Track scraper statistics"""
    total_listings: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    ranges_processed: int = 0
    pages_processed: int = 0
    duplicates_skipped: int = 0


class MarktplaatsScraper:
    """Robust scraper for Marktplaats with dynamic range splitting"""

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.log = LoggerSetup("marktplaats_complete.log").get_logger()
        self.webshare_obj = WEBSHARE()
        self.unique_features = marktplaats_features
        self.db_obj = VehicleDatabase(logger=self.log)
        self.thread_limit = Config.MARKTPLAATS_THREAD_COUNT if hasattr(Config, 'MARKTPLAATS_THREAD_COUNT') else 25
        self.db_lock = threading.Lock()  # Lock for thread-safe database operations

        self.base_url = "https://www.marktplaats.nl/lrp/api/search"
        self.base_params = {
            "attributesById[]": "10882",
            "attributesByKey[]": "offeredSince:Altijd",
            "l1CategoryId": "91",
            "limit": "90",
            "offset": "0",
            "sortBy": "PRICE",
            "sortOrder": "INCREASING",
            "viewOptions": "list-view"
        }

        self.headers = {
            "accept": "*/*",
            "accept-language": "en-PK,en;q=0.9,ur-PK;q=0.8,ur;q=0.7,en-GB;q=0.6,en-US;q=0.5",
            "priority": "u=1, i",
            "referer": "https://www.marktplaats.nl/l/auto-s/",
            "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            )
        }

    def _make_request(self, url: str, params: Optional[Dict[str, Any]] = None,
                      is_detail_page: bool = False) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self.headers,
                    proxies=self.webshare_obj.get_proxy(),
                    timeout=30
                )
                self.stats.total_requests += 1

                if response.status_code == 200:
                    return response
                else:
                    self.log.warning(
                        f"⚠️ HTTP {response.status_code} on attempt {attempt + 1}/{self.config.max_retries}")

            except requests.exceptions.Timeout:
                self.log.error(f"⏱️ Timeout on attempt {attempt + 1}/{self.config.max_retries}")
            except requests.exceptions.ConnectionError:
                self.log.error(f"🔌 Connection error on attempt {attempt + 1}/{self.config.max_retries}")
            except Exception as e:
                self.log.error(f"❌ Error on attempt {attempt + 1}/{self.config.max_retries}: {str(e)[:100]}")

            if attempt < self.config.max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff

        self.stats.failed_requests += 1
        return None

    def get_search_response(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get search results with parameters"""
        response = self._make_request(self.base_url, params=params)

        if response:
            try:
                return response.json()
            except json.JSONDecodeError:
                self.log.error("❌ Failed to parse JSON response")
                return None
        return None

    def get_detail_response(self, url: str) -> Optional[scrapy.Selector]:
        """Get product detail page"""
        response = self._make_request(url, is_detail_page=True)

        if response:
            return scrapy.Selector(text=response.text)
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
            self.log.info(f"⚠️ Cannot split range further: ({start}, {end})")
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

    def parse_and_insert_listing(self, listing_data: dict, url: str, listing_id: str) -> bool:
        """Parse individual listing and insert into database"""
        try:
            sel = self.get_detail_response(url)
            if not sel:
                self.log.warning(f"⚠️ No data found for {url}")
                return False

            script_text = [s for s in sel.css('script::text').getall() if '__HEADER_CONFIG__' in s]
            if not script_text:
                self.log.warning(f"⚠️ No config script found for {url}")
                return False

            data = {}
            listing = json.loads(script_text[0].split('window.__CONFIG__ = ')[-1].removesuffix(';'))

            data['title'] = listing['listing']['title']
            data['id'] = listing['listing']['itemId']
            data['url'] = url
            data['images'] = json.dumps(
                ['https:' + l.replace('#', '86') for l in listing['listing']['gallery']['imageUrls']])
            data['seller_name'] = listing['listing']['seller']['name']

            price_cents = listing['listing']['priceInfo']['priceCents']
            data['priceCents'] = str(price_cents)
            data['price'] = str(price_cents / 100)

            # Extract description
            description_html = sel.css('.Description-description').get()
            if description_html:
                soup = BeautifulSoup(description_html, "html.parser")
                data['description'] = soup.get_text().strip()
            else:
                data['description'] = ""

            # Extract features
            features = {}

            for attribute in listing['listing']['carAttributes']['groupedWithIcons']:
                try:
                    if attribute['key'] == 'Options':
                        for attr in attribute['attributes']:
                            property_features = attr['values']
                            for feature in self.unique_features:
                                features[feature] = feature in property_features
                    else:
                        for attr in attribute['attributes']:
                            try:
                                data[attr['key']] = attr['value']
                            except:
                                data[attr['key']] = ' '.join(attr['values'])
                except Exception as e:
                    self.log.error(f"❌ Error extracting attributes: {e}")
            if not features:
                for feature in self.unique_features:
                    features[feature] = False
            # Extract car details
            try:
                for key, value in listing['listing']['carDetails'].items():
                    if value is not None:
                        data[key] = value
            except Exception as e:
                self.log.error(f"❌ Error extracting car details: {e}")

            data.update(features)
            for key in ['isImported', 'isTurbo']:
                if key in data and data[key].lower() in ['ja', 'ja.']:
                    data[key] = True
                else:
                    data[key] = False
            # Convert to standardized format
            final_data = convert_vehicle_data(data, 'marktplaats')

            # Insert into database with thread lock
            with self.db_lock:
                self.db_obj.insert_vehicle(final_data)

            self.log.info(f"  ✓ Inserted: {url}")
            return True

        except Exception as e:
            self.log.error(f"❌ Error parsing listing {url}: {str(e)[:200]}")
            return False

    def process_listings(self, listings: List[dict]) -> None:
        """Process multiple listings with threading"""
        if not listings:
            return

        self.log.info(f"  🔄 Processing {len(listings)} listings with {self.thread_limit} threads")

        inserted_count = 0
        with ThreadPoolExecutor(max_workers=self.thread_limit) as executor:
            futures = []
            for listing in listings:
                title = listing.get('title')
                vip_url = listing.get('vipUrl')
                listing_id = listing.get('itemId')

                if not vip_url or title == '-' or not listing_id:
                    self.log.warning(f"⚠️ Invalid listing data, skipping")
                    continue

                # Check if listing already exists in database
                if self.db_obj.check_id_exists(listing_id, 'marktplaats'):
                    self.stats.duplicates_skipped += 1
                    continue

                url = 'https://www.marktplaats.nl' + vip_url
                future = executor.submit(self.parse_and_insert_listing, listing, url, listing_id)
                futures.append(future)

            for future in as_completed(futures):
                try:
                    success = future.result()
                    if success:
                        inserted_count += 1
                except Exception as e:
                    self.log.error(f"❌ Error processing listing: {e}")

        if inserted_count > 0:
            self.stats.total_listings += inserted_count
            self.log.info(f"  ✅ Inserted {inserted_count} listings (Total: {self.stats.total_listings})")

    def fetch_single_page(self, params: Dict[str, Any], page: int) -> List[dict]:
        """Fetch a single page of results"""
        try:
            page_params = params.copy()
            page_params["offset"] = str((page - 1) * self.config.listings_per_page)

            data = self.get_search_response(page_params)

            if not data:
                self.log.warning(f"  ⚠️ Failed to get page {page}")
                return []

            listings = data.get('listings', [])
            self.stats.pages_processed += 1
            self.log.info(f"  📖 Page {page}: fetched {len(listings)} listings")

            return listings
        except Exception as e:
            self.log.error(f"❌ Error fetching page {page}: {e}")
            return []

    def process_price_range(self, price_range: Tuple[int, int]) -> None:
        """Process a single price range with dynamic chunking"""
        self.log.info(f"\n{'=' * 60}")
        self.log.info(f"💰 Processing price range: €{price_range[0] / 100:.2f} - €{price_range[1] / 100:.2f}")

        start, end = price_range
        start_val = 'null' if start == 0 else start

        # Build search parameters
        params = self.base_params.copy()
        params["attributeRanges[]"] = f"PriceCents:{start_val}:{end}"

        # Get first page to check total results
        response = self.get_search_response(params)

        if not response:
            self.log.warning(f"❌ Failed to get response for range {price_range}")
            return

        num_results = response.get("totalResultCount", 0)
        self.log.info(f"📈 Found {num_results} results")

        if num_results == 0:
            self.log.info("⭐️ No results, skipping range")
            return

        # Handle range splitting if needed
        if num_results > self.config.max_results_per_range:
            self.log.info(f"⚠️ Too many results ({num_results}), splitting range...")
            sub_ranges = self.split_range_dynamically(price_range, num_results)

            for sub_range in sub_ranges:
                self.process_price_range(sub_range)
            return

        # Calculate number of pages
        num_pages = (num_results + self.config.listings_per_page - 1) // self.config.listings_per_page
        self.log.info(f"🔄 Processing {num_pages} page(s) in batches of {self.config.batch_pages}")

        if num_pages:
            # Process pages in batches
            for batch_start in range(1, num_pages + 1, self.config.batch_pages):
                batch_end = min(batch_start + self.config.batch_pages, num_pages + 1)
                batch_pages = range(batch_start, batch_end)

                self.log.info(f"  📦 Batch: pages {batch_start} to {batch_end - 1}")

                # Fetch all pages in the batch concurrently
                all_listings = []
                batch_size = batch_end - batch_start
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = []
                    for page in batch_pages:
                        future = executor.submit(self.fetch_single_page, params, page)
                        futures.append(future)

                    for future in as_completed(futures):
                        try:
                            listings = future.result()
                            all_listings.extend(listings)
                        except Exception as e:
                            self.log.error(f"❌ Error fetching page in batch: {e}")

                # Remove duplicates based on itemId
                unique_listings = {}
                for listing in all_listings:
                    listing_id = listing.get('itemId')
                    if listing_id and listing_id not in unique_listings:
                        unique_listings[listing_id] = listing

                duplicates_in_batch = len(all_listings) - len(unique_listings)
                if duplicates_in_batch > 0:
                    self.stats.duplicates_skipped += duplicates_in_batch
                    self.log.info(f"  🔍 Removed {duplicates_in_batch} duplicate listings from batch")

                # Process all unique listings from the batch
                unique_listings_list = list(unique_listings.values())
                self.log.info(f"  ⚙️ Processing {len(unique_listings_list)} unique listings from batch")
                self.process_listings(unique_listings_list)

        self.log.info(f"  ✅ Completed range {price_range} (Total Inserted: {self.stats.total_listings})")
        self.stats.ranges_processed += 1

    def run(self):
        """Main execution method"""
        self.log.info("🚀 Starting Marktplaats scraping...")
        self.log.info(f"⚙️ Config: €{self.config.price_start / 100:.2f}-€{self.config.price_end / 100:.2f}, "
                      f"chunk size: €{self.config.initial_chunk_size / 100:.2f}, batch pages: {self.config.batch_pages}")
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
                self.log.error("\n\n⚠️ Scraping interrupted by user")
                break
            except Exception as e:
                self.log.error(f"❌ Error processing range {price_range}: {str(e)[:200]}")
                continue

        elapsed_time = time.time() - start_time
        self.db_obj.mark_unavailable_before(start_date, 'marktplaats')

        # Print final statistics
        self.log.info(f"\n{'=' * 60}")
        self.log.info("📊 SCRAPING COMPLETED")
        self.log.info(f"{'=' * 60}")
        self.log.info(f"✅ Total listings collected: {self.stats.total_listings}")
        self.log.info(f"⭐️ Duplicates skipped: {self.stats.duplicates_skipped}")
        self.log.info(f"🔄 Pages processed: {self.stats.pages_processed}")
        self.log.info(f"📦 Ranges processed: {self.stats.ranges_processed}")
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
        price_start=0,
        price_end=10_000_000,
        initial_chunk_size=50_000,
        max_retries=5,
        delay_between_requests=.1,
        max_results_per_range=1890,
        batch_pages=3  # Process 3 pages at a time
    )

    # Initialize and run scraper
    scraper = MarktplaatsScraper(config)
    scraper.run()
