import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from pymongo.errors import DuplicateKeyError

from .config import Settings
from .db import database, initialize, utc_date
from .models import Asset, Device
from .providers.weather import refresh
from .security import digest, token
from .services import validate_cdn


def private_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="MongoDB operator tools; no public admin enrollment endpoint"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    provision = sub.add_parser("provision-device")
    provision.add_argument("--serial", required=True)
    rotate = sub.add_parser("rotate-device-key")
    rotate.add_argument("--device-id", required=True)
    asset = sub.add_parser("publish-asset")
    asset.add_argument("--file", required=True, type=Path)
    asset.add_argument("--url", required=True)
    asset.add_argument("--name", required=True)
    asset.add_argument("--owner-id")
    sub.add_parser("refresh-weather")
    sub.add_parser("cleanup")
    args = parser.parse_args()
    settings = Settings()
    client, db = database(settings)
    try:
        if args.command == "init-db":
            initialize(db)
            print("MongoDB collections, validators and indexes initialized")
        elif args.command == "refresh-weather":
            print(json.dumps(refresh(db)))
        elif args.command in ("provision-device", "rotate-device-key"):
            secret = token("dev")
            if args.command == "provision-device":
                if not 1 <= len(args.serial) <= 64 or any(ord(c) < 32 for c in args.serial):
                    parser.error("Serial must contain 1-64 printable characters")
                device = Device(serial=args.serial, token_hash=digest(secret), created_at=int(time.time()))
                # Write the recoverable private credential before making it active.
                path = Path(".secrets") / f"device-{device.id}-{time.time_ns()}.json"
                private_file(path, {"device_id": device.id, "model": device.model, "device_token": secret})
                db.devices.insert_one(device.mongo())
            else:
                row = db.devices.find_one({"_id": args.device_id})
                if not row:
                    parser.error("Device not found")
                device = Device.model_validate(row)
                path = Path(".secrets") / f"device-{device.id}-{time.time_ns()}.json"
                private_file(path, {"device_id": device.id, "model": device.model, "device_token": secret})
                db.devices.update_one(
                    {"_id": device.id},
                    {"$set": {"token_hash": digest(secret), "pairing_hash": None, "pairing_expires_at": 0}},
                )
            print(f"Device {device.id}; private credentials saved to {path}. Never publish this file.")
        elif args.command == "publish-asset":
            if args.file.stat().st_size != 320 * 240 * 2 or not 1 <= len(args.name) <= 64:
                parser.error("Expected 320x240 RGB565-LE raw data and a name up to 64 characters")
            if args.owner_id and not db.users.find_one({"_id": args.owner_id}):
                parser.error("Asset owner does not exist")
            item = Asset(
                owner_id=args.owner_id,
                model="WazeHUD-CYD",
                name=args.name,
                url=validate_cdn(args.url, settings),
                size=args.file.stat().st_size,
                sha256=hashlib.sha256(args.file.read_bytes()).hexdigest(),
            )
            db.assets.insert_one(item.mongo())
            print(f"Asset {item.id} registered; upload the raw file to CDN separately.")
        elif args.command == "cleanup":
            now = utc_date(time.time())
            for name in ("login_sessions", "rate_limits", "provider_cache"):
                db[name].delete_many({"expire_at": {"$lte": now}})
            print("Expired sessions, limiter records and provider cache cleaned")
    except DuplicateKeyError:
        parser.error("A unique serial or key already exists")
    finally:
        client.close()


if __name__ == "__main__":
    main()
