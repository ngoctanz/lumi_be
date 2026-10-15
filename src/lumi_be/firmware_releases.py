import re
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

MODEL = "WazeHUD-CYD"
MAX_PACKAGE_SIZE = 0x300000 + 128
MAX_MANIFEST_SIZE = 8192
MAX_RELEASE_INDEX_SIZE = 524288
VERSION = re.compile(r"^[A-Za-z0-9.+_-]{1,31}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class FirmwareReleaseError(RuntimeError):
    pass


def _github_url(value, repository):
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.hostname != "github.com" or parsed.port not in (None, 443):
        return False
    return parsed.path.startswith(f"/{repository}/releases/download/")


def normalize_manifest(value, repository):
    if not isinstance(value, dict) or set(value) - {
        "schema",
        "model",
        "version",
        "build",
        "channel",
        "size",
        "sha256",
        "url",
    }:
        return None
    if (
        value.get("schema") != 1
        or value.get("model") != MODEL
        or not isinstance(value.get("version"), str)
        or not VERSION.fullmatch(value["version"])
        or not isinstance(value.get("build"), int)
        or isinstance(value.get("build"), bool)
        or not 1 <= value["build"] <= 2147483647
        or value.get("channel") not in ("stable", "beta")
        or not isinstance(value.get("size"), int)
        or isinstance(value.get("size"), bool)
        or not 384 <= value["size"] <= MAX_PACKAGE_SIZE
        or not isinstance(value.get("sha256"), str)
        or not SHA256.fullmatch(value["sha256"])
        or not _github_url(value.get("url"), repository)
    ):
        return None
    return {
        key: value[key] for key in ("schema", "model", "version", "build", "channel", "size", "sha256", "url")
    }


class GithubFirmwareCatalog:
    def __init__(self, settings, client=None, clock=time.monotonic):
        self.repository = settings.github_firmware_repository
        self.cache_seconds = settings.github_release_cache_seconds
        self.clock = clock
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "lumi-cloud-firmware-catalog/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_api_token:
            headers["Authorization"] = f"Bearer {settings.github_api_token.get_secret_value()}"
        self.client = client or httpx.Client(timeout=8, follow_redirects=True, headers=headers)
        self.owns_client = client is None
        self.cache = {}
        self.lock = threading.Lock()

    def close(self):
        if self.owns_client:
            self.client.close()

    def _get_json(self, url, maximum, headers=None):
        try:
            response = self.client.get(url, headers=headers)
        except httpx.HTTPError as error:
            raise FirmwareReleaseError("GitHub releases are unavailable") from error
        if response.status_code != 200 or len(response.content) > maximum:
            raise FirmwareReleaseError("GitHub releases are unavailable")
        try:
            return response.json()
        except ValueError as error:
            raise FirmwareReleaseError("GitHub returned invalid release data") from error

    def _load(self, channel):
        releases = self._get_json(
            f"https://api.github.com/repos/{self.repository}/releases?per_page=20",
            MAX_RELEASE_INDEX_SIZE,
        )
        if not isinstance(releases, list):
            raise FirmwareReleaseError("GitHub returned invalid release data")
        candidates = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") is not False:
                continue
            if channel == "stable" and release.get("prerelease") is not False:
                continue
            assets = release.get("assets")
            if not isinstance(assets, list):
                continue
            asset = next(
                (item for item in assets if isinstance(item, dict) and item.get("name") == "manifest.json"),
                None,
            )
            manifest_url = asset.get("browser_download_url") if asset else None
            manifest_asset_id = asset.get("id") if asset else None
            if (
                not _github_url(manifest_url, self.repository)
                or not isinstance(manifest_asset_id, int)
                or isinstance(manifest_asset_id, bool)
            ):
                continue
            manifest_api_url = (
                f"https://api.github.com/repos/{self.repository}/releases/assets/{manifest_asset_id}"
            )
            manifest = normalize_manifest(
                self._get_json(
                    manifest_api_url,
                    MAX_MANIFEST_SIZE,
                    headers={"Accept": "application/octet-stream"},
                ),
                self.repository,
            )
            if not manifest or (channel == "stable" and manifest["channel"] != "stable"):
                continue
            if channel == "beta" and manifest["channel"] not in ("beta", "stable"):
                continue
            package_name = PurePosixPath(urlparse(manifest["url"]).path).name
            package_asset = next(
                (
                    item
                    for item in assets
                    if isinstance(item, dict)
                    and item.get("name") == package_name
                    and isinstance(item.get("id"), int)
                    and not isinstance(item.get("id"), bool)
                    and item.get("size") == manifest["size"]
                ),
                None,
            )
            if not package_asset:
                continue
            candidates.append(
                {
                    **manifest,
                    "asset_id": package_asset["id"],
                    "manifest_url": manifest_url,
                    "release_url": release.get("html_url")
                    if _github_url(release.get("html_url"), self.repository)
                    else None,
                    "published_at": release.get("published_at"),
                }
            )
        return max(candidates, key=lambda item: item["build"], default=None)

    def open_package(self, asset_id):
        api_url = f"https://api.github.com/repos/{self.repository}/releases/assets/{asset_id}"
        metadata = self._get_json(api_url, MAX_MANIFEST_SIZE)
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != asset_id
            or not isinstance(metadata.get("name"), str)
            or not metadata["name"].endswith(".whfw")
            or not isinstance(metadata.get("size"), int)
            or not 384 <= metadata["size"] <= MAX_PACKAGE_SIZE
            or not _github_url(metadata.get("browser_download_url"), self.repository)
        ):
            raise FirmwareReleaseError("GitHub package metadata is invalid")
        try:
            request = self.client.build_request(
                "GET", api_url, headers={"Accept": "application/octet-stream"}
            )
            response = self.client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise FirmwareReleaseError("GitHub package is unavailable") from error
        if response.status_code != 200:
            response.close()
            raise FirmwareReleaseError("GitHub package is unavailable")
        length = response.headers.get("Content-Length")
        if length and (not length.isdigit() or int(length) != metadata["size"]):
            response.close()
            raise FirmwareReleaseError("GitHub package size changed")
        return response, metadata["name"], metadata["size"]

    def latest(self, model, channel, current_build):
        if model != MODEL:
            return {"update_available": False, "current_build": current_build, "release": None}
        key = (model, channel)
        now = self.clock()
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > now:
                release = cached[1]
            else:
                try:
                    release = self._load(channel)
                    self.cache[key] = (now + self.cache_seconds, release)
                except FirmwareReleaseError:
                    if not cached:
                        raise
                    release = cached[1]
        return {
            "update_available": bool(release and release["build"] > current_build),
            "current_build": current_build,
            "release": release,
            "source": "github",
        }
