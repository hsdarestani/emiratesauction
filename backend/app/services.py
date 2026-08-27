from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import desc, select

from .config import settings
from .damage_analysis import analyze_purchase
from .models import AuctionResult, AuctionSnapshot, MarketPrice, Vehicle, VehicleImage
from .scraper import fetch_detail, fetch_live, is_quality_vehicle, normalize


def upsert_vehicle(db, payload, tracked=False):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.lot_id == payload["lot_id"]))
    if not vehicle:
        vehicle = Vehicle(**{k: v for k, v in payload.items() if k != "images"}, is_tracked=tracked)
        vehicle.last_live_bid = payload["current_bid"]
        vehicle.last_live_bid_at = datetime.now(timezone.utc)
        db.add(vehicle); db.flush()
    else:
        for key, value in payload.items():
            if key != "images" and value is not None:
                setattr(vehicle, key, value)
        if payload["status"] in ("active", "ending"):
            vehicle.last_live_bid = payload["current_bid"]
            vehicle.last_live_bid_at = datetime.now(timezone.utc)
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


def monitoring_is_valid(last_seen_at, finished_at, max_gap_seconds=5):
    if not last_seen_at:
        return False
    return 0 <= (finished_at - last_seen_at).total_seconds() <= max_gap_seconds


def resolve_closing_price(detail_price, last_live_bid, last_seen_at, observed_at, max_gap_seconds=5):
    """Choose the best observed closing price without inventing a result.

    Emirates Auction's official detail payload can keep CurrentPriceStr available
    after IsExpired becomes true. That value is stronger evidence than our queue
    timing, so it remains usable even if a worker reached the expiry response a
    few seconds late. If the expired detail hides the price, only a tightly
    observed pre-expiry live bid may be used.
    """
    official = Decimal(detail_price or 0)
    fallback = Decimal(last_live_bid or 0)
    if official > 0:
        return official, True, "emirates_expired_detail"
    if fallback > 0 and monitoring_is_valid(last_seen_at, observed_at, max_gap_seconds):
        return fallback, True, "near_realtime_at_expiry"
    if fallback > 0:
        return fallback, False, "monitoring_gap_at_expiry"
    return Decimal(0), False, "missing_closing_price"


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
        observed_at = datetime.now(timezone.utc)
        previous_seen_at = vehicle.last_live_bid_at
        gap = max(0, round((observed_at - previous_seen_at).total_seconds())) if previous_seen_at else None
        detail_price = Decimal(payload["current_bid"] or 0)
        price, valid, source = resolve_closing_price(
            detail_price,
            vehicle.last_live_bid or vehicle.current_bid,
            previous_seen_at,
            observed_at,
        )
        bids = payload["bid_count"] if payload["bid_count"] else vehicle.bid_count
        previous = db.scalar(select(AuctionSnapshot).where(AuctionSnapshot.vehicle_id == vehicle.id).order_by(desc(AuctionSnapshot.timestamp)).limit(1))
        if price > 0 and (not previous or previous.current_bid != price or previous.bid_count != bids):
            jump = price - (previous.current_bid if previous else price)
            db.add(AuctionSnapshot(vehicle_id=vehicle.id, current_bid=price, bid_count=bids, price_jump=jump))
        if price > 0:
            vehicle.current_bid = price
            vehicle.last_live_bid = price
        # Preserve the true pre-expiry live observation timestamp when we had to
        # fall back to it; an after-expiry fetch must not make old evidence look fresh.
        if detail_price > 0:
            vehicle.last_live_bid_at = observed_at
        vehicle.bid_count = bids
        vehicle.auction_end_time = payload["auction_end_time"] or vehicle.auction_end_time
        vehicle.finished_at = vehicle.finished_at or observed_at
        vehicle.monitoring_gap_seconds = gap
        vehicle.price_data_valid = valid
        vehicle.price_source = source
        vehicle.status = "finished" if valid else "finished_unreliable"
        result = db.scalar(select(AuctionResult).where(AuctionResult.vehicle_id == vehicle.id))
        if not result:
            result = AuctionResult(vehicle_id=vehicle.id, final_bid=price if valid else None)
            db.add(result)
        result.final_bid = price if valid else None
        result.final_price_status = "observed" if valid else "unreliable"
        db.commit()
        return "finished_valid" if valid else "finished_unreliable"
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
    result.final_bid = price
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
    for listing in listings:
        collected.append(upsert_vehicle(db, normalize(listing), tracked=True))
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
        if not is_quality_vehicle({"Title": vehicle.title}):
            vehicle.status = "ignored"; vehicle.is_tracked = False; db.commit(); continue
        try:
            update_one(db, vehicle)
        except Exception:
            continue
    return collected


def opportunity(vehicle):
    manual_prices = [Decimal(x.market_price) for x in vehicle.market_prices]
    market = sum(manual_prices) / len(manual_prices) if manual_prices else Decimal(0)
    market_source = "Manuelle Bewertung" if market > 0 else None
    german = vehicle.german_market
    if german and german.status == "ready" and german.median_price_eur:
        market = Decimal(german.median_price_eur) * Decimal(german.eur_aed_rate or settings.eur_aed_rate)
        market_source = "AutoScout24 Deutschland"

    if vehicle.status == "finished" and vehicle.price_data_valid and vehicle.result and vehicle.result.final_bid is not None:
        purchase_price = Decimal(vehicle.result.final_bid)
    else:
        purchase_price = Decimal(vehicle.current_bid or 0)

    analysis = analyze_purchase(
        condition=vehicle.condition,
        tags=vehicle.condition_tags,
        damage_description=vehicle.damage_description,
        make=vehicle.make,
        purchase_price_aed=purchase_price,
        market_value_aed=market,
        import_cost_aed=vehicle.import_cost,
        manual_repair_aed=vehicle.repair_estimate,
        eur_aed_rate=settings.eur_aed_rate,
    )
    analysis.update({
        "market_price": float(market),
        "market_reference_source": market_source,
        "potential_profit": analysis["estimated_net_profit_aed"],
        "discount_percent": analysis["estimated_margin_percent"],
        "risk_score": analysis["damage_risk_score"],
    })
    return analysis
