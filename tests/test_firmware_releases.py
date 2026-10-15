import json
from types import SimpleNamespace

import httpx

from lumi_be.config import Settings
from lumi_be.firmware_releases import GithubFirmwareCatalog

REPOSITORY = "ngoctanz/lumi_espcore"
PACKAGE_URL = f"https://github.com/{REPOSITORY}/releases/download/v2.1.0/wazehud-2.1.0.whfw"
MANIFEST_URL = f"https://github.com/{REPOSITORY}/releases/download/v2.1.0/manifest.json"


def manifest(build=41, channel="stable"):
    return {
        "schema": 1,
        "model": "WazeHUD-CYD",
        "version": "2.1.0",
        "build": build,
        "channel": channel,
        "size": 2700000,
        "sha256": "a" * 64,
        "url": PACKAGE_URL,
    }


def catalog(handler):
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27018",
        github_firmware_repository=REPOSITORY,
        github_release_cache_seconds=300,
    )
    return GithubFirmwareCatalog(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_catalog_selects_highest_build_and_caches_github_requests():
    calls = []
    releases = [
        {
            "draft": False,
            "prerelease": False,
            "html_url": f"https://github.com/{REPOSITORY}/releases/tag/v2.1.0",
            "published_at": "2026-10-12T00:00:00Z",
            "assets": [
                {"id": 99, "name": "manifest.json", "browser_download_url": MANIFEST_URL},
                {"id": 123, "name": "wazehud-2.1.0.whfw", "size": 2700000},
            ],
        }
    ]

    def handler(request):
        calls.append(str(request.url))
        body = releases if request.url.path.endswith("/releases") else manifest()
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )

    service = catalog(handler)
    result = service.latest("WazeHUD-CYD", "stable", 40)
    assert result["update_available"] is True
    assert result["release"]["build"] == 41
    assert result["release"]["asset_id"] == 123
    assert service.latest("WazeHUD-CYD", "stable", 41)["update_available"] is False
    assert len(calls) == 2


def test_catalog_rejects_manifest_download_outside_configured_github_repo():
    releases = [
        {
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "manifest.json",
                    "browser_download_url": MANIFEST_URL,
                }
            ],
        }
    ]

    def handler(request):
        body = (
            releases
            if request.url.path.endswith("/releases")
            else {**manifest(), "url": "https://evil.example/firmware.whfw"}
        )
        return httpx.Response(200, content=json.dumps(body).encode())

    assert catalog(handler).latest("WazeHUD-CYD", "stable", 0)["release"] is None


def test_release_endpoint_is_public_and_reports_availability(app, client):
    app.state.firmware_catalog = SimpleNamespace(
        latest=lambda model, channel, current: {
            "update_available": current < 41,
            "current_build": current,
            "release": manifest(),
            "source": "github",
        }
    )
    path = "/api/v1/firmware/releases/latest?model=WazeHUD-CYD&channel=stable&current_build=40"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["update_available"] is True


def test_release_package_download_is_public(app, client):
    class Package:
        def iter_bytes(self, _chunk_size):
            yield b"firmware-package"

        def close(self):
            pass

    app.state.firmware_catalog = SimpleNamespace(
        open_package=lambda asset_id: (Package(), f"firmware-{asset_id}.whfw", 16)
    )
    response = client.get("/api/v1/firmware/releases/assets/123")
    assert response.status_code == 200
    assert response.content == b"firmware-package"
    assert response.headers["content-disposition"] == 'attachment; filename="firmware-123.whfw"'
