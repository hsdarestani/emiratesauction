from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import monitoring_is_valid, poll_interval, resolve_closing_price
from app.worker import closing_observation_is_valid, closing_queue_for


def test_adaptive_poll_intervals():
    now = datetime.now(timezone.utc)
    assert poll_interval(now + timedelta(hours=1), now) == 60
    assert poll_interval(now + timedelta(minutes=20), now) == 20
    assert poll_interval(now + timedelta(minutes=4), now) == 5
    assert poll_interval(now + timedelta(seconds=45), now) == 2


def test_past_end_is_polled_immediately():
    now = datetime.now(timezone.utc)
    assert poll_interval(now - timedelta(seconds=1), now) == 2


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


def test_closing_feed_only_trusts_a_recent_observation():
    observed = datetime.now(timezone.utc)
    assert closing_observation_is_valid(observed - timedelta(seconds=8), observed)
    assert not closing_observation_is_valid(observed - timedelta(seconds=9), observed)
    assert not closing_observation_is_valid(None, observed)
