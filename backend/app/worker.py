from datetime import datetime, timedelta, timezone

from celery import Celery
from redis import Redis
from sqlalchemy import or_, select
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Vehicle
from .services import collect, poll_interval, update_one, verify_final_price
from .autoscout import compare_closed, compare_live

# Tasks are fire-and-forget. Keeping every Celery result in the same Redis
# instance as the broker caused Redis to grow until the small VPS hit OOM.
celery = Celery("auction", broker=settings.redis_url)
celery.conf.beat_schedule = {
    "poll-live-auctions": {"task": "poll_live_auctions", "schedule": settings.poll_interval_seconds},
    "dispatch-due-auctions": {"task": "dispatch_due_auctions", "schedule": 2.0},
    "recover-recent-unreliable": {"task": "dispatch_unreliable_recovery", "schedule": 60.0},
    "compare-live-with-germany": {"task": "compare_live_with_germany", "schedule": 300},
    "compare-finished-with-germany": {"task": "compare_finished_with_germany", "schedule": 1800},
}
celery.conf.task_acks_late = True
celery.conf.task_reject_on_worker_lost = True
celery.conf.worker_prefetch_multiplier = 1
celery.conf.task_ignore_result = True
celery.conf.task_store_errors_even_if_ignored = False
celery.conf.broker_connection_retry_on_startup = True
redis = Redis.from_url(settings.redis_url)

CLOSING_WINDOW_SECONDS = 5 * 60
QUEUE_MARKER_TTL_SECONDS = 45


def closing_queue_for(vehicle, now):
    if not vehicle.auction_end_time:
        return "live"
    remaining = (vehicle.auction_end_time - now).total_seconds()
    return "closing" if remaining <= CLOSING_WINDOW_SECONDS else "live"


@celery.task(name="poll_live_auctions")
def poll_live_auctions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [v.lot_id for v in collect(db, settings.poll_limit)]


@celery.task(name="dispatch_due_auctions")
def dispatch_due_auctions():
    now = datetime.now(timezone.utc)
    enqueued = 0
    with SessionLocal() as db:
        # Closing auctions are ordered first and get their own high-concurrency
        # worker queue. The Redis marker prevents hundreds of duplicate tasks
        # building up while one HTTP request is already queued or running.
        due = db.scalars(select(Vehicle).where(
            Vehicle.is_tracked.is_(True),
            Vehicle.status.in_(("active", "ending")),
            or_(Vehicle.next_poll_at.is_(None), Vehicle.next_poll_at <= now),
        ).order_by(Vehicle.auction_end_time).limit(250)).all()
        for vehicle in due:
            marker = f"auction:queued:{vehicle.id}"
            if not redis.set(marker, "1", nx=True, ex=QUEUE_MARKER_TTL_SECONDS):
                continue
            interval = poll_interval(vehicle.auction_end_time, now)
            vehicle.next_poll_at = now + timedelta(seconds=interval)
            try:
                poll_vehicle.apply_async((vehicle.id,), queue=closing_queue_for(vehicle, now))
                enqueued += 1
            except Exception:
                redis.delete(marker)
                vehicle.next_poll_at = now

        finalizing = db.scalars(select(Vehicle).where(
            Vehicle.is_tracked.is_(True), Vehicle.status == "finalizing",
            or_(Vehicle.next_poll_at.is_(None), Vehicle.next_poll_at <= now),
        ).limit(20)).all()
        for vehicle in finalizing:
            vehicle.next_poll_at = now + timedelta(minutes=5)
            verify_final_price_task.apply_async((vehicle.id, 0), queue="finalize")
            enqueued += 1
        db.commit()
    return enqueued


@celery.task(name="poll_vehicle")
def poll_vehicle(vehicle_id):
    marker = f"auction:queued:{vehicle_id}"
    lock = redis.lock(f"auction:poll:{vehicle_id}", timeout=25, blocking_timeout=0)
    acquired = lock.acquire(blocking=False)
    if not acquired:
        return "duplicate"
    try:
        with SessionLocal() as db:
            vehicle = db.get(Vehicle, vehicle_id)
            if not vehicle or vehicle.status not in ("active", "ending"):
                return "stale"
            state = update_one(db, vehicle)
            if state == "finished_valid":
                compare_finished_vehicle.delay(vehicle_id)
            return state
    finally:
        try: lock.release()
        except Exception: pass
        redis.delete(marker)


@celery.task(name="dispatch_unreliable_recovery")
def dispatch_unreliable_recovery():
    """Retry only recent finishes that were hidden because the old queue was late."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=48)
    queued = 0
    with SessionLocal() as db:
        rows = db.scalars(select(Vehicle).where(
            Vehicle.status == "finished_unreliable",
            Vehicle.finished_at.is_not(None),
            Vehicle.finished_at >= cutoff,
        ).order_by(Vehicle.finished_at.desc()).limit(20)).all()
        for vehicle in rows:
            marker = f"auction:recover:{vehicle.id}"
            if not redis.set(marker, "1", nx=True, ex=120):
                continue
            try:
                recover_unreliable_vehicle.apply_async((vehicle.id,), queue="closing")
                queued += 1
            except Exception:
                redis.delete(marker)
    return queued


@celery.task(name="recover_unreliable_vehicle")
def recover_unreliable_vehicle(vehicle_id):
    marker = f"auction:recover:{vehicle_id}"
    try:
        with SessionLocal() as db:
            vehicle = db.get(Vehicle, vehicle_id)
            if not vehicle or vehicle.status != "finished_unreliable":
                return "stale"
            try:
                state = update_one(db, vehicle)
            except Exception:
                return "retry_later"
            if state == "finished_valid":
                compare_finished_vehicle.delay(vehicle_id)
            return state
    finally:
        redis.delete(marker)


VERIFY_DELAYS = (2, 5, 15, 30, 60, 300, 900, 1800)


@celery.task(name="verify_final_price")
def verify_final_price_task(vehicle_id, attempt=0):
    lock = redis.lock(f"auction:finalize:{vehicle_id}", timeout=40, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return "duplicate"
    try:
        with SessionLocal() as db:
            vehicle = db.get(Vehicle, vehicle_id)
            if not vehicle or vehicle.status != "finalizing":
                return "stale"
            try:
                outcome = verify_final_price(db, vehicle)
                if outcome is True:
                    compare_finished_vehicle.delay(vehicle_id)
                    return "verified"
                if outcome is None:
                    return "live"
            except Exception:
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
        if not vehicle or vehicle.status != "finished" or not vehicle.price_data_valid:
            return "nicht_valide"
        return compare_vehicle(db, vehicle).status


@celery.task(name="compare_live_with_germany")
def compare_live_with_germany():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [{"vehicle_id": row.vehicle_id, "status": row.status} for row in compare_live(db)]


@celery.task(name="compare_finished_with_germany")
def compare_finished_with_germany():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [{"vehicle_id": row.vehicle_id, "status": row.status} for row in compare_closed(db)]
