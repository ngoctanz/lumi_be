# Local-first data API contract (firmware build 43)

Cloud provides data, not device control. There are no account, enrollment,
claim, device configuration, navigation-relay or image-upload HTTP endpoints.
The ESP owns Wi-Fi, layout, appearance and weather coordinates in local NVS.
Old account/device Mongo documents are preserved but not read by active routes.

## Public read-only APIs

- GET /api/v1/widget-sources: versioned catalogue of installed trusted adapters.
- GET /api/v1/widgets/weather?latitude=10.12&longitude=106.12: <=512-byte
  normalized weather, schema 1, state fresh/stale/pending, at/retry_seconds/data.
- GET /api/v1/assets?model=WazeHUD-CYD: at most 50 public assets per page.
  owner_id is strictly null; former private assets are never published.
- GET /api/v1/firmware/releases/latest and /assets/{id}: validated release
  discovery/download. GitHub credentials remain server-side.

Weather requests share a coarse (~1 km) TTL cache. No user/device ID or GPS
history is stored. A Mongo lease and a stale-time predicate limit concurrent
upstream fetches. Upstream host is fixed in the adapter; consumers cannot submit
URLs, scripts or database queries. Public read/upstream/download rate limits
bound cost. 429 includes Retry-After plus retry_seconds; clients must back off.
Provider failures keep stale data until its maximum age; otherwise return pending.

## Image publishing

Public image uploads are disabled. Operators publish over the CLI:
python -m lumi_be.cli publish-image --file wallpaper.png --name Sunset
or publish-asset --file wallpaper.rgb565 --url https://... --name Sunset.
CLI files must be explicitly mounted read-only into the container. Cloudinary
publishes fixed 320x240 RGB565 + preview variants. SDK/API secrets never go to ESP.
The current firmware still uses built-in wallpapers; fetching/rendering dynamic
Cloudinary pixels requires a separate bounded asset renderer and is not enabled.

## Deployment

Deploy the backend before using firmware build 43's weather data endpoint.
Run init-db (schema 3); it creates only public asset/cache/limiter indexes and
never deletes legacy data. Keep legacy collections private for operator review.
Use docker compose up -d --build --remove-orphans to retire the old worker.
App origin remains https://localhost on Android, http://localhost:5173 for dev.
Production disables Uvicorn access logging so weather query coordinates are not
written to its access log. Reverse-proxy logging is managed separately.

Pairing and encryption are device-local; see the firmware repository's
docs/DEVICE_ONBOARDING.md for the exact transport and migration requirements.
