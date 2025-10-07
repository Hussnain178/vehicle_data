import time
from typing import List, Dict, Any, Optional
import json
import requests
from bs4 import BeautifulSoup
from scrapy import Selector
from dataclasses import dataclass
from datetime import datetime
from utils.key_mapping import convert_vehicle_data
from utils.filters import *
import threading
from proxies.webshare import WEBSHARE
from database.db import VehicleDatabase


@dataclass
class ScraperConfig:
    """Configuration for the hourly scraper"""
    max_pages: int = 200
    max_retries: int = 3
    delay_between_requests: float = 1.0


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


class AutoScout24HourlyScraper:
    """Hourly scraper for AutoScout24 - fetches latest listings sorted by age"""

    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with configuration"""
        self.config = config or ScraperConfig()
        self.stats = ScraperStats()
        self.webshare_obj = WEBSHARE()
        self.unique_features = autoscout24_features
        self.db_obj = VehicleDatabase()

    def _make_request(self, url: str, params: Optional[Dict[str, Any]] = None,
                      is_pagination: bool = False) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling"""
        for attempt in range(self.config.max_retries):
            try:
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

    def get_pagination_response(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get search results with parameters"""
        response = self._make_request(url, params=params, is_pagination=True)

        if response:
            try:
                return response.json()
            except json.JSONDecodeError:
                print("❌ Failed to parse JSON response")
                return None
        return None

    def get_detail_response(self, url: str) -> Optional[Dict[str, Any]]:
        """Get product detail page"""
        response = self._make_request(url, is_pagination=False)

        if response:
            try:
                json_data = Selector(text=response.text).css('#__NEXT_DATA__::text').get('{}')
                return json.loads(json_data)
            except Exception as e:
                print(f"❌ Error parsing detail page: {str(e)[:100]}")
                return None
        return None

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
            print(f"❌ Error parsing listing: {e}")
            return {}

    def parse_detail_listing(self, basic_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse detailed listing data from product page"""
        listing_id = basic_data.get('id')

        # Check for duplicate
        if self.db_obj.check_id_exists(listing_id, 'autoscout24'):
            print(f"⏭️  Skipping duplicate ID: {listing_id}")
            self.stats.duplicates_skipped += 1
            return None

        try:
            # Construct full URL
            url = 'https://www.autoscout24.de' + basic_data.get('url', '')
            basic_data['url'] = url

            product_response = self.get_detail_response(url)

            if not product_response:
                print(f"⚠️  Failed to get details for: {url}")
                return basic_data

            # Extract description
            try:
                description = product_response['props']['pageProps']['listingDetails']['description']
                if description:
                    soup = BeautifulSoup(description, "html.parser")
                    clean_text = soup.get_text(separator="\n").strip()
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
            print(f"✅ Parsed: {basic_data.get('url', 'Unknown')[:50]} - €{basic_data.get('price', 'N/A')}")
            return basic_data

        except Exception as e:
            print(f"❌ Error parsing details for {basic_data.get('url', 'Unknown')}: {str(e)[:100]}")
            return basic_data

    def process_listings(self, listings: List[Dict[str, Any]]):
        """Process multiple listings concurrently"""
        threads = []
        lock = threading.Lock()

        def process_single(listing):
            try:
                basic_data = self.parse_listing(listing)
                if not basic_data:
                    return

                detailed_data = self.parse_detail_listing(basic_data)
                if not detailed_data:
                    return

                # Build title
                detailed_data[
                    'title'] = f"{detailed_data.get('vehicle_make', '')} {detailed_data.get('vehicle_model', '')} {detailed_data.get('vehicle_modelVersionInput', '')}".strip()

                final_data = convert_vehicle_data(detailed_data, 'autoscout24')

                # Thread-safe append and counter increment
                with lock:
                    self.db_obj.insert_vehicle(final_data)
                    self.stats.total_listings += 1
                    self.stats.list_process_per_page += 1

            except Exception as e:
                print(f"❌ Error processing listing: {e}")

        # Create and start threads
        for listing in listings:
            t = threading.Thread(target=process_single, args=(listing,))
            t.start()
            threads.append(t)

        # Wait for all threads to complete
        for t in threads:
            t.join()

    def run(self):
        """Main execution method - fetch latest listings sorted by age"""
        print("🚀 Starting AutoScout24 hourly scraping...")
        print(f"⚙️  Config: Max {self.config.max_pages} pages, sorted by age")

        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"🕐 Run timestamp: {timestamp}")

        url = "https://www.autoscout24.de/_next/data/as24-search-funnel_main-20250924171425/lst.json"
        page_number = 1
        total_fetched = 0

        try:
            while page_number < self.config.max_pages:
                print(f"\n{'=' * 60}")
                print(f"📖 Processing page {page_number}")

                # Build search parameters
                params = {
                    "atype": "C",
                    "cy": "D",
                    "damaged_listing": "exclude",
                    "desc": "1",
                    "ocs_listing": "include",
                    "powertype": "kw",
                    "search_id": "fgm1i1ycu0",
                    "sort": "age",  # Sort by age (newest first)
                    "source": "listpage_pagination",
                    "ustate": "N,U",
                    "page": str(page_number)
                }

                response = self.get_pagination_response(url, params)

                if not response or 'pageProps' not in response:
                    print(f"❌ Failed to get response for page {page_number}")
                    break

                page_props = response['pageProps']
                num_results = page_props.get('numberOfResults', 0)
                num_pages = page_props.get('numberOfPages', 0)

                if page_number == 1:
                    print(f"📈 Total results available: {num_results}")
                    print(f"📄 Total pages available: {num_pages}")

                # Extract listings
                listings = page_props.get('listings', [])

                if not listings:
                    print("⚠️  No listings found on this page")
                    break

                print(f"🔄 Processing {len(listings)} listings from this page")

                # Process listings
                self.process_listings(listings)
                self.stats.pages_processed += 1

                print(f"✅ Parsed {self.stats.list_process_per_page} listings (Total: {self.stats.total_listings})")
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

                # Add delay between pages
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
        max_pages=200,
        max_retries=3,
        delay_between_requests=1.0
    )

    # Initialize and run scraper
    scraper = AutoScout24HourlyScraper(config)
    scraper.run()
