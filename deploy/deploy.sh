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
    echo "Production health check passed"
    free -h
    docker compose ps
    exit 0
  fi
  sleep 5
done

echo "Production health check failed" >&2
docker compose ps >&2 || true
docker compose logs --tail=80 backend frontend >&2 || true
exit 1
