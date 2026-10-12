"""Exercise firmware HTTP contracts using an operator-provisioned credential file."""

import argparse
import json
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:8000")
parser.add_argument("--credentials", type=Path, required=True)
parser.add_argument("--pair", action="store_true", help="Request a short-lived pairing code")
args = parser.parse_args()
credentials = json.loads(args.credentials.read_text())
with httpx.Client(
    base_url=args.base_url,
    headers={"Authorization": f"Bearer {credentials['device_token']}"},
    timeout=10,
    trust_env=False,
) as client:
    hello = client.post("/api/v1/device/hello", json={"firmware": "simulator-0.1", "build": 0})
    hello.raise_for_status()
    print("Device:", hello.json()["id"], "paired:", hello.json()["paired"])
    snapshot = client.get("/api/v1/device/snapshot")
    snapshot.raise_for_status()
    if len(snapshot.content) > 512:
        raise RuntimeError("Snapshot exceeds firmware receive contract")
    print("Snapshot bytes:", len(snapshot.content), "revision:", snapshot.json()["rev"])
    if args.pair:
        response = client.post("/api/v1/device/pairing")
        response.raise_for_status()
        print("One-time pairing code:", response.json()["code"], "expires:", response.json()["expires_at"])
