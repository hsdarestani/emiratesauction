from datetime import datetime, timedelta, timezone

from app.services import poll_interval


def test_adaptive_poll_intervals():
    now = datetime.now(timezone.utc)
    assert poll_interval(now + timedelta(hours=1), now) == 60
    assert poll_interval(now + timedelta(minutes=20), now) == 20
    assert poll_interval(now + timedelta(minutes=4), now) == 5
    assert poll_interval(now + timedelta(seconds=45), now) == 2


def test_past_end_is_polled_immediately():
    now = datetime.now(timezone.utc)
    assert poll_interval(now - timedelta(seconds=1), now) == 2
