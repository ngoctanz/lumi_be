"""Operator-only catalogue publishing. No account or device enrollment."""

import argparse
import hashlib
import time
from pathlib import Path

from .cloud_images import CloudinaryImageStore, prepare_image
from .config import Settings
from .db import database, initialize, utc_date
from .models import Asset
from .services import validate_cdn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser("cleanup")
    for name in ("publish-asset", "publish-image"):
        command = commands.add_parser(name)
        command.add_argument("--file", required=True, type=Path)
        command.add_argument("--name", required=True)
        if name == "publish-asset":
            command.add_argument("--url", required=True)
    args = parser.parse_args()
    settings = Settings()
    client, db = database(settings)
    try:
        if args.command == "init-db":
            initialize(db)
            print("Public data schema initialized; legacy user data left untouched")
        elif args.command == "cleanup":
            for name in ("rate_limits", "provider_cache"):
                db[name].delete_many({"expire_at": {"$lte": utc_date(time.time())}})
            print("Expired public caches cleaned")
        else:
            if not 1 <= len(args.name) <= 64 or any(ord(c) < 32 for c in args.name):
                parser.error("Name must contain 1-64 printable characters")
            if not args.file.is_file() or args.file.stat().st_size > settings.image_upload_max_bytes:
                parser.error("File missing or oversized")
            payload = args.file.read_bytes()
            image = None
            if args.command == "publish-image":
                image = prepare_image(payload)
                payload = image.raw
            elif len(payload) != 153600:
                parser.error("Expected 320x240 RGB565-LE")
            asset = Asset(
                model="WazeHUD-CYD",
                name=args.name,
                url="pending",
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                created_at=int(time.time()),
            )
            if image:
                store = CloudinaryImageStore(settings)
                uploaded = store.upload("public", asset.id, image)
                asset.url, asset.preview_url = uploaded.raw_url, uploaded.preview_url
                asset.provider = "cloudinary"
                asset.provider_id, asset.preview_provider_id = (
                    uploaded.raw_public_id,
                    uploaded.preview_public_id,
                )
            else:
                asset.url = validate_cdn(args.url, settings)
            try:
                db.assets.insert_one(asset.mongo())
            except Exception:
                if image:
                    store.delete(asset)
                raise
            print(f"Public asset published: {asset.id}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
