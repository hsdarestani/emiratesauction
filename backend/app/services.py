from decimal import Decimal
from sqlalchemy import desc, select

from .models import AuctionResult, AuctionSnapshot, MarketPrice, Vehicle, VehicleImage
from .scraper import fetch_detail, fetch_live, is_quality_vehicle, normalize


def upsert_vehicle(db, payload, tracked=False):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.lot_id == payload["lot_id"]))
    if not vehicle:
        vehicle = Vehicle(**{k: v for k, v in payload.items() if k != "images"}, is_tracked=tracked)
        db.add(vehicle); db.flush()
    else:
        for key, value in payload.items():
            if key != "images" and value is not None:
                setattr(vehicle, key, value)
        vehicle.is_tracked = vehicle.is_tracked or tracked
    previous = db.scalar(select(AuctionSnapshot).where(AuctionSnapshot.vehicle_id == vehicle.id).order_by(desc(AuctionSnapshot.timestamp)).limit(1))
    price, bids = Decimal(payload["current_bid"]), payload["bid_count"]
    if not previous or previous.current_bid != price or previous.bid_count != bids:
        jump = price - (previous.current_bid if previous else price)
        db.add(AuctionSnapshot(vehicle_id=vehicle.id, current_bid=price, bid_count=bids, price_jump=jump))
    for url in payload.get("images", []):
        exists = db.scalar(select(VehicleImage).where(VehicleImage.vehicle_id == vehicle.id, VehicleImage.url == url))
        if not exists: db.add(VehicleImage(vehicle_id=vehicle.id, url=url))
    if payload["status"] == "closed":
        result = db.scalar(select(AuctionResult).where(AuctionResult.vehicle_id == vehicle.id))
        if not result: db.add(AuctionResult(vehicle_id=vehicle.id, final_bid=price))
    db.commit(); db.refresh(vehicle)
    return vehicle


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
        vehicle.status = "closed"
        result = db.scalar(select(AuctionResult).where(AuctionResult.vehicle_id == vehicle.id))
        if not result:
            db.add(AuctionResult(vehicle_id=vehicle.id, final_bid=Decimal(vehicle.current_bid or 0)))
        db.commit()
    return collected


def opportunity(vehicle):
    prices = [Decimal(x.market_price) for x in vehicle.market_prices]
    market = sum(prices) / len(prices) if prices else Decimal(0)
    profit = market - Decimal(vehicle.current_bid or 0) - Decimal(vehicle.repair_estimate or 0) - Decimal(vehicle.import_cost or 0)
    discount = (profit / market * 100) if market else Decimal(0)
    risk = min(100, 18 * len(vehicle.condition_tags or []))
    return {"market_price": float(market), "potential_profit": float(profit), "discount_percent": round(float(discount), 1), "risk_score": risk}
