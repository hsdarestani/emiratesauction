from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import Base, engine, get_db
from .models import AuctionSnapshot, MarketPrice, Vehicle
from .autoscout import compare_vehicle, serialize_comparison
from .services import collect, opportunity
from .migrations import migrate

app = FastAPI(title="Emirates Auction Intelligence", docs_url="/api/docs", openapi_url="/api/openapi.json")

# Finished auctions may remain stored for audit/recovery even if their price
# confidence is low. The public finished list, however, only exposes trusted
# final prices.
CLOSED_STATUSES = (
    "finished",
    "finished_unreliable",
    "historical_unreliable",
    "verified",
    "verification_failed",
    "finalizing",
)


class TrackIn(BaseModel):
    lot_url: HttpUrl
    vin: str | None = None
    notes: str | None = None
    target_price: Decimal | None = None


class ValuationIn(BaseModel):
    market_price: Decimal
    source: str = "manual"
    repair_estimate: Decimal = 0
    import_cost: Decimal = 0


@app.on_event("startup")
def startup(): migrate()


def serialize(v):
    data = {c.name: getattr(v, c.name) for c in v.__table__.columns}
    data.update(opportunity(v)); data["images"] = [x.url for x in v.images[:20]]
    observed_final = v.result.final_bid if v.result and v.price_data_valid else None
    data["last_live_bid"] = float(v.last_live_bid or 0) if v.last_live_bid is not None else None
    data["verified_final_price"] = float(v.result.verified_final_price) if v.result and v.result.verified_final_price is not None else None
    data["final_bid"] = float(observed_final) if observed_final is not None else None
    data["final_price_status"] = v.result.final_price_status if v.result else ("live" if v.status in ("active", "ending") else "unavailable")
    data["final_price_verified_at"] = v.result.final_price_verified_at if v.result else None
    data["final_price_source"] = v.result.final_price_source if v.result else None
    data["sold_date"] = (v.finished_at or v.auction_end_time) if v.status in CLOSED_STATUSES else None
    data["germany"] = serialize_comparison(v)
    return data


def serialize_card(v):
    """Small payload for frequently refreshed dashboard cards."""
    observed_final = v.result.final_bid if v.result and v.price_data_valid else None
    last_observed = v.last_live_bid if v.last_live_bid is not None else v.current_bid
    return {
        "id": v.id,
        "lot_id": v.lot_id,
        "title": v.title,
        "status": v.status,
        "current_bid": float(v.current_bid or 0),
        "last_live_bid": float(last_observed) if last_observed is not None else None,
        "final_bid": float(observed_final) if observed_final is not None else None,
        "final_price_status": v.result.final_price_status if v.result else "unavailable",
        "price_source": v.price_source,
        "bid_count": v.bid_count or 0,
        "auction_end_time": v.auction_end_time,
        "finished_at": v.finished_at or v.auction_end_time,
        "updated_at": v.updated_at,
        "condition_tags": v.condition_tags or [],
        "images": [v.images[0].url] if v.images else [],
        "price_data_valid": bool(v.price_data_valid),
        "germany": serialize_comparison(v),
    }


def vehicle_query():
    return select(Vehicle).options(
        selectinload(Vehicle.images),
        selectinload(Vehicle.market_prices),
        selectinload(Vehicle.result),
        selectinload(Vehicle.german_market),
    )


def card_query():
    return select(Vehicle).options(
        selectinload(Vehicle.images),
        selectinload(Vehicle.result),
        selectinload(Vehicle.german_market),
    )


@app.get("/api/health")
def health(): return {"status": "ok", "source": "Emirates Auction live API"}


@app.get("/api/vehicles")
def vehicles(db: Session = Depends(get_db)):
    rows = db.scalars(vehicle_query().where(or_(Vehicle.status.in_(("active", "ending")), Vehicle.status.in_(CLOSED_STATUSES))).order_by(Vehicle.auction_end_time)).all()
    return [serialize(x) for x in rows]


@app.get("/api/vehicles/{vehicle_id}")
def vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    row = db.scalar(vehicle_query().where(Vehicle.id == vehicle_id))
    if not row: raise HTTPException(404, "Fahrzeug nicht gefunden")
    return serialize(row)


@app.get("/api/vehicles/{vehicle_id}/history")
def history(vehicle_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(AuctionSnapshot).where(AuctionSnapshot.vehicle_id == vehicle_id).order_by(AuctionSnapshot.timestamp)).all()


@app.get("/api/auctions/live")
def live(db: Session = Depends(get_db)):
    rows = db.scalars(card_query().where(Vehicle.status.in_(("active", "ending"))).order_by(Vehicle.auction_end_time)).all()
    return [serialize_card(x) for x in rows]


@app.get("/api/auctions/closed")
def closed(db: Session = Depends(get_db)):
    rows = db.scalars(
        card_query()
        .where(
            Vehicle.status == "finished",
            Vehicle.price_data_valid.is_(True),
        )
        .order_by(desc(func.coalesce(Vehicle.finished_at, Vehicle.auction_end_time, Vehicle.updated_at)))
    ).all()
    return [serialize_card(x) for x in rows]


@app.get("/api/opportunities")
def opportunities(include_avoid: bool = False, recommendation: str | None = None, db: Session = Depends(get_db)):
    rows = db.scalars(vehicle_query().where(or_(Vehicle.status.in_(("active", "ending")), Vehicle.price_data_valid.is_(True)))).all()
    items = [serialize(x) for x in rows]
    allowed = {"KAUFEN", "PRÜFEN", "MEIDEN"}
    if recommendation:
        wanted = recommendation.upper()
        if wanted not in allowed:
            raise HTTPException(422, "Ungültiger Kaufentscheidungsfilter")
        items = [x for x in items if x["purchase_recommendation"] == wanted]
    elif not include_avoid:
        items = [x for x in items if x["purchase_recommendation"] != "MEIDEN"]
    rank = {"KAUFEN": 0, "PRÜFEN": 1, "MEIDEN": 2}
    return sorted(items, key=lambda x: (rank.get(x["purchase_recommendation"], 9), -x["estimated_net_profit_aed"]))


@app.post("/api/tracked-auctions")
def track(body: TrackIn, x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Ungültiger Admin-Schlüssel")
    import re
    match = re.search(r"/vehicles/(\d+)", str(body.lot_url))
    if not match: raise HTTPException(422, "Ungültige Emirates-Auction-Fahrzeug-URL")
    from .scraper import fetch_detail, normalize
    from .services import upsert_vehicle
    row = upsert_vehicle(db, normalize({}, fetch_detail(match.group(1))), tracked=True)
    row.vin, row.notes, row.target_price = body.vin or row.vin, body.notes, body.target_price
    db.commit(); return {"id": row.id, "lot_id": row.lot_id, "tracked": True}


@app.post("/api/vehicles/{vehicle_id}/valuation")
def valuation(vehicle_id: int, body: ValuationIn, x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Ungültiger Admin-Schlüssel")
    row = db.get(Vehicle, vehicle_id)
    if not row: raise HTTPException(404, "Fahrzeug nicht gefunden")
    row.repair_estimate, row.import_cost = body.repair_estimate, body.import_cost
    db.add(MarketPrice(vehicle_id=row.id, source=body.source, market_price=body.market_price)); db.commit()
    return {"ok": True}


@app.post("/api/admin/collect-now")
def collect_now(x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Ungültiger Admin-Schlüssel")
    return {"lots": [v.lot_id for v in collect(db, settings.poll_limit)]}


@app.post("/api/admin/compare-autoscout/{vehicle_id}")
def compare_autoscout(vehicle_id: int, x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Ungültiger Admin-Schlüssel")
    row = db.get(Vehicle, vehicle_id)
    if not row: raise HTTPException(404, "Fahrzeug nicht gefunden")
    if row.status not in ("active", "ending", "finished"):
        raise HTTPException(409, "Für dieses Fahrzeug ist kein Marktvergleich verfügbar")
    if row.status == "finished" and not row.price_data_valid:
        raise HTTPException(409, "Der Marktvergleich ist nur für verlässliche Endpreise verfügbar")
    comparison = compare_vehicle(db, row)
    return {"status": comparison.status, "comparable_count": comparison.comparable_count}


@app.get("/api/data-quality")
def data_quality(db: Session = Depends(get_db)):
    invalid = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.status.in_(("historical_unreliable", "finished_unreliable"))))
    valid = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.status == "finished", Vehicle.price_data_valid.is_(True)))
    return {"historisch_ungueltig": invalid or 0, "historisch_valide": valid or 0}
