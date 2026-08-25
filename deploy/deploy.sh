#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/emiratesauction
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
  systemctl enable --now docker
fi
if [ ! -d "$APP_DIR/.git" ]; then git clone https://github.com/hsdarestani/emiratesauction.git "$APP_DIR"; fi
cd "$APP_DIR"
git fetch origin main
git reset --hard origin/main
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/change-me/$(openssl rand -hex 24)/g" .env
fi
docker compose up -d --build --remove-orphans
docker compose exec -T backend python -c 'from app.database import Base,engine; Base.metadata.create_all(engine)'
docker compose exec -T backend python -c 'from app.database import SessionLocal; from app.services import collect; db=SessionLocal(); print([v.lot_id for v in collect(db,10)]); db.close()'
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
