#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/emiratesauction
export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
  systemctl enable --now docker
fi

# This VPS has ~2 GB RAM. A small swap file prevents the kernel from killing
# Redis/Celery during image builds or short traffic spikes.
if ! swapon --show --noheadings | grep -q .; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=10 >/dev/null
printf 'vm.swappiness=10\n' >/etc/sysctl.d/99-emiratesauction.conf

if [ ! -d "$APP_DIR/.git" ]; then git clone https://github.com/hsdarestani/emiratesauction.git "$APP_DIR"; fi
cd "$APP_DIR"
git fetch origin main
git reset --hard origin/main
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/change-me/$(openssl rand -hex 24)/g" .env
fi

# Free memory before BuildKit starts. Scheduled work is idempotent and resumes
# automatically after compose comes back up.
docker compose stop worker closing-worker beat >/dev/null 2>&1 || true

docker compose up -d --build --remove-orphans
docker compose exec -T backend python -c 'from app.migrations import migrate; migrate()'

if [ ! -f /etc/letsencrypt/live/emiratesauction.smarbiz.sbs/fullchain.pem ]; then
  cat >/etc/nginx/sites-available/emiratesauction.smarbiz.sbs <<'NGINX'
server {
  listen 80;
  server_name emiratesauction.smarbiz.sbs;
  location / { proxy_pass http://127.0.0.1:8087; proxy_set_header Host $host; }
}
NGINX
  ln -sfn /etc/nginx/sites-available/emiratesauction.smarbiz.sbs /etc/nginx/sites-enabled/emiratesauction.smarbiz.sbs
  nginx -t && systemctl reload nginx
  certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email -d emiratesauction.smarbiz.sbs
fi
install -m 644 deploy/nginx.conf /etc/nginx/sites-available/emiratesauction.smarbiz.sbs
ln -sfn /etc/nginx/sites-available/emiratesauction.smarbiz.sbs /etc/nginx/sites-enabled/emiratesauction.smarbiz.sbs
nginx -t && systemctl reload nginx

# Do not run a full Emirates collection or AutoScout batch synchronously during
# deploy. Beat schedules those jobs after the server has settled.
for attempt in $(seq 1 12); do
  if curl -fsS -m 5 http://127.0.0.1:8087/ >/dev/null && curl -fsS -m 5 http://127.0.0.1:8087/api/health >/dev/null; then
    # HTTP can be healthy while the Celery workers are crash-looping. That used
    # to let deployments pass even though no auctions could advance to finished.
    sleep 5
    workers_ok=1
    for service in worker closing-worker beat; do
      cid="$(docker compose ps -q "$service")"
      if [ -z "$cid" ] || [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)" != "true" ]; then
        echo "Required service is not running: $service" >&2
        workers_ok=0
      fi
    done
    if [ "$workers_ok" -ne 1 ]; then
      docker compose ps >&2 || true
      docker compose logs --tail=160 worker closing-worker beat >&2 || true
      exit 1
    fi

    echo "Production health check passed"
    docker compose exec -T backend python - <<'PY' || true
from sqlalchemy import desc, func, select
from app.database import SessionLocal
from app.models import Vehicle
with SessionLocal() as db:
    total = db.scalar(select(func.count()).select_from(Vehicle)) or 0
    active = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.status.in_(("active", "ending")))) or 0
    finished = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.status == "finished", Vehicle.price_data_valid.is_(True))) or 0
    unreliable = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.status == "finished_unreliable")) or 0
    print({"vehicles": total, "active": active, "finished_valid": finished, "finished_unreliable": unreliable})

    sources = db.execute(
        select(Vehicle.price_source, func.count())
        .where(Vehicle.status == "finished_unreliable")
        .group_by(Vehicle.price_source)
        .order_by(desc(func.count()))
    ).all()
    print("unreliable_by_source", [(source, count) for source, count in sources])

    gap_stats = db.execute(
        select(
            func.min(Vehicle.monitoring_gap_seconds),
            func.avg(Vehicle.monitoring_gap_seconds),
            func.max(Vehicle.monitoring_gap_seconds),
        ).where(Vehicle.status == "finished_unreliable")
    ).one()
    print("unreliable_gap_seconds", {"min": gap_stats[0], "avg": float(gap_stats[1]) if gap_stats[1] is not None else None, "max": gap_stats[2]})

    latest = db.scalars(
        select(Vehicle)
        .where(Vehicle.status == "finished_unreliable")
        .order_by(desc(Vehicle.finished_at))
        .limit(12)
    ).all()
    for v in latest:
        print("unreliable_sample", {
            "lot": v.lot_id,
            "title": v.title,
            "end": v.auction_end_time.isoformat() if v.auction_end_time else None,
            "finished": v.finished_at.isoformat() if v.finished_at else None,
            "last_seen": v.last_live_bid_at.isoformat() if v.last_live_bid_at else None,
            "gap": v.monitoring_gap_seconds,
            "source": v.price_source,
            "last_bid": float(v.last_live_bid) if v.last_live_bid is not None else None,
        })
PY
    free -h
    docker compose ps
    exit 0
  fi
  sleep 5
done

echo "Production health check failed" >&2
docker compose ps >&2 || true
docker compose logs --tail=160 backend frontend worker closing-worker beat >&2 || true
exit 1
