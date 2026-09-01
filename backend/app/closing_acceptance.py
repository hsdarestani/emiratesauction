"""Deployment acceptance check for the auction-closing pipeline.

Runs inside the production backend image but uses an isolated in-memory database,
so it cannot create fake rows in the real auction database. It also checks that
the official Emirates Auction active feed is reachable and its EndDate is parsed
as timezone-aware UTC.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .database import Base
from .main import closed
from .models import Vehicle
from .scraper import fetch_live, normalize
from .worker import _finalize_from_closing_feed


def _session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _vehicle(lot_id, now, seen_seconds_ago, bid):
    return Vehicle(
        lot_id=lot_id,
        url=f"https://www.emiratesauction.com/auctions/vehicles/{lot_id}/4",
        title="Synthetic deployment acceptance vehicle",
        current_bid=bid,
        last_live_bid=bid,
        last_live_bid_at=now - timedelta(seconds=seen_seconds_ago),
        bid_count=8,
        auction_end_time=now - timedelta(seconds=1),
        status="ending",
        is_tracked=True,
    )


def check_upstream_feed():
    inventory = fetch_live()
    if len(inventory) < 5:
        raise RuntimeError(f"Official Emirates feed unexpectedly small: {len(inventory)}")
    sample = normalize(inventory[0])
    end = sample.get("auction_end_time")
    if end is not None and (end.tzinfo is None or end.utcoffset() is None):
        raise RuntimeError("Emirates EndDate was not normalized to timezone-aware UTC")
    print({"acceptance_upstream_feed": "ok", "active_feed_size": len(inventory)})


def check_closing_pipeline():
    now = datetime.now(timezone.utc)
    with _session() as db:
        valid = _vehicle("__acceptance-valid__", now, 4, 54321)
        db.add(valid)
        db.commit()
        state = _finalize_from_closing_feed(db, valid, now)
        if state != "finished_valid":
            raise RuntimeError(f"Fresh closing observation did not finalize: {state}")
        cards = closed(db)
        card = next((x for x in cards if x["lot_id"] == "__acceptance-valid__"), None)
        if not card or card["final_bid"] != 54321.0 or not card["price_data_valid"]:
            raise RuntimeError("Valid closing price did not reach the public closed serializer")

    with _session() as db:
        stale = _vehicle("__acceptance-stale__", now, 20, 99999)
        db.add(stale)
        db.commit()
        state = _finalize_from_closing_feed(db, stale, now)
        if state != "finished_unreliable":
            raise RuntimeError(f"Stale closing observation was incorrectly trusted: {state}")
        if any(x["lot_id"] == "__acceptance-stale__" for x in closed(db)):
            raise RuntimeError("Unreliable closing price leaked into the public closed list")

    print({"acceptance_closing_pipeline": "ok", "published_final_bid": 54321})


def main():
    check_upstream_feed()
    check_closing_pipeline()
    print("Closing acceptance check passed")


if __name__ == "__main__":
    main()
