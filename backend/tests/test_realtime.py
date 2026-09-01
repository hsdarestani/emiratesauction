from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import closed
from app.models import Vehicle
from app.scraper import parse_dt
from app.services import monitoring_is_valid, poll_interval, resolve_closing_price
from app.worker import _finalize_from_closing_feed, closing_observation_is_valid, closing_queue_for


def test_adaptive_poll_intervals():
    now = datetime.now(timezone.utc)
    assert poll_interval(now + timedelta(hours=1), now) == 60
    assert poll_interval(now + timedelta(minutes=20), now) == 20
    assert poll_interval(now + timedelta(minutes=4), now) == 5
    assert poll_interval(now + timedelta(seconds=45), now) == 2


def test_past_end_is_polled_immediately():
    now = datetime.now(timezone.utc)
    assert poll_interval(now - timedelta(seconds=1), now) == 2


def test_emirates_naive_enddate_is_dubai_local_time():
    parsed = parse_dt("2026-08-27T21:50:40")
    assert parsed == datetime(2026, 8, 27, 17, 50, 40, tzinfo=timezone.utc)


def test_emirates_explicit_timezone_is_preserved_and_normalized_to_utc():
    assert parse_dt("2026-08-27T21:50:40+04:00") == datetime(2026, 8, 27, 17, 50, 40, tzinfo=timezone.utc)
    assert parse_dt("2026-08-27T17:50:40Z") == datetime(2026, 8, 27, 17, 50, 40, tzinfo=timezone.utc)


def test_end_price_requires_tight_monitoring_gap_when_expired_detail_hides_price():
    finished = datetime.now(timezone.utc)
    assert monitoring_is_valid(finished - timedelta(seconds=5), finished)
    assert not monitoring_is_valid(finished - timedelta(seconds=6), finished)
    assert not monitoring_is_valid(None, finished)

    price, valid, source = resolve_closing_price(
        0, 125000, finished - timedelta(seconds=4), finished
    )
    assert price == 125000
    assert valid is True
    assert source == "near_realtime_at_expiry"

    _, valid, source = resolve_closing_price(
        0, 125000, finished - timedelta(seconds=20), finished
    )
    assert valid is False
    assert source == "monitoring_gap_at_expiry"


def test_official_expired_detail_price_survives_worker_delay():
    observed = datetime.now(timezone.utc)
    price, valid, source = resolve_closing_price(
        137000, 120000, observed - timedelta(seconds=30), observed
    )
    assert price == 137000
    assert valid is True
    assert source == "emirates_expired_detail"


def test_last_five_minutes_use_dedicated_closing_queue():
    now = datetime.now(timezone.utc)
    assert closing_queue_for(SimpleNamespace(auction_end_time=now + timedelta(minutes=4)), now) == "closing"
    assert closing_queue_for(SimpleNamespace(auction_end_time=now + timedelta(minutes=8)), now) == "live"
    assert closing_queue_for(SimpleNamespace(auction_end_time=None), now) == "live"


def test_closing_feed_tolerates_measured_api_latency_but_stays_tight():
    observed = datetime.now(timezone.utc)
    assert closing_observation_is_valid(observed - timedelta(seconds=12), observed)
    assert not closing_observation_is_valid(observed - timedelta(seconds=13), observed)
    assert not closing_observation_is_valid(None, observed)


def _isolated_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _ending_vehicle(lot_id, now, last_seen_seconds_ago, bid=12345):
    return Vehicle(
        lot_id=lot_id,
        url=f"https://www.emiratesauction.com/auctions/vehicles/{lot_id}/4",
        title="Synthetic closing acceptance vehicle",
        current_bid=bid,
        last_live_bid=bid,
        last_live_bid_at=now - timedelta(seconds=last_seen_seconds_ago),
        bid_count=7,
        auction_end_time=now - timedelta(seconds=1),
        status="ending",
        is_tracked=True,
    )


def test_closing_feed_result_reaches_public_closed_endpoint():
    """Acceptance test for the exact path that used to leave Beendet empty."""
    now = datetime.now(timezone.utc)
    with _isolated_session() as db:
        vehicle = _ending_vehicle("smoke-valid", now, last_seen_seconds_ago=4, bid=54321)
        db.add(vehicle)
        db.commit()

        assert _finalize_from_closing_feed(db, vehicle, now) == "finished_valid"
        cards = closed(db)

        card = next(item for item in cards if item["lot_id"] == "smoke-valid")
        assert card["status"] == "finished"
        assert card["price_data_valid"] is True
        assert card["final_bid"] == 54321.0
        assert card["price_source"] == "emirates_live_api_closing_feed"


def test_stale_closing_observation_is_hidden_from_public_closed_endpoint():
    now = datetime.now(timezone.utc)
    with _isolated_session() as db:
        vehicle = _ending_vehicle("smoke-stale", now, last_seen_seconds_ago=20, bid=99999)
        db.add(vehicle)
        db.commit()

        assert _finalize_from_closing_feed(db, vehicle, now) == "finished_unreliable"
        assert all(item["lot_id"] != "smoke-stale" for item in closed(db))
