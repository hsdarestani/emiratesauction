from decimal import Decimal
from sqlalchemy import desc, select

from .models import AuctionResult, AuctionSnapshot, MarketPrice, Vehicle, VehicleImage
from .scraper import fetch_detail, fetch_live, normalize


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
    listings = active_inventory[:limit]
    collected = []
    for listing in listings:
        try: detail = fetch_detail(listing.get("Lot") or listing["Id"])
        except Exception: detail = None
        collected.append(upsert_vehicle(db, normalize(listing, detail), tracked=True))
    tracked = db.scalars(select(Vehicle).where(Vehicle.is_tracked.is_(True), Vehicle.status == "active")).all()
    known = {str(x.get("Lot") or x.get("Id")): x for x in active_inventory}
    for vehicle in tracked:
        listing = known.get(vehicle.lot_id)
        if listing:
            if vehicle.lot_id not in {v.lot_id for v in collected}:
                try: detail = fetch_detail(vehicle.lot_id)
                except Exception: detail = None
                upsert_vehicle(db, normalize(listing, detail), tracked=True)
            continue
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
