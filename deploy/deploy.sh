#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/emiratesauction
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
install -m 644 deploy/nginx.conf /etc/nginx/sites-available/emiratesauction.smarbiz.sbs
ln -sfn /etc/nginx/sites-available/emiratesauction.smarbiz.sbs /etc/nginx/sites-enabled/emiratesauction.smarbiz.sbs
nginx -t && systemctl reload nginx

