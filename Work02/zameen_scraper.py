import csv
import json
import os
import random
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from curl_cffi import requests

# Configuration
SCRAPE_URLS = [
    # Islamabad
    "https://www.zameen.com/Homes/Islamabad-3-1.html",
    "https://www.zameen.com/Flats_Apartments/Islamabad-3-1.html",
    "https://www.zameen.com/Farm_Houses/Islamabad-3-1.html"
]

CSV_FILENAME = os.path.join("data", "property_dataset.csv")
MAX_ITEMS_TO_COLLECT = 10000
REQUEST_INTERVAL = 1
MAX_THREADS = 15

# ANSI colour shortcuts for terminal output
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_GREEN = "\033[32m"
CLR_RED = "\033[31m"
CLR_CYAN = "\033[36m"
CLR_YELLOW = "\033[33m"

# Enable ANSI colours on Windows
if sys.platform == "win32":
    try:
        os.system("color")
    except Exception:
        pass

# Strip formatting if output is redirected to a file or piped
if not sys.stdout.isatty():
    CLR_RESET = CLR_BOLD = CLR_GREEN = CLR_RED = CLR_CYAN = CLR_YELLOW = ""

def find_hydrated_state(soup: BeautifulSoup) -> dict:
    """Locate and decode the JavaScript state payload."""
    for script_tag in soup.find_all("script"):
        js_code = script_tag.string or ""
        if "window.state =" in js_code:
            start_index = js_code.find("{")
            if start_index != -1:
                trimmed_js = js_code[start_index:]
                try:
                    state_obj, _ = json.JSONDecoder().raw_decode(trimmed_js)
                    return state_obj
                except Exception:
                    pass
    return {}

def download_page_soup(http_session: requests.Session, url: str) -> BeautifulSoup:
    """Download and parse page HTML with robust retry and backoff logic."""
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            res = http_session.get(url, timeout=20)
            res.raise_for_status()
            if res.status_code in (403, 429) or ("captcha" in res.text.lower() and "cloudflare" in res.text.lower()):
                raise RuntimeError("Request blocked by security shield.")
            return BeautifulSoup(res.text, "html.parser")
        except Exception as e:
            if attempt == max_retries:
                raise e
            # Exponential backoff with random delay to avoid synchronization waves
            sleep_time = (2 ** attempt) + random.uniform(0.5, 1.5)
            time.sleep(sleep_time)

def collect_property_slugs(search_soup: BeautifulSoup) -> list:
    """Collect listings from search hydration state."""
    state = find_hydrated_state(search_soup)
    property_hits = state.get("algolia", {}).get("content", {}).get("hits", [])
    
    slug_identifiers = []
    for hit in property_hits:
        slug = hit.get("slug")
        if slug:
            slug_identifiers.append(str(slug))
    return slug_identifiers

def parse_next_page(search_soup: BeautifulSoup) -> str:
    """Parse link pointing to the next search page."""
    rel_next = search_soup.find("link", rel="next")
    if rel_next and rel_next.get("href"):
        return str(rel_next["href"]).strip()
    return ""

def extract_property_features(http_session: requests.Session, slug: str) -> dict:
    """Extract required features from a listing page."""
    target_url = f"https://www.zameen.com/Property/{slug}.html"
    detail_soup = download_page_soup(http_session, target_url)
    state = find_hydrated_state(detail_soup)
    
    data = state.get("property", {}).get("data", {})
    if not data:
        raise ValueError(f"No property payload found for: {slug}")
        
    price_val = data.get("price")
    area_val = data.get("area")
    bedrooms_count = data.get("rooms")
    bathrooms_count = data.get("baths")
    
    # Get the most specific property type from the category hierarchy
    categories = data.get("category", [])
    type_name = ""
    if categories:
        deepest_node = max(categories, key=lambda x: x.get("level", 0))
        type_name = deepest_node.get("nameSingular", deepest_node.get("name", ""))
    if not type_name:
        type_name = data.get("type", "")

    # Extract city name and full location label from the location hierarchy
    locations_hierarchy = data.get("locations", [])
    city_name = "Islamabad"
    for loc in locations_hierarchy:
        if loc.get("level") == 2:
            city_name = loc.get("name", "")
            break

    hierarchy_label = data.get("labels", {}).get("locationHierarchy", "")
    if not hierarchy_label and locations_hierarchy:
        hierarchy_label = ", ".join([loc.get("name", "") for loc in locations_hierarchy])

    # Flatten all amenity groups into a single slug->value dict
    amenity_groups = data.get("amenities", [])
    active_amenities = {}
    for group in amenity_groups:
        items = group.get("amenities", [])
        for item in items:
            item_slug = item.get("slug")
            item_value = str(item.get("value", "")).strip()
            if not item_value:
                item_value = "Yes"
            active_amenities[item_slug] = item_value

    return {
        "Title": data.get("title", ""),
        "Price": price_val,
        "Area": area_val,
        "City": city_name,
        "Number of bedrooms": bedrooms_count,
        "Number of bathrooms": bathrooms_count,
        "Location": hierarchy_label,
        "Property type": type_name.capitalize(),
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

def crawl_listing(slug: str, index: int) -> tuple:
    """Thread worker that fetches and returns one listing's features."""
    # Random delay to spread network load across threads
    time.sleep(random.uniform(0.5, REQUEST_INTERVAL))
    session = requests.Session(impersonate="chrome")
    return extract_property_features(session, slug), index

def scrape_slugs_from_start_url(scrape_url: str, url_idx: int, collected_slugs: list, slug_lock: threading.Lock):
    """Crawl a search starting URL sequentially page by page and append unique slugs to the shared collected_slugs list."""
    session = requests.Session(impersonate="chrome")
    current_search_url = scrape_url
    page = 1
    
    # Extract short descriptive tag from url
    parts = scrape_url.rstrip("/").split("/")
    tag = parts[-1].replace(".html", "") if parts else "Search"
    
    while current_search_url:
        with slug_lock:
            if len(collected_slugs) >= MAX_ITEMS_TO_COLLECT:
                break

        print(f"  [Thread-{url_idx}] Indexing Page {page} for: {tag}...")
        try:
            search_soup = download_page_soup(session, current_search_url)
            slugs = collect_property_slugs(search_soup)
            
            if not slugs:
                break
                
            new_slugs_count = 0
            with slug_lock:
                for s in slugs:
                    if s not in collected_slugs:
                        collected_slugs.append(s)
                        new_slugs_count += 1
                        if len(collected_slugs) >= MAX_ITEMS_TO_COLLECT:
                            break
                            
            print(f"  [Thread-{url_idx}] Page {page} ({tag}): Added {new_slugs_count} new slugs. Total: {CLR_GREEN}{len(collected_slugs)}/{MAX_ITEMS_TO_COLLECT}{CLR_RESET}")
            
            with slug_lock:
                if len(collected_slugs) >= MAX_ITEMS_TO_COLLECT:
                    break
                    
            current_search_url = parse_next_page(search_soup)
            if not current_search_url:
                break

            page += 1
            # Short sleep between pages to avoid triggering rate limits
            time.sleep(random.uniform(0.5, REQUEST_INTERVAL + 1.0))
        except Exception as e:
            print(f"  [Thread-{url_idx}] {CLR_RED}[ERROR]{CLR_RESET} Slug extraction failed for {tag} on Page {page}: {e}")
            break

def main():
    sys.stdout.reconfigure(encoding='utf-8')

    print(f"\n{CLR_BOLD}{CLR_CYAN}Zameen.com Property Scraper (curl_cffi - Multithreaded){CLR_RESET}")
    print(f"Target count: {MAX_ITEMS_TO_COLLECT} | Threads: {MAX_THREADS} | CSV file: {CSV_FILENAME}")
    print(f"Initial URLs: {len(SCRAPE_URLS)} start directories configured.\n")

    collected_slugs = []
    slug_lock = threading.Lock()
    
    print(f"{CLR_BOLD}{CLR_YELLOW}Phase 1: Discovering Property Slugs (Multithreaded){CLR_RESET}")

    # Run one thread per start URL to collect property slugs in parallel
    with ThreadPoolExecutor(max_workers=min(len(SCRAPE_URLS), MAX_THREADS)) as executor:
        futures = [
            executor.submit(scrape_slugs_from_start_url, scrape_url, idx, collected_slugs, slug_lock)
            for idx, scrape_url in enumerate(SCRAPE_URLS, 1)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  {CLR_RED}[ERROR]{CLR_RESET} A slug discovery thread crashed: {e}")

    if not collected_slugs:
        print(f"\n{CLR_RED}[ERROR] No targets collected. Exiting.{CLR_RESET}")
        return

    print(f"\n{CLR_BOLD}{CLR_YELLOW}Phase 2: Crawling Property Details (Multithreaded){CLR_RESET}")
    csv_headers = [
        "Title", "Price", "Area", "City", "Number of bedrooms", "Number of bathrooms",
        "Location", "Property type", "Built in year", "Parking space",
        "Servant Quarters", "Store rooms", "Kitchens", "Drawing Room",
        "Dining Room", "Study Room", "Prayer Room", "Powder Room",
        "Lounge or Sitting Room", "Laundry Room"
    ]
    
    csv_lock = threading.Lock()
    success_count = 0
    
    # Ensure data directory exists
    data_dir = os.path.dirname(CSV_FILENAME)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        
    with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=csv_headers)
        writer.writeheader()
        
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = {
                executor.submit(crawl_listing, slug, idx): slug
                for idx, slug in enumerate(collected_slugs, 1)
            }

            # Process each listing result as its thread finishes
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    record, idx = future.result()
                    with csv_lock:
                        writer.writerow(record)
                        csv_file.flush()
                        success_count += 1
                    print(f"  [{idx}/{len(collected_slugs)}] {CLR_GREEN}OK{CLR_RESET} - Price: {record['Price']} | Beds: {record['Number of bedrooms']}")
                except Exception as e:
                    idx = collected_slugs.index(slug) + 1
                    print(f"  [{idx}/{len(collected_slugs)}] {CLR_RED}ERROR{CLR_RESET} - Skip: {slug[:25]}... ({e})")

    print(f"\n{CLR_BOLD}{CLR_GREEN}Scraping Finished Successfully!{CLR_RESET}")
    print(f"Total slugs: {len(collected_slugs)} | Saved records: {success_count}")
    print(f"Dataset path: {os.path.abspath(CSV_FILENAME)}\n")

if __name__ == "__main__":
    main()
