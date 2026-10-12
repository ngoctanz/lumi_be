#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.production.example to .env and fill real values." >&2
  exit 1
fi

docker compose config --quiet
docker compose build --pull api
docker compose up -d --remove-orphans

attempt=0
until [ "$attempt" -ge 30 ]; do
  status="$(docker compose ps --format json api 2>/dev/null || true)"
  if printf '%s' "$status" | grep -q '"Health":"healthy"'; then
    proxy="$(docker compose ps --format json caddy 2>/dev/null || true)"
    if printf '%s' "$proxy" | grep -q '"State":"running"'; then
      echo "Lumi API is healthy at https://${LUMI_DOMAIN:-$(sed -n 's/^LUMI_DOMAIN=//p' .env)}"
      exit 0
    fi
  fi
  attempt=$((attempt + 1))
  sleep 2
done

echo "API did not become healthy. Recent logs:" >&2
docker compose logs --tail=100 api init caddy >&2
exit 1
