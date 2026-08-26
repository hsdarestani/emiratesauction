from celery import Celery
from .config import settings
from .database import Base, SessionLocal, engine
from .services import collect
from .autoscout import compare_closed

celery = Celery("auction", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.beat_schedule = {
    "poll-live-auctions": {"task": "poll_live_auctions", "schedule": settings.poll_interval_seconds},
    "compare-finished-with-germany": {"task": "compare_finished_with_germany", "schedule": 1800},
}


@celery.task(name="poll_live_auctions")
def poll_live_auctions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [v.lot_id for v in collect(db, settings.poll_limit)]


@celery.task(name="compare_finished_with_germany")
def compare_finished_with_germany():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        return [{"vehicle_id": row.vehicle_id, "status": row.status} for row in compare_closed(db)]
