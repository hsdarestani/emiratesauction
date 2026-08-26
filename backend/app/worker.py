from datetime import datetime, timedelta, timezone

from celery import Celery
from redis import Redis
from sqlalchemy import or_, select
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Vehicle
from .services import collect, poll_interval, update_one, verify_final_price
from .autoscout import compare_closed

celery = Celery("auction", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.beat_schedule = {
    "poll-live-auctions": {"task": "poll_live_auctions", "schedule": settings.poll_interval_seconds},
    "dispatch-due-auctions": {"task": "dispatch_due_auctions", "schedule": 2.0},
    "compare-finished-with-germany": {"task": "compare_finished_with_germany", "schedule": 1800},
}
celery.conf.task_acks_late = True
celery.conf.task_reject_on_worker_lost = True
redis = Redis.from_url(settings.redis_url)


@celery.task(name="poll_live_auctions")
def poll_live_auctions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [v.lot_id for v in collect(db, settings.poll_limit)]


@celery.task(name="dispatch_due_auctions")
def dispatch_due_auctions():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = db.scalars(select(Vehicle).where(
            Vehicle.is_tracked.is_(True),
            Vehicle.status.in_(("active", "ending")),
            or_(Vehicle.next_poll_at.is_(None), Vehicle.next_poll_at <= now),
        ).order_by(Vehicle.auction_end_time).limit(50)).all()
        for vehicle in due:
            vehicle.next_poll_at = now + timedelta(seconds=poll_interval(vehicle.auction_end_time, now))
            poll_vehicle.apply_async((vehicle.id,), queue="live")
        finalizing = db.scalars(select(Vehicle).where(
            Vehicle.is_tracked.is_(True), Vehicle.status == "finalizing",
            or_(Vehicle.next_poll_at.is_(None), Vehicle.next_poll_at <= now),
        ).limit(20)).all()
        for vehicle in finalizing:
            vehicle.next_poll_at = now + timedelta(minutes=5)
            verify_final_price_task.apply_async((vehicle.id, 0), queue="finalize")
        db.commit()
    return len(due) + len(finalizing)


@celery.task(name="poll_vehicle")
def poll_vehicle(vehicle_id):
    lock = redis.lock(f"auction:poll:{vehicle_id}", timeout=25, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return "duplicate"
    try:
        with SessionLocal() as db:
            vehicle = db.get(Vehicle, vehicle_id)
            if not vehicle or vehicle.status not in ("active", "ending"):
                return "stale"
            state = update_one(db, vehicle)
            if state == "finished":
                verify_final_price_task.apply_async((vehicle_id, 0), countdown=2, queue="finalize")
            return state
    finally:
        try: lock.release()
        except Exception: pass


VERIFY_DELAYS = (2, 5, 15, 30, 60, 300, 900, 1800)


@celery.task(name="verify_final_price")
def verify_final_price_task(vehicle_id, attempt=0):
    lock = redis.lock(f"auction:finalize:{vehicle_id}", timeout=40, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return "duplicate"
    try:
        with SessionLocal() as db:
            vehicle = db.get(Vehicle, vehicle_id)
            if not vehicle or vehicle.status == "verified":
                return "already_verified"
            try:
                if verify_final_price(db, vehicle):
                    compare_finished_vehicle.delay(vehicle_id)
                    return "verified"
            except Exception:
                # The retry schedule below also covers timeouts and temporary
                # Emirates Auction errors; the unverified value stays hidden.
                pass
            delay = VERIFY_DELAYS[min(attempt + 1, len(VERIFY_DELAYS) - 1)]
            verify_final_price_task.apply_async((vehicle_id, attempt + 1), countdown=delay, queue="finalize")
            return f"retry_in_{delay}s"
    finally:
        try: lock.release()
        except Exception: pass


@celery.task(name="compare_finished_vehicle")
def compare_finished_vehicle(vehicle_id):
    from .autoscout import compare_vehicle
    with SessionLocal() as db:
        vehicle = db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.status != "verified":
            return "not_verified"
        return compare_vehicle(db, vehicle).status


@celery.task(name="compare_finished_with_germany")
def compare_finished_with_germany():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [{"vehicle_id": row.vehicle_id, "status": row.status} for row in compare_closed(db)]
