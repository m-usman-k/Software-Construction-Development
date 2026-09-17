import csv
import json
import logging
import os
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from curl_cffi import requests

# Configuration Constants
SCRAPE_URLS = [
    "https://www.zameen.com/Homes/Islamabad-3-1.html",
    "https://www.zameen.com/Flats_Apartments/Islamabad-3-1.html",
    "https://www.zameen.com/Farm_Houses/Islamabad-3-1.html"
]

CSV_FILENAME = os.path.join("data", "property_dataset_refactored.csv")
MAX_ITEMS_TO_COLLECT = 10000
REQUEST_INTERVAL = 1
MAX_THREADS = 15

HTTP_TIMEOUT = 20
MAX_RETRIES = 3
BLOCKED_STATUS_CODES = (403, 429)

CSV_HEADERS = [
    "Title", "Price", "Area", "City", "Number of bedrooms", "Number of bathrooms",
    "Location", "Property type", "Built in year", "Parking space",
    "Servant Quarters", "Store rooms", "Kitchens", "Drawing Room",
    "Dining Room", "Study Room", "Prayer Room", "Powder Room",
    "Lounge or Sitting Room", "Laundry Room"
]

# Configure structured logging to replace ad-hoc print statements
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(threadNameName)s: %(message)s', # threadName helps trace concurrency
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class ZameenParser:
    """Handles pure extraction of data from HTML or JSON payloads.
    Separating parsing from fetching makes it easily testable with static files.
    """

    @staticmethod
    def extract_hydrated_state(soup: BeautifulSoup) -> dict:
        """Extracts the initial state injected by the server into the HTML."""
        for script_tag in soup.find_all("script"):
            js_code = script_tag.string or ""
            if "window.state =" in js_code:
                start_index = js_code.find("{")
                if start_index != -1:
                    try:
                        state_obj, _ = json.JSONDecoder().raw_decode(js_code[start_index:])
                        return state_obj
                    except json.JSONDecodeError:
                        continue
        return {}

    @staticmethod
    def parse_property_slugs(search_soup: BeautifulSoup) -> list:
        state = ZameenParser.extract_hydrated_state(search_soup)
        hits = state.get("algolia", {}).get("content", {}).get("hits", [])
        return [str(hit.get("slug")) for hit in hits if hit.get("slug")]

    @staticmethod
    def parse_next_page_url(search_soup: BeautifulSoup) -> str:
        rel_next = search_soup.find("link", rel="next")
        return str(rel_next["href"]).strip() if rel_next and rel_next.get("href") else ""

    @staticmethod
    def parse_property_details(detail_soup: BeautifulSoup) -> dict:
        """Coordinates the extraction of all required features from a single property page."""
        state = ZameenParser.extract_hydrated_state(detail_soup)
        data = state.get("property", {}).get("data", {})
        
        if not data:
            raise ValueError("No property payload found in the provided HTML state.")
            
        return {
            **ZameenParser._extract_basic_info(data),
            **ZameenParser._extract_location(data),
            **ZameenParser._extract_amenities(data)
        }

    @staticmethod
    def _extract_basic_info(data: dict) -> dict:
        """Pulls top-level details like title, price, and dynamically finds the most specific category."""
        categories = data.get("category", [])
        type_name = data.get("type", "")
        if categories:
            deepest_node = max(categories, key=lambda x: x.get("level", 0))
            type_name = deepest_node.get("nameSingular", deepest_node.get("name", ""))
            
        return {
            "Title": data.get("title", ""),
            "Price": data.get("price"),
            "Area": data.get("area"),
            "Number of bedrooms": data.get("rooms"),
            "Number of bathrooms": data.get("baths"),
            "Property type": type_name.capitalize()
        }

    @staticmethod
    def _extract_location(data: dict) -> dict:
        """Parses the hierarchical location data to find the broad city and specific neighborhood label."""
        locations_hierarchy = data.get("locations", [])
        city_name = "Islamabad" # Default
        for loc in locations_hierarchy:
            if loc.get("level") == 2:
                city_name = loc.get("name", "")
                break

        hierarchy_label = data.get("labels", {}).get("locationHierarchy", "")
        if not hierarchy_label and locations_hierarchy:
            hierarchy_label = ", ".join([loc.get("name", "") for loc in locations_hierarchy])

        return {
            "City": city_name,
            "Location": hierarchy_label
        }

    @staticmethod
    def _extract_amenities(data: dict) -> dict:
        """Flattens the nested amenity groups into a simple dictionary for easy lookup."""
        active_amenities = {}
        for group in data.get("amenities", []):
            for item in group.get("amenities", []):
                slug = item.get("slug")
                val = str(item.get("value", "")).strip()
                active_amenities[slug] = val if val else "Yes"

        return {
            "Built in year": active_amenities.get("built-in-year", ""),
            "Parking space": active_amenities.get("parking-spaces", "No"),
            "Servant Quarters": active_amenities.get("servant-quarters", "No"),
            "Store rooms": active_amenities.get("store-rooms", "No"),
            "Kitchens": active_amenities.get("kitchens", "No"),
            "Drawing Room": active_amenities.get("drawing-room", "No"),
            "Dining Room": active_amenities.get("dining-room", "No"),
            "Study Room": active_amenities.get("study-room", "No"),
            "Prayer Room": active_amenities.get("prayer-room", "No"),
            "Powder Room": active_amenities.get("powder-room", "No"),
            "Lounge or Sitting Room": active_amenities.get("lounge-or-sitting-room", "No"),
            "Laundry Room": active_amenities.get("laundry-room", "No")
        }


class ZameenScraper:
    """Manages the lifecycle of scraping: threading, network requests, state locks, and saving data."""
    
    def __init__(self, target_count: int = MAX_ITEMS_TO_COLLECT, max_threads: int = MAX_THREADS):
        self.target_count = target_count
        self.max_threads = max_threads
        self.collected_slugs = []
        self.slug_lock = threading.Lock()
        
    def run(self):
        logger.info(f"Starting Zameen Scraper (Target: {self.target_count} | Threads: {self.max_threads})")
        self._discover_slugs()
        
        if not self.collected_slugs:
            logger.error("No slugs collected. Exiting.")
            return
            
        self._crawl_properties_and_save()
        logger.info("Scraping Finished Successfully!")

    def _get_session(self) -> requests.Session:
        # We must impersonate a browser to bypass advanced bot protection/WAFs (like Cloudflare)
        return requests.Session(impersonate="chrome")

    def _fetch_html_soup(self, session: requests.Session, url: str) -> BeautifulSoup:
        """Fetches a URL with exponential backoff to handle rate limits and transient errors gracefully."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                res = session.get(url, timeout=HTTP_TIMEOUT)
                res.raise_for_status()
                
                if res.status_code in BLOCKED_STATUS_CODES or ("captcha" in res.text.lower() and "cloudflare" in res.text.lower()):
                    raise RuntimeError("Request blocked by security shield.")
                    
                return BeautifulSoup(res.text, "html.parser")
            except Exception as e:
                if attempt == MAX_RETRIES:
                    raise e
                # Random jitter is added to the backoff to prevent synchronized waves of retry requests across threads
                time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

    def _process_search_page(self, session: requests.Session, url: str, tag: str, page: int) -> str:
        """Processes a single search page, abstracting the inner loop of the crawler."""
        soup = self._fetch_html_soup(session, url)
        slugs = ZameenParser.parse_property_slugs(soup)
        
        if not slugs:
            return ""
            
        new_count = 0
        with self.slug_lock:
            for s in slugs:
                if s not in self.collected_slugs:
                    self.collected_slugs.append(s)
                    new_count += 1
                    if len(self.collected_slugs) >= self.target_count:
                        break
        
        # We use a custom threadName adapter in logging so the tag/page can be easily traced
        logger.info(f"[{tag}] Page {page}: Added {new_count} slugs. Total: {len(self.collected_slugs)}/{self.target_count}")
        return ZameenParser.parse_next_page_url(soup)

    def _scrape_category(self, start_url: str):
        session = self._get_session()
        current_url = start_url
        page = 1
        tag = start_url.rstrip("/").split("/")[-1].replace(".html", "")

        while current_url:
            with self.slug_lock:
                if len(self.collected_slugs) >= self.target_count:
                    break

            try:
                current_url = self._process_search_page(session, current_url, tag, page)
                page += 1
                # Short sleep between sequential pages to avoid triggering rate limits for the same category
                time.sleep(random.uniform(0.5, REQUEST_INTERVAL + 1.0))
            except Exception as e:
                logger.error(f"[{tag}] Failed on Page {page}: {e}")
                break

    def _discover_slugs(self):
        logger.info("Phase 1: Discovering Property Slugs")
        with ThreadPoolExecutor(max_workers=min(len(SCRAPE_URLS), self.max_threads)) as executor:
            futures = [executor.submit(self._scrape_category, url) for url in SCRAPE_URLS]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Category thread crashed: {e}")

    def _fetch_property(self, slug: str) -> dict:
        session = self._get_session()
        # Random delay to spread network load evenly across our concurrent threads
        time.sleep(random.uniform(0.5, REQUEST_INTERVAL))
        
        url = f"https://www.zameen.com/Property/{slug}.html"
        soup = self._fetch_html_soup(session, url)
        return ZameenParser.parse_property_details(soup)

    def _crawl_properties_and_save(self):
        logger.info("Phase 2: Crawling Property Details")
        
        os.makedirs(os.path.dirname(CSV_FILENAME), exist_ok=True)
        csv_lock = threading.Lock()
        success_count = 0
        
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
            writer.writeheader()
            
            with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
                futures = {executor.submit(self._fetch_property, slug): slug for slug in self.collected_slugs}
                
                for idx, future in enumerate(as_completed(futures), 1):
                    slug = futures[future]
                    try:
                        record = future.result()
                        with csv_lock:
                            writer.writerow(record)
                            csv_file.flush()
                            success_count += 1
                        logger.info(f"[{idx}/{len(self.collected_slugs)}] OK - Price: {record['Price']} | Beds: {record['Number of bedrooms']}")
                    except Exception as e:
                        logger.error(f"[{idx}/{len(self.collected_slugs)}] ERROR - Skip {slug[:25]}... ({e})")
                        
        logger.info(f"Saved {success_count} records to {os.path.abspath(CSV_FILENAME)}")


if __name__ == "__main__":
    scraper = ZameenScraper()
    scraper.run()
