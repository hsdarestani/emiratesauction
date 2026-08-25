import json
import re
from datetime import datetime
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from .config import settings

HEADERS = {"Lang": "en", "Source": "web", "User-Agent": "EmiratesAuctionIntelligence/1.0"}


def money(value):
    if value is None:
        return Decimal(0)
    return Decimal(re.sub(r"[^0-9.]", "", str(value)) or "0")


def parse_dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def fetch_live(limit=10):
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        response = client.post(f"{settings.ea_api_url}/api/Vehicles", json={})
        response.raise_for_status()
        data = response.json().get("Data", [])
    active = [x for x in data if not x.get("IsExpired")]
    active.sort(key=lambda x: x.get("EndDate") or "9999")
    return active[:limit]


def fetch_detail(lot_id):
    url = f"{settings.ea_site_url}/auctions/vehicles/{lot_id}/4"
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    node = soup.select_one("#__NEXT_DATA__")
    if not node:
        raise ValueError(f"No __NEXT_DATA__ for lot {lot_id}")
    raw = json.loads(node.string)
    return raw["props"]["pageProps"]["fallback"]["detailsData"]["Data"]


def normalize(listing, detail=None):
    detail = detail or listing
    specs, notes, images, documents = {}, [], [], []
    for section in detail.get("Sections", []):
        for group in section.get("OptionGroups", []):
            if "Value" in group:
                specs[group.get("Title", "").strip().lower()] = group.get("Value")
            notes.extend(o.get("Title", "") for o in group.get("Options", []))
            documents.extend([group.get("Link")] if group.get("Link") else [])
            for item in group.get("Images", []):
                link = item.get("ImageLink_Details") or item.get("ImageLink")
                if link:
                    images.append(link.replace("[w]", "1200").replace("[h]", "0"))
    tags = [t.get("Title") or t.get("TagName") for t in detail.get("Tags", listing.get("Tags", []))]
    odometer = specs.get("odometer") or detail.get("OdometerStr") or ""
    mileage_match = re.search(r"[\d,]+", odometer)
    lot = str(detail.get("Lot") or listing.get("Lot") or listing.get("Id"))
    return {
        "lot_id": lot, "auction_id": str(detail.get("AuctionTypeId") or 4),
        "url": f"{settings.ea_site_url}/auctions/vehicles/{lot}/4", "title": detail.get("Title") or listing.get("Title"),
        "vin": specs.get("vin number") or None, "make": specs.get("make"), "model": specs.get("model"),
        "trim": specs.get("trim"), "year": detail.get("Year") or listing.get("Year"),
        "mileage": int(mileage_match.group().replace(",", "")) if mileage_match else None,
        "fuel": specs.get("fuel type"), "transmission": specs.get("transmission"), "color": specs.get("exterior"),
        "body_type": specs.get("body type") or detail.get("CarType"), "condition": ", ".join(filter(None, tags)) or None,
        "condition_tags": list(filter(None, tags)), "keys_available": specs.get("keys"),
        "inspection_report_url": documents[0] if documents else None, "damage_description": "; ".join(filter(None, notes)) or None,
        "current_bid": money(detail.get("CurrentPriceStr") or listing.get("CurrentPriceStr")),
        "bid_count": int(detail.get("Bids") or listing.get("Bids") or 0), "auction_end_time": parse_dt(detail.get("EndDate") or listing.get("EndDate")),
        "status": "closed" if detail.get("IsExpired", listing.get("IsExpired")) else "active",
        "images": list(dict.fromkeys(([detail.get("MainImage").replace("[w]", "1200").replace("[h]", "0")] if detail.get("MainImage") else []) + images))[:60],
    }
