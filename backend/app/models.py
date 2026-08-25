from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now():
    return datetime.now(timezone.utc)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    auction_id: Mapped[str] = mapped_column(String(40), default="4")
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(255))
    vin: Mapped[str | None] = mapped_column(String(80))
    make: Mapped[str | None] = mapped_column(String(100), index=True)
    model: Mapped[str | None] = mapped_column(String(100), index=True)
    trim: Mapped[str | None] = mapped_column(String(100))
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    mileage: Mapped[int | None] = mapped_column(Integer)
    fuel: Mapped[str | None] = mapped_column(String(60))
    transmission: Mapped[str | None] = mapped_column(String(60))
    color: Mapped[str | None] = mapped_column(String(60))
    body_type: Mapped[str | None] = mapped_column(String(80))
    condition: Mapped[str | None] = mapped_column(String(120))
    condition_tags: Mapped[list] = mapped_column(JSON, default=list)
    keys_available: Mapped[str | None] = mapped_column(String(40))
    inspection_report_url: Mapped[str | None] = mapped_column(Text)
    damage_description: Mapped[str | None] = mapped_column(Text)
    starting_bid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    current_bid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), index=True)
    bid_count: Mapped[int] = mapped_column(Integer, default=0)
    reserve_status: Mapped[str | None] = mapped_column(String(80))
    auction_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auction_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    repair_estimate: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    import_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    snapshots = relationship("AuctionSnapshot", cascade="all, delete-orphan")
    images = relationship("VehicleImage", cascade="all, delete-orphan")
    market_prices = relationship("MarketPrice", cascade="all, delete-orphan")


class AuctionSnapshot(Base):
    __tablename__ = "auction_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    current_bid: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    bid_count: Mapped[int] = mapped_column(Integer)
    price_jump: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuctionResult(Base):
    __tablename__ = "auction_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), unique=True)
    final_bid: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    sold_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    winner_status: Mapped[str] = mapped_column(String(50), default="unknown")


class VehicleImage(Base):
    __tablename__ = "vehicle_images"
    __table_args__ = (UniqueConstraint("vehicle_id", "url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    url: Mapped[str] = mapped_column(Text)


class MarketPrice(Base):
    __tablename__ = "market_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    market_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

