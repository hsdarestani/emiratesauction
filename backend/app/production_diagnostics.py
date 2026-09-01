from datetime import datetime, timedelta, timezone

from redis import Redis
from sqlalchemy import func, select

from .config import settings
from .database import SessionLocal
from .models import Vehicle
from .scraper import fetch_live
from .worker import CLOSING_HEALTH_KEY, CLOSING_OBSERVATION_MAX_GAP_SECONDS, CLOSING_WINDOW_SECONDS


def main():
    now = datetime.now(timezone.utc)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    print({"diagnostic_now_utc": now.isoformat()})
    try:
        health = redis.hgetall(CLOSING_HEALTH_KEY)
    except Exception as exc:
        health = {"redis_error": repr(exc)}
    print("closing_health", health)

    with SessionLocal() as db:
        status_counts = db.execute(
            select(Vehicle.status, func.count()).group_by(Vehicle.status).order_by(Vehicle.status)
        ).all()
        print("status_counts", [(status, count) for status, count in status_counts])

        finished_valid = db.scalar(
            select(func.count()).select_from(Vehicle).where(
                Vehicle.status == "finished", Vehicle.price_data_valid.is_(True)
            )
        ) or 0
        finished_unreliable = db.scalar(
            select(func.count()).select_from(Vehicle).where(Vehicle.status == "finished_unreliable")
        ) or 0
        print({"finished_valid": finished_valid, "finished_unreliable": finished_unreliable})

        closing_rows = db.scalars(
            select(Vehicle).where(
                Vehicle.is_tracked.is_(True),
                Vehicle.status.in_(("active", "ending")),
                Vehicle.auction_end_time.is_not(None),
                Vehicle.auction_end_time <= now + timedelta(seconds=CLOSING_WINDOW_SECONDS),
            ).order_by(Vehicle.auction_end_time).limit(2000)
        ).all()
        overdue = [v for v in closing_rows if v.auction_end_time and v.auction_end_time <= now]
        stale = []
        for v in closing_rows:
            age = None if not v.last_live_bid_at else (now - v.last_live_bid_at).total_seconds()
            if age is None or age > CLOSING_OBSERVATION_MAX_GAP_SECONDS:
                stale.append(v)
        print({
            "closing_window": len(closing_rows),
            "overdue_active_or_ending": len(overdue),
            "closing_stale_observations": len(stale),
        })

        for v in closing_rows[:15]:
            print("closing_sample", {
                "lot": v.lot_id,
                "title": v.title,
                "status": v.status,
                "end": v.auction_end_time.isoformat() if v.auction_end_time else None,
                "seconds_to_end": round((v.auction_end_time - now).total_seconds()) if v.auction_end_time else None,
                "last_seen": v.last_live_bid_at.isoformat() if v.last_live_bid_at else None,
                "last_seen_age": round((now - v.last_live_bid_at).total_seconds(), 1) if v.last_live_bid_at else None,
                "last_bid": float(v.last_live_bid or 0),
            })

        next_rows = db.scalars(
            select(Vehicle).where(
                Vehicle.is_tracked.is_(True),
                Vehicle.status.in_(("active", "ending")),
                Vehicle.auction_end_time.is_not(None),
                Vehicle.auction_end_time > now + timedelta(seconds=CLOSING_WINDOW_SECONDS),
            ).order_by(Vehicle.auction_end_time).limit(10)
        ).all()
        for v in next_rows:
            print("next_end", {
                "lot": v.lot_id,
                "title": v.title,
                "end": v.auction_end_time.isoformat(),
                "seconds_to_end": round((v.auction_end_time - now).total_seconds()),
            })

    inventory = fetch_live()
    active_ids = {str(item.get("Lot") or item.get("Id")) for item in inventory}
    print({"official_active_feed_size": len(inventory)})
    if closing_rows:
        present = sum(1 for v in closing_rows if v.lot_id in active_ids)
        absent = len(closing_rows) - present
        print({"closing_present_in_official_feed": present, "closing_absent_from_official_feed": absent})


if __name__ == "__main__":
    main()
