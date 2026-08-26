from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import desc, select

from .models import AuctionResult, AuctionSnapshot, MarketPrice, Vehicle, VehicleImage
from .scraper import fetch_detail, fetch_live, is_quality_vehicle, normalize


def upsert_vehicle(db, payload, tracked=False):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.lot_id == payload["lot_id"]))
    if not vehicle:
        vehicle = Vehicle(**{k: v for k, v in payload.items() if k != "images"}, is_tracked=tracked)
        vehicle.last_live_bid = payload["current_bid"]
        db.add(vehicle); db.flush()
    else:
        for key, value in payload.items():
            if key != "images" and value is not None:
                setattr(vehicle, key, value)
        if payload["status"] in ("active", "ending"):
            vehicle.last_live_bid = payload["current_bid"]
        vehicle.is_tracked = vehicle.is_tracked or tracked
    previous = db.scalar(select(AuctionSnapshot).where(AuctionSnapshot.vehicle_id == vehicle.id).order_by(desc(AuctionSnapshot.timestamp)).limit(1))
    price, bids = Decimal(payload["current_bid"]), payload["bid_count"]
    if not previous or previous.current_bid != price or previous.bid_count != bids:
        jump = price - (previous.current_bid if previous else price)
        db.add(AuctionSnapshot(vehicle_id=vehicle.id, current_bid=price, bid_count=bids, price_jump=jump))
    for url in payload.get("images", []):
        exists = db.scalar(select(VehicleImage).where(VehicleImage.vehicle_id == vehicle.id, VehicleImage.url == url))
        if not exists: db.add(VehicleImage(vehicle_id=vehicle.id, url=url))
    db.commit(); db.refresh(vehicle)
    return vehicle


def poll_interval(end_time, now=None):
    now = now or datetime.now(timezone.utc)
    if not end_time:
        return 60
    remaining = (end_time - now).total_seconds()
    if remaining <= 60:
        return 2
    if remaining <= 5 * 60:
        return 5
    if remaining <= 30 * 60:
        return 20
    return 60


def mark_finalizing(db, vehicle, now=None):
    now = now or datetime.now(timezone.utc)
    vehicle.status = "finalizing"
    vehicle.finished_at = vehicle.finished_at or now
    result = db.scalar(select(AuctionResult).where(AuctionResult.vehicle_id == vehicle.id))
    if not result:
        result = AuctionResult(vehicle_id=vehicle.id, final_bid=None, final_price_status="finalizing")
        db.add(result)
    db.commit()
    return result


def update_one(db, vehicle, detail=None):
    """Poll one official detail page and make the end transition idempotently."""
    detail = detail or fetch_detail(vehicle.lot_id)
    payload = normalize({}, detail)
    expired = payload["status"] == "closed"
    if expired:
        mark_finalizing(db, vehicle)
        return "finished"
    payload["status"] = "ending" if poll_interval(payload["auction_end_time"]) <= 5 else "active"
    updated = upsert_vehicle(db, payload, tracked=True)
    updated.next_poll_at = datetime.now(timezone.utc) + timedelta(seconds=poll_interval(updated.auction_end_time))
    db.commit()
    return "live"


def verify_final_price(db, vehicle, detail=None):
    """Publish a final price only when the official post-auction detail says expired."""
    result = mark_finalizing(db, vehicle)
    result.verification_attempts = (result.verification_attempts or 0) + 1
    detail = detail or fetch_detail(vehicle.lot_id)
    payload = normalize({}, detail)
    if payload["status"] != "closed":
        payload["status"] = "ending" if poll_interval(payload["auction_end_time"]) <= 5 else "active"
        updated = upsert_vehicle(db, payload, tracked=True)
        updated.next_poll_at = datetime.now(timezone.utc) + timedelta(seconds=poll_interval(updated.auction_end_time))
        result.final_bid = None
        result.verified_final_price = None
        result.final_price_verified_at = None
        result.final_price_source = None
        result.final_price_status = "live"
        db.commit()
        return None
    price = Decimal(payload["current_bid"])
    verified_at = datetime.now(timezone.utc)
    vehicle.current_bid = price
    vehicle.status = "verified"
    vehicle.finished_at = vehicle.finished_at or verified_at
    result.final_bid = price  # compatibility for existing consumers
    result.verified_final_price = price
    result.final_price_verified_at = verified_at
    result.final_price_source = f"{vehicle.url} (__NEXT_DATA__.detailsData.Data; IsExpired=true)"
    result.final_price_status = "verified"
    db.commit()
    return True


def collect(db, limit=10):
    active_inventory = fetch_live()
    listings = [item for item in active_inventory if is_quality_vehicle(item)]
    collected = []
    # Store and snapshot every qualifying vehicle from the full inventory using
    # the lightweight list payload. This makes the dashboard complete without
    # issuing hundreds of detail-page requests every five minutes.
    for listing in listings:
        collected.append(upsert_vehicle(db, normalize(listing), tracked=True))
    # Enrich a bounded rotating batch with specifications, documents and media.
    needs_detail = db.scalars(select(Vehicle).where(Vehicle.status == "active", Vehicle.is_tracked.is_(True), Vehicle.body_type.is_(None)).order_by(Vehicle.auction_end_time.asc())).all()
    for vehicle in needs_detail[:limit]:
        listing = next((x for x in listings if str(x.get("Lot") or x.get("Id")) == vehicle.lot_id), None)
        if not listing: continue
        try: detail = fetch_detail(vehicle.lot_id)
        except Exception: continue
        upsert_vehicle(db, normalize(listing, detail), tracked=True)
    tracked = db.scalars(select(Vehicle).where(Vehicle.is_tracked.is_(True), Vehicle.status == "active")).all()
    known = {str(x.get("Lot") or x.get("Id")): x for x in listings}
    for vehicle in tracked:
        listing = known.get(vehicle.lot_id)
        if listing:
            continue
        # Existing low-quality/non-car records from the first deployment should
        # disappear without being counted as historical vehicle results.
        if not is_quality_vehicle({"Title": vehicle.title}):
            vehicle.status = "ignored"; vehicle.is_tracked = False; db.commit(); continue
        try:
            update_one(db, vehicle)
        except Exception:
            # A transient list/detail outage must never fabricate an auction end.
            continue
    return collected


def opportunity(vehicle):
    prices = [Decimal(x.market_price) for x in vehicle.market_prices]
    market = sum(prices) / len(prices) if prices else Decimal(0)
    profit = market - Decimal(vehicle.current_bid or 0) - Decimal(vehicle.repair_estimate or 0) - Decimal(vehicle.import_cost or 0)
    discount = (profit / market * 100) if market else Decimal(0)
    risk = min(100, 18 * len(vehicle.condition_tags or []))
    return {"market_price": float(market), "potential_profit": float(profit), "discount_percent": round(float(discount), 1), "risk_score": risk}
