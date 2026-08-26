import html
import re
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select

from .config import settings
from .models import GermanMarketComparison, Vehicle


MAKE_SLUGS = {
    "mercedes": "mercedes-benz", "mercedes-benz": "mercedes-benz",
    "range rover": "land-rover", "land rover": "land-rover",
    "rolls royce": "rolls-royce", "rolls-royce": "rolls-royce",
}
MODEL_ALIASES = {
    "mercedes-benz": {"a": "a-klasse", "b": "b-klasse", "c": "c-klasse", "e": "e-klasse", "s": "s-klasse"},
    "land-rover": {"range": "range-rover"},
}


def _slug(value):
    value = value.lower().strip().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def search_url(vehicle):
    make_text = (vehicle.make or "").strip().lower()
    make = MAKE_SLUGS.get(make_text, _slug(make_text))
    raw_model = (vehicle.model or "").strip()
    token = re.split(r"[\s/-]+", raw_model)[0].lower() if raw_model else ""
    model = MODEL_ALIASES.get(make, {}).get(token, _slug(token))
    if not make or not model or not vehicle.year:
        raise ValueError("make, model and year are required")
    return f"https://www.autoscout24.de/lst/{quote(make)}/{quote(model)}/re_{vehicle.year}?atype=C&cy=D&damaged_listing=exclude&desc=0&sort=standard&ustate=N%2CU"


def fetch_comparables(vehicle):
    url = search_url(vehicle)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EmiratesAuctionIntelligence/1.0; +https://emiratesauction.smarbiz.sbs)",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
    }
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
    articles = re.findall(r'<article\b[^>]*data-testid="list-item"[^>]*>', response.text, re.I)
    candidates = []
    for tag in articles:
        attrs = dict(re.findall(r'data-([\w-]+)="([^"]*)"', tag))
        try:
            price, year = int(attrs["price"]), int(attrs.get("first-registration", "-").split("-")[-1])
        except (KeyError, ValueError):
            continue
        if abs(year - int(vehicle.year)) > 1 or not 1000 <= price <= 1000000:
            continue
        mileage = int(attrs.get("mileage", 0) or 0)
        candidates.append({"price_eur": price, "mileage_km": mileage or None, "model": html.unescape(attrs.get("model", "")), "year": year})
    variant_numbers = re.findall(r"\b\d{2,3}\b", vehicle.model or "")
    exact = [x for x in candidates if all(re.search(rf"\b{re.escape(number)}\b", x["model"], re.I) for number in variant_numbers)]
    samples = exact if variant_numbers and len(exact) >= 3 else candidates
    mileage_matches = [x for x in samples if not vehicle.mileage or not x["mileage_km"] or abs(x["mileage_km"] - vehicle.mileage) <= max(75000, vehicle.mileage * .75)]
    if len(mileage_matches) >= 3:
        samples = mileage_matches
    if len(samples) < 3:
        raise ValueError("fewer than 3 comparable German listings")
    prices = [x["price_eur"] for x in samples]
    return url, samples[:40], Decimal(str(statistics.median(prices))), Decimal(min(prices)), Decimal(max(prices))


def compare_vehicle(db, vehicle):
    row = db.scalar(select(GermanMarketComparison).where(GermanMarketComparison.vehicle_id == vehicle.id))
    if not row:
        row = GermanMarketComparison(vehicle_id=vehicle.id, search_url="", eur_aed_rate=Decimal(str(settings.eur_aed_rate)))
        db.add(row)
    row.fetched_at = datetime.now(timezone.utc)
    row.eur_aed_rate = Decimal(str(settings.eur_aed_rate))
    try:
        url, samples, median, low, high = fetch_comparables(vehicle)
        row.search_url, row.samples = url, samples
        row.median_price_eur, row.min_price_eur, row.max_price_eur = median, low, high
        row.comparable_count, row.status, row.error = len(samples), "ready", None
    except Exception as exc:
        try: row.search_url = search_url(vehicle)
        except Exception: row.search_url = "https://www.autoscout24.de/"
        row.status, row.error = "unavailable", str(exc)[:300]
    db.commit(); db.refresh(row)
    return row


def compare_closed(db, limit=None):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.autoscout_refresh_hours)
    rows = db.scalars(
        select(Vehicle).outerjoin(GermanMarketComparison).where(
            Vehicle.status == "finished",
            Vehicle.price_data_valid.is_(True),
            or_(GermanMarketComparison.id.is_(None), GermanMarketComparison.fetched_at < cutoff),
        ).order_by(Vehicle.auction_end_time.desc())
    ).all()
    return [compare_vehicle(db, vehicle) for vehicle in rows[:limit or settings.autoscout_batch_size]]


def compare_live(db, limit=None):
    """Build a German reference before an auction ends so buy decisions are useful live."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.autoscout_live_refresh_hours)
    rows = db.scalars(
        select(Vehicle).outerjoin(GermanMarketComparison).where(
            Vehicle.status.in_(("active", "ending")),
            Vehicle.is_tracked.is_(True),
            Vehicle.make.is_not(None), Vehicle.model.is_not(None), Vehicle.year.is_not(None),
            or_(GermanMarketComparison.id.is_(None), GermanMarketComparison.fetched_at < cutoff),
        ).order_by(Vehicle.auction_end_time.asc())
    ).all()
    return [compare_vehicle(db, vehicle) for vehicle in rows[:limit or settings.autoscout_live_batch_size]]


def serialize_comparison(vehicle):
    row = vehicle.german_market
    if not row:
        return {"status": "pending"}
    result = vehicle.result
    if vehicle.status == "finished" and vehicle.price_data_valid and result and result.final_bid is not None:
        purchase_bid = Decimal(result.final_bid)
        bid_basis = "Endpreis"
    else:
        purchase_bid = Decimal(vehicle.current_bid or 0)
        bid_basis = "Aktuelles Gebot"
    market_aed = Decimal(row.median_price_eur or 0) * Decimal(row.eur_aed_rate or 0)
    gross = market_aed - purchase_bid
    net = gross - Decimal(vehicle.repair_estimate or 0) - Decimal(vehicle.import_cost or 0)
    return {
        "source": row.source, "status": row.status, "search_url": row.search_url,
        "median_price_eur": float(row.median_price_eur or 0),
        "min_price_eur": float(row.min_price_eur or 0), "max_price_eur": float(row.max_price_eur or 0),
        "comparable_count": row.comparable_count, "eur_aed_rate": float(row.eur_aed_rate),
        "market_value_aed": float(market_aed), "gross_spread_aed": float(gross),
        "estimated_net_profit_aed": float(net), "fetched_at": row.fetched_at,
        "bid_basis": bid_basis, "error": row.error,
    }
