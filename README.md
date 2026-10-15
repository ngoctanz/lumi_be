# Lumi data backend

FastAPI + MongoDB read-only cloud services for Lumi HUD. ESP stores its own
configuration; the phone talks directly to ESP. No user accounts, ownership
mapping, cloud device-control APIs or factory cloud credentials are required.

## Setup

Copy .env.example to .env; configure MongoDB, allowed app origins, optional
Cloudinary credentials, and the GitHub firmware repository/token.
Never commit .env, private keys, image operator files or database dumps.

Local:
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -m lumi_be.cli init-db
.venv/Scripts/python -m uvicorn lumi_be.app:create_app --factory --no-access-log

VPS:
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs --tail=100 api init

The compose init service applies schema/indexes before API readiness.
Cloudflare Tunnel may reach 127.0.0.1:18082; existing .env/DNS/Tunnel settings stay
in place. Old worker containers are retired with --remove-orphans. No automatic
data deletion is part of this migration.

## APIs

See docs/FIRMWARE_CONTRACT.md. Public weather is normalized, cached and bounded.
Public image catalogue excludes all former private images. Firmware releases are
validated against the configured GitHub repository. All consumer routes are GET;
admin image publishing is CLI-only and never exposed anonymously.

The phone's continuous GPS stream stays local. Weather may use an occasional
coarse location, with no account/device identity or persistent movement history.

## Publishing images

Use the operator CLI publish-image to convert/upload a local image to Cloudinary,
or publish-asset to register a preconverted 320x240 RGB565 file on an allowed CDN.
Mount operator input files read-only into Docker and run the command in the API
image. Do not add a public upload endpoint without a separate abuse-control design.

## Verification

Tests use a real MongoDB test instance at 127.0.0.1:27018 by default.
Set LUMI_TEST_MONGODB_URI to another dedicated test database server if necessary.
Run pytest, ruff check src tests, and ruff format --check src tests.
Tests create isolated databases and never use production collections.
