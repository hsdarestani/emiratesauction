from datetime import datetime, timedelta, timezone

from app.services import monitoring_is_valid, poll_interval


def test_adaptive_poll_intervals():
    now = datetime.now(timezone.utc)
    assert poll_interval(now + timedelta(hours=1), now) == 60
    assert poll_interval(now + timedelta(minutes=20), now) == 20
    assert poll_interval(now + timedelta(minutes=4), now) == 5
    assert poll_interval(now + timedelta(seconds=45), now) == 2


def test_past_end_is_polled_immediately():
    now = datetime.now(timezone.utc)
    assert poll_interval(now - timedelta(seconds=1), now) == 2


def test_end_price_requires_tight_monitoring_gap():
    finished = datetime.now(timezone.utc)
    assert monitoring_is_valid(finished - timedelta(seconds=5), finished)
    assert not monitoring_is_valid(finished - timedelta(seconds=6), finished)
    assert not monitoring_is_valid(None, finished)
