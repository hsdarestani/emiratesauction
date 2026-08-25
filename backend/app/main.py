from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import Base, engine, get_db
from .models import AuctionSnapshot, MarketPrice, Vehicle
from .services import collect, opportunity

app = FastAPI(title="Emirates Auction Intelligence", docs_url="/api/docs", openapi_url="/api/openapi.json")


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
def startup(): Base.metadata.create_all(engine)


def serialize(v):
    data = {c.name: getattr(v, c.name) for c in v.__table__.columns}
    data.update(opportunity(v)); data["images"] = [x.url for x in v.images[:20]]
    data["final_bid"] = float(v.result.final_bid) if v.result else (float(v.current_bid or 0) if v.status == "closed" else None)
    data["sold_date"] = v.result.sold_date if v.result else None
    return data


@app.get("/api/health")
def health(): return {"status": "ok", "source": "Emirates Auction live API"}


@app.get("/api/vehicles")
def vehicles(db: Session = Depends(get_db)):
    rows = db.scalars(select(Vehicle).options(selectinload(Vehicle.images), selectinload(Vehicle.market_prices)).order_by(Vehicle.auction_end_time)).all()
    return [serialize(x) for x in rows]


@app.get("/api/vehicles/{vehicle_id}")
def vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    row = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id).options(selectinload(Vehicle.images), selectinload(Vehicle.market_prices)))
    if not row: raise HTTPException(404, "Vehicle not found")
    return serialize(row)


@app.get("/api/vehicles/{vehicle_id}/history")
def history(vehicle_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(AuctionSnapshot).where(AuctionSnapshot.vehicle_id == vehicle_id).order_by(AuctionSnapshot.timestamp)).all()


@app.get("/api/auctions/live")
def live(db: Session = Depends(get_db)):
    rows = db.scalars(select(Vehicle).where(Vehicle.status == "active").options(selectinload(Vehicle.images), selectinload(Vehicle.market_prices), selectinload(Vehicle.result)).order_by(Vehicle.auction_end_time)).all()
    return [serialize(x) for x in rows]


@app.get("/api/auctions/closed")
def closed(db: Session = Depends(get_db)):
    rows = db.scalars(select(Vehicle).where(Vehicle.status == "closed").options(selectinload(Vehicle.images), selectinload(Vehicle.market_prices), selectinload(Vehicle.result)).order_by(desc(Vehicle.auction_end_time), desc(Vehicle.updated_at))).all()
    return [serialize(x) for x in rows]


@app.get("/api/opportunities")
def opportunities(db: Session = Depends(get_db)):
    rows = db.scalars(select(Vehicle).options(selectinload(Vehicle.images), selectinload(Vehicle.market_prices))).all()
    return sorted([serialize(x) for x in rows], key=lambda x: x["potential_profit"], reverse=True)


@app.post("/api/tracked-auctions")
def track(body: TrackIn, x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Invalid admin token")
    import re
    match = re.search(r"/vehicles/(\d+)", str(body.lot_url))
    if not match: raise HTTPException(422, "Invalid Emirates Auction vehicle URL")
    from .scraper import fetch_detail, normalize
    from .services import upsert_vehicle
    row = upsert_vehicle(db, normalize({}, fetch_detail(match.group(1))), tracked=True)
    row.vin, row.notes, row.target_price = body.vin or row.vin, body.notes, body.target_price
    db.commit(); return {"id": row.id, "lot_id": row.lot_id, "tracked": True}


@app.post("/api/vehicles/{vehicle_id}/valuation")
def valuation(vehicle_id: int, body: ValuationIn, x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Invalid admin token")
    row = db.get(Vehicle, vehicle_id)
    if not row: raise HTTPException(404, "Vehicle not found")
    row.repair_estimate, row.import_cost = body.repair_estimate, body.import_cost
    db.add(MarketPrice(vehicle_id=row.id, source=body.source, market_price=body.market_price)); db.commit()
    return {"ok": True}


@app.post("/api/admin/collect-now")
def collect_now(x_admin_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if x_admin_token != settings.admin_token: raise HTTPException(401, "Invalid admin token")
    return {"lots": [v.lot_id for v in collect(db, settings.poll_limit)]}
