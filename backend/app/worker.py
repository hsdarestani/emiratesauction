from datetime import datetime, timedelta, timezone
from decimal import Decimal

from celery import Celery
from redis import Redis
from sqlalchemy import or_, select

from .autoscout import compare_closed, compare_live
from .config import settings
from .database import Base, SessionLocal, engine
from .models import AuctionResult, Vehicle
from .scraper import fetch_live, normalize
from .services import collect, poll_interval, update_one, upsert_vehicle, verify_final_price

# Tasks are fire-and-forget. Keeping every Celery result in the same Redis
# instance as the broker caused Redis to grow until the small VPS hit OOM.
celery = Celery("auction", broker=settings.redis_url)
celery.conf.beat_schedule = {
    "poll-live-auctions": {"task": "poll_live_auctions", "schedule": settings.poll_interval_seconds},
    # One shared API snapshot every two seconds owns the final five minutes.
    # This avoids hundreds of HTML detail requests and captures the last bid
    # before Emirates Auction removes the lot from the active feed.
    "poll-closing-feed": {
        "task": "poll_closing_feed",
        "schedule": 2.0,
        "options": {"queue": "closing"},
    },
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
# The official Vehicles feed currently takes roughly five seconds per full
# snapshot in production. One healthy post-EndDate absence is therefore enough
# when we observed the lot immediately beforehand; requiring two misses made the
# evidence artificially stale before finalization.
CLOSING_MISS_CONFIRMATIONS = 1
CLOSING_OBSERVATION_MAX_GAP_SECONDS = 12
# The active inventory can legitimately fall well below 100 between auction
# batches. HTTP/JSON failures already raise in fetch_live(); only reject an
# implausibly tiny successful payload here so a low inventory does not freeze
# every vehicle in the "ending" state.
MIN_HEALTHY_ACTIVE_FEED_SIZE = 5


def closing_queue_for(vehicle, now):
    if not vehicle.auction_end_time:
        return "live"
    remaining = (vehicle.auction_end_time - now).total_seconds()
    return "closing" if remaining <= CLOSING_WINDOW_SECONDS else "live"


def closing_observation_is_valid(last_seen_at, observed_at):
    if not last_seen_at:
        return False
    gap = (observed_at - last_seen_at).total_seconds()
    return 0 <= gap <= CLOSING_OBSERVATION_MAX_GAP_SECONDS


def _finalize_from_closing_feed(db, vehicle, observed_at):
    """Trust the last API bid only when the lot was observed immediately before disappearance."""
    last_seen_at = vehicle.last_live_bid_at
    price = Decimal(vehicle.last_live_bid or vehicle.current_bid or 0)
    gap = None
    if last_seen_at:
        gap = max(0, round((observed_at - last_seen_at).total_seconds()))

    valid = bool(price > 0 and closing_observation_is_valid(last_seen_at, observed_at))

    vehicle.finished_at = vehicle.finished_at or observed_at
    vehicle.monitoring_gap_seconds = gap
    vehicle.price_data_valid = valid
    vehicle.price_source = (
        "emirates_live_api_closing_feed"
        if valid
        else "closing_feed_monitoring_gap"
    )
    vehicle.status = "finished" if valid else "finished_unreliable"

    result = db.scalar(select(AuctionResult).where(AuctionResult.vehicle_id == vehicle.id))
    if not result:
        result = AuctionResult(vehicle_id=vehicle.id, final_bid=price if valid else None)
        db.add(result)
    result.final_bid = price if valid else None
    result.final_price_status = "observed" if valid else "unreliable"
    db.commit()
    return "finished_valid" if valid else "finished_unreliable"


@celery.task(name="poll_live_auctions")
def poll_live_auctions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [v.lot_id for v in collect(db, settings.poll_limit)]


@celery.task(name="poll_closing_feed")
def poll_closing_feed():
    """Monitor every auction in its final five minutes from one official API snapshot.

    The Vehicles API only exposes active lots. While a lot is present we store
    its latest bid and any extended EndDate. Once its known end time has passed
    and it is absent from a healthy snapshot, the immediately preceding API bid
    becomes our observed end price, provided that observation is still recent.
    """
    # fetch_live() has a 30 second request timeout. Keep the Redis lock alive
    # longer than that so a slow upstream response cannot create overlapping
    # closing snapshots that race each other.
    lock = redis.lock("auction:closing-feed", timeout=40, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return "duplicate"

    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            closing = db.scalars(
                select(Vehicle)
                .where(
                    Vehicle.is_tracked.is_(True),
                    Vehicle.status.in_(("active", "ending")),
                    Vehicle.auction_end_time.is_not(None),
                    Vehicle.auction_end_time <= now + timedelta(seconds=CLOSING_WINDOW_SECONDS),
                )
                .order_by(Vehicle.auction_end_time)
                .limit(250)
            ).all()

            if not closing:
                return {"tracked": 0, "updated": 0, "finished": 0}

            inventory = fetch_live()
            # Never interpret an API outage/truncated payload as hundreds of auctions ending.
            if len(inventory) < MIN_HEALTHY_ACTIVE_FEED_SIZE:
                return {
                    "tracked": len(closing),
                    "updated": 0,
                    "finished": 0,
                    "feed_size": len(inventory),
                    "status": "feed_too_small",
                }

            active = {
                str(item.get("Lot") or item.get("Id")): item
                for item in inventory
            }
            updated = 0
            finished = 0

            for vehicle in closing:
                listing = active.get(vehicle.lot_id)
                miss_key = f"auction:closing-miss:{vehicle.id}"

                if listing is not None:
                    redis.delete(miss_key)
                    payload = normalize(listing)
                    payload["status"] = "ending"
                    current = upsert_vehicle(db, payload, tracked=True)
                    current.next_poll_at = now + timedelta(seconds=2)
                    db.commit()
                    updated += 1
                    continue

                # Absence before the currently known EndDate is not an ending signal.
                # This also protects against transient feed omissions.
                if vehicle.auction_end_time and now < vehicle.auction_end_time:
                    redis.delete(miss_key)
                    continue

                misses = redis.incr(miss_key)
                redis.expire(miss_key, 20)
                if misses < CLOSING_MISS_CONFIRMATIONS:
                    continue

                state = _finalize_from_closing_feed(db, vehicle, now)
                redis.delete(miss_key)
                if state == "finished_valid":
                    compare_finished_vehicle.delay(vehicle.id)
                finished += 1

            return {
                "tracked": len(closing),
                "updated": updated,
                "finished": finished,
                "feed_size": len(inventory),
            }
    finally:
        try:
            lock.release()
        except Exception:
            pass


@celery.task(name="dispatch_due_auctions")
def dispatch_due_auctions():
    now = datetime.now(timezone.utc)
    enqueued = 0
    closing_cutoff = now + timedelta(seconds=CLOSING_WINDOW_SECONDS)
    with SessionLocal() as db:
        # The shared closing feed owns the final five minutes. Individual detail
        # polling remains only for auctions farther away from the end so HTML
        # rate limits cannot make us miss a closing bid.
        due = db.scalars(select(Vehicle).where(
            Vehicle.is_tracked.is_(True),
            Vehicle.status.in_(("active", "ending")),
            or_(Vehicle.next_poll_at.is_(None), Vehicle.next_poll_at <= now),
            or_(Vehicle.auction_end_time.is_(None), Vehicle.auction_end_time > closing_cutoff),
        ).order_by(Vehicle.auction_end_time).limit(250)).all()
        for vehicle in due:
            marker = f"auction:queued:{vehicle.id}"
            if not redis.set(marker, "1", nx=True, ex=QUEUE_MARKER_TTL_SECONDS):
                continue
            interval = poll_interval(vehicle.auction_end_time, now)
            vehicle.next_poll_at = now + timedelta(seconds=interval)
            try:
                poll_vehicle.apply_async((vehicle.id,), queue="live")
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
            # Never let an HTML detail request race the API closing feed.
            now = datetime.now(timezone.utc)
            if (
                vehicle.auction_end_time
                and vehicle.auction_end_time <= now + timedelta(seconds=CLOSING_WINDOW_SECONDS)
            ):
                return "closing_feed_owned"
            state = update_one(db, vehicle)
            if state == "finished_valid":
                compare_finished_vehicle.delay(vehicle_id)
            return state
    finally:
        try:
            lock.release()
        except Exception:
            pass
        redis.delete(marker)


@celery.task(name="dispatch_unreliable_recovery")
def dispatch_unreliable_recovery():
    """Retry recent hidden finishes against the official expired detail page."""
    now = datetime.now(timezone.utc)
    # Keep a wide enough recovery window to repair the rows affected by the
    # historical Dubai-vs-UTC EndDate bug, without turning this into an
    # unbounded archive crawler.
    cutoff = now - timedelta(days=14)
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
                # Recovery performs HTML detail requests and must never compete
                # with the two-second closing feed for its dedicated queue.
                recover_unreliable_vehicle.apply_async((vehicle.id,), queue="finalize")
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
        try:
            lock.release()
        except Exception:
            pass


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
