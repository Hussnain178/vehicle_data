import time
from typing import List, Dict, Any, Optional
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
    """Configuration for the daily scraper"""
    max_retries: int = 5
    delay_between_requests: float = 1.0
    listings_per_page: int = 90
    max_pages: int = 100  # Maximum pages to scrape (adjust based on needs)
    batch_pages: int = 3  # Number of pages to fetch before processing


@dataclass
class ScraperStats:
    """Track scraper statistics"""
    total_listings: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    pages_processed: int = 0
    duplicates_skipped: int = 0
    new_listings: int = 0


class MarktplaatsDailyScraper:
    """Daily scraper for Marktplaats - fetches newest listings using SORT_INDEX"""

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.log = LoggerSetup("marktplaats_daily.log").get_logger()
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
            "sortBy": "SORT_INDEX",
            "sortOrder": "DECREASING",
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

            # Extract car details
            try:
                for key, value in listing['listing']['carDetails'].items():
                    if value is not None:
                        data[key] = value
            except Exception as e:
                self.log.error(f"❌ Error extracting car details: {e}")
            for key in ['isImported', 'isTurbo']:
                if key in data and data[key].lower() in ['ja', 'ja.']:
                    data[key] = True
                else:
                    data[key] = False
            data.update(features)

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

    def process_listings(self, listings: List[dict]) -> tuple[int, int]:
        """Process multiple listings with threading. Returns (new_count, existing_count)."""
        if not listings:
            return 0, 0

        self.log.info(f"  🔄 Processing {len(listings)} listings with {self.thread_limit} threads")

        inserted_count = 0
        existing_count = 0

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
                    existing_count += 1
                    continue

                url = 'https://www.marktplaats.nl' + vip_url
                future = executor.submit(self.parse_and_insert_listing, listing, url, listing_id)
                futures.append(future)

            for future in as_completed(futures):
                try:
                    success = future.result()
                    if success:
                        inserted_count += 1
                        self.stats.new_listings += 1
                except Exception as e:
                    self.log.error(f"❌ Error processing listing: {e}")

        if inserted_count > 0:
            self.stats.total_listings += inserted_count
            self.log.info(f"  ✅ Inserted {inserted_count} new listings (Total: {self.stats.total_listings})")

        if existing_count > 0:
            self.log.info(f"  ♻️ Found {existing_count} existing listings (already in database)")

        return inserted_count, existing_count  # Return both counts

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

    def scrape_newest_listings(self) -> None:
        """Scrape newest listings until we encounter 3 consecutive pages with no new data"""
        self.log.info(f"\n{'=' * 60}")
        self.log.info(f"🆕 Fetching newest listings (SORT_INDEX DECREASING)")

        params = self.base_params.copy()

        # First, check how many results exist
        first_response = self.get_search_response(params)
        if not first_response:
            self.log.warning("❌ Failed to get initial response")
            return

        total_results = first_response.get("totalResultCount", 0)
        self.log.info(f"📈 Total listings available: {total_results}")

        # Calculate actual max pages based on total results and configured max
        calculated_max_pages = min(
            self.config.max_pages,
            (total_results + self.config.listings_per_page - 1) // self.config.listings_per_page
        )

        self.log.info(f"🔄 Will scrape up to {calculated_max_pages} pages")
        self.log.info(f"🛑 Stop criteria: 3 consecutive pages with no new data")

        # Early stopping criteria - track per page
        consecutive_pages_no_new = 0
        max_consecutive_no_new = 3  # Stop if 3 consecutive pages have 0 new listings

        page = 1
        while page <= calculated_max_pages:
            self.log.info(f"\n  📄 Processing page {page}/{calculated_max_pages}")

            # Fetch single page
            page_params = params.copy()
            page_params["offset"] = str((page - 1) * self.config.listings_per_page)

            data = self.get_search_response(page_params)

            if not data:
                self.log.warning(f"  ⚠️ Failed to get page {page}")
                page += 1
                continue

            listings = data.get('listings', [])
            self.stats.pages_processed += 1
            self.log.info(f"  📖 Fetched {len(listings)} listings from page {page}")

            if not listings:
                self.log.warning(f"  ⚠️ No listings found on page {page}")
                break

            # Remove duplicates within page based on itemId
            unique_listings = {}
            for listing in listings:
                listing_id = listing.get('itemId')
                if listing_id and listing_id not in unique_listings:
                    unique_listings[listing_id] = listing

            duplicates_in_page = len(listings) - len(unique_listings)
            if duplicates_in_page > 0:
                self.log.info(f"  🔍 Removed {duplicates_in_page} duplicate listings from page")

            # Process all unique listings from the page
            unique_listings_list = list(unique_listings.values())
            self.log.info(f"  ⚙️ Processing {len(unique_listings_list)} unique listings")

            new_count, existing_count = self.process_listings(unique_listings_list)

            # Check if this page had any new listings
            if new_count == 0:
                consecutive_pages_no_new += 1
                self.log.info(
                    f"  ⚠️ No new listings on this page ({consecutive_pages_no_new}/{max_consecutive_no_new})")

                if consecutive_pages_no_new >= max_consecutive_no_new:
                    self.log.info(f"  🛑 Stopping: {max_consecutive_no_new} consecutive pages with no new data")
                    break
            else:
                # Reset counter if we found new listings
                consecutive_pages_no_new = 0
                self.log.info(f"  ✅ Found {new_count} new listings on this page")

            page += 1

        self.log.info(f"\n  ✅ Completed daily scrape (New listings: {self.stats.new_listings})")

    def run(self):
        """Main execution method"""
        self.log.info("🚀 Starting Marktplaats Daily Scraping...")
        self.log.info(f"⚙️ Config: {self.config.listings_per_page} listings/page, max {self.config.max_pages} pages")
        self.log.info(f"🛑 Will stop after 3 consecutive pages with no new data")
        start_date = datetime.now().strftime("%d-%m-%Y")
        start_time = time.time()

        try:
            self.scrape_newest_listings()

        except KeyboardInterrupt:
            self.log.error("\n\n⚠️ Scraping interrupted by user")
        except Exception as e:
            self.log.error(f"❌ Error during scraping: {str(e)[:200]}")

        elapsed_time = time.time() - start_time

        # Mark unavailable listings (optional for daily runs)
        # self.db_obj.mark_unavailable_before(start_date, 'marktplaats')

        # Print final statistics
        self.log.info(f"\n{'=' * 60}")
        self.log.info("📊 DAILY SCRAPING COMPLETED")
        self.log.info(f"{'=' * 60}")
        self.log.info(f"🆕 New listings collected: {self.stats.new_listings}")
        self.log.info(f"✅ Total listings processed: {self.stats.total_listings}")
        self.log.info(f"⭐️ Duplicates skipped: {self.stats.duplicates_skipped}")
        self.log.info(f"🔄 Pages processed: {self.stats.pages_processed}")
        self.log.info(f"🌐 Total requests: {self.stats.total_requests}")
        self.log.info(f"❌ Failed requests: {self.stats.failed_requests}")
        self.log.info(f"⏱️ Time elapsed: {elapsed_time:.2f} seconds")
        if elapsed_time > 0 and self.stats.new_listings > 0:
            self.log.info(f"⚡ Average: {self.stats.new_listings / elapsed_time:.2f} new listings/sec")
        self.log.info(f"{'=' * 60}")


def main():
    """Entry point for the daily scraper"""
    # Create custom configuration if needed
    config = ScraperConfig(
        max_retries=5,
        delay_between_requests=.1,
        listings_per_page=90,
        max_pages=100  # Maximum pages to check (will stop early if 3 consecutive pages have no new data)
    )

    # Initialize and run scraper
    scraper = MarktplaatsDailyScraper(config)
    scraper.run()


