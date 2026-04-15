"""
TLB Scraper – South Africa
Scrapes TLB (Tractor Loader Backhoe) machine listings priced under R450 000
from Mascus South Africa and saves the results to data/listings.json.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_PRICE_ZAR = 450_000
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "listings.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-ZA,en;q=0.9",
}

# Mascus ZA search URL for used backhoe loaders with price filter
MASCUS_BASE_URL = "https://www.mascus.co.za"
MASCUS_SEARCH_URL = (
    "https://www.mascus.co.za/construction/used-backhoe-loaders"
    "?pricemax=450000&currency=ZAR&sort=price_asc"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_price(raw: str) -> int | None:
    """Parse a price string like 'R 320 000' → 320000."""
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def fetch(url: str, retries: int = 3, delay: float = 2.0) -> BeautifulSoup | None:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as exc:
            print(f"  [attempt {attempt}] Error fetching {url}: {exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(delay)
    return None


# ---------------------------------------------------------------------------
# Mascus scraper
# ---------------------------------------------------------------------------

def scrape_mascus_page(url: str) -> list[dict]:
    """Scrape a single Mascus listing page and return a list of listings."""
    soup = fetch(url)
    if soup is None:
        return []

    listings = []

    # Each listing is in an <article> or a <li> with class containing 'result'
    cards = soup.select("li.result, article.result, div.result-list-item")

    for card in cards:
        try:
            listing = parse_mascus_card(card)
            if listing:
                listings.append(listing)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"  Warning: could not parse card – {exc}", file=sys.stderr)

    return listings


def parse_mascus_card(card) -> dict | None:
    """Extract structured data from a single Mascus result card."""
    # Title / model
    title_el = card.select_one("a.main-link, h2 a, h3 a, .title a, a[data-category]")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    relative_url = title_el.get("href", "")
    listing_url = (
        relative_url if relative_url.startswith("http") else MASCUS_BASE_URL + relative_url
    )

    # Price
    price_el = card.select_one(".price, .result-price, span[class*='price']")
    price_str = price_el.get_text(strip=True) if price_el else ""
    price = clean_price(price_str)

    # Skip listings that are over the budget or have no price
    if price is None or price > MAX_PRICE_ZAR:
        return None

    # Year
    year = None
    year_el = card.select_one(".year, [class*='year']")
    if year_el:
        match = re.search(r"\b(19|20)\d{2}\b", year_el.get_text())
        if match:
            year = int(match.group())

    # Location
    location_el = card.select_one(".location, [class*='location'], [class*='country']")
    location = location_el.get_text(strip=True) if location_el else "South Africa"

    # Image
    img_el = card.select_one("img")
    image_url = ""
    if img_el:
        image_url = img_el.get("src") or img_el.get("data-src") or ""

    # Hours / condition
    hours_el = card.select_one("[class*='hours'], [class*='condition']")
    hours = hours_el.get_text(strip=True) if hours_el else ""

    # Manufacturer / brand  (first word of title is often the brand)
    brand = title.split()[0] if title else ""

    return {
        "id": re.sub(r"[^a-z0-9]", "-", listing_url.lower())[-60:],
        "title": title,
        "brand": brand,
        "year": year,
        "price_zar": price,
        "price_display": f"R {price:,}",
        "location": location,
        "hours": hours,
        "image_url": image_url,
        "listing_url": listing_url,
        "source": "Mascus ZA",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_mascus(max_pages: int = 5) -> list[dict]:
    """Scrape multiple pages of Mascus ZA TLB results."""
    all_listings: list[dict] = []
    print(f"Scraping Mascus ZA (up to {max_pages} pages)…")

    for page in range(1, max_pages + 1):
        url = MASCUS_SEARCH_URL if page == 1 else f"{MASCUS_SEARCH_URL}&page={page}"
        print(f"  Page {page}: {url}")
        page_listings = scrape_mascus_page(url)
        print(f"  → {len(page_listings)} listings found")
        all_listings.extend(page_listings)
        if len(page_listings) == 0:
            break
        time.sleep(1.5)  # be polite

    return all_listings


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------

def deduplicate(listings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in listings:
        key = item["listing_url"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("TLB Scraper – South Africa (under R450 000)")
    print("=" * 60)

    listings: list[dict] = []
    listings.extend(scrape_mascus())

    listings = deduplicate(listings)
    listings.sort(key=lambda x: x["price_zar"])

    # If the scraper returned nothing (e.g. network error), keep existing data
    if not listings:
        print("\n⚠️  No listings fetched – keeping existing data file unchanged.")
        sys.exit(0)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "max_price_zar": MAX_PRICE_ZAR,
        "count": len(listings),
        "listings": listings,
    }
    DATA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n✓ Saved {len(listings)} listings → {DATA_FILE}")


if __name__ == "__main__":
    main()
