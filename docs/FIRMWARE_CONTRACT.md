# Firmware cloud contract v1

This backend is a working control plane. The ESP32 and companion have not yet
been migrated to it. Introduce the cloud adapter behind an explicit build/runtime
setting, provision per-device credentials, then test with a real HTTPS hostname.
Do not replace the deployed local paths before the adapter passes hardware tests.

## Identity and pairing

An operator provisions a UUID + random 256-bit device credential. Store the
credential in per-device NVS, outside the common firmware image. MAC/serial is
inventory metadata, not authentication. The backend only stores the token hash.

Every request sends `Authorization: Bearer dev_...` over certificate-verified
HTTPS. Never log this header, pairing codes, Wi-Fi credentials or raw HLP frames.
Firmware must never use `setInsecure()` for cloud account/claim traffic.

After a physical pairing action, call POST `/api/v1/device/pairing` and display
the returned ID + one-time code as text/QR. The cloud cannot itself prove a button
press; possession of the unique device credential is the device trust boundary.
Do not request new pairing codes on every boot. An already owned device requires
an owner-initiated unbind. A user claims the exact UUID and code atomically.

Provisioning Wi-Fi is still local on first use and when router credentials change.
Cloud pairing/account control becomes available only after Internet connectivity.
No firmware credentials have been provisioned to COM5 by this backend task.

## Small HTTP responses

Boot/heartbeat (every 60 seconds):

```http
POST /api/v1/device/hello
Authorization: Bearer dev_...
Content-Type: application/json

{"firmware":"2.0.0-alpha.31-ota-diag","build":31,"applied_revision":0}
```

The server derives identity from the bearer credential; body `device_id` is
rejected. Heartbeat reports the actually applied revision only after successful
validation and local persistence. It must not acknowledge merely received data.

Poll `/api/v1/device/snapshot` approximately every 30 seconds. Full snapshots
contain `v`, `t`, server epoch `time`, desired `rev`, `paired`, `poll_ms`, `cfg`,
selected asset ID, layout-presence flag and normalized weather (or null).
Successful snapshot/hello/firmware responses and device errors are <=512 bytes.
No account email, owner ID, credentials or image bytes are included.

When `rev` changes, retrieve `/device/layout` (<=4096 bytes) using ETag, validate
schema 1 and 320x240 geometry, then apply/persist through the existing ownership
rules. HLP messages remain <=512 bytes including newline: the HTTP layout body
is a separate contract, not a larger HLP frame. Cloud config does not contain
Wi-Fi credentials or override the local Wi-Fi mode.

Weather uses compact normalized fields `temp`, `feels`, `hi`, `lo`, `hum`, `wind`,
`c` and `at`. `c`: 0 clear, 1 partly cloudy, 2 cloudy, 3 rain, 4 storm. Provider
JSON, key management, schedules and conversions stay on the backend. A null
weather field means keep the local last known valid value with its timestamp.

## Navigation and local precedence

An authenticated owner posts a full HLP snapshot to `/me/devices/{id}/navigation`.
Fields missing from that snapshot reset to defaults; this is not a patch.
The backend validates a complete frame at <=512 UTF-8 bytes including newline.
It stores only the latest frame per device and expires it after 3000 ms.

Firmware polls `/device/navigation` only while cloud navigation is active,
initially at 250–500 ms. HTTP 200 contains the HLP frame directly. The
`X-Frame-Age-Ms` header indicates its age at the cloud: subtract that age from the
local freshness budget, rather than treating an old frame as newly generated.
204 means no fresh cloud navigation. This initial transport is polling, not a
real-time WebSocket/MQTT event bus; evaluate throughput before larger fleets.

SPP retains precedence over simultaneous Wi-Fi traffic. Preserve existing local
LAN input as a fallback. Do not freeze the last turn/alert on cloud disconnection.
`avg=1` continues to mean a Vietnam no-passing zone, never a speed camera.

## Assets and memory

JSON contains only asset IDs. Retrieve asset metadata separately (<=1024 bytes):
HTTPS URL, SHA-256, raw size, dimensions and encoding. Initial wallpaper contract
is RGB565 little-endian, 320x240, exactly 153600 bytes. The backend does not make
the current firmware dynamically render these assets: that adapter is future work.

The current 4 MiB partition layout has NO filesystem after adding recovery. Do
not simply add an image downloader or allocate a 153600-byte RAM framebuffer.
A measured next step can remove three optional compiled wallpapers (460800 pixel
bytes), keep one fallback, shrink the app slot after verifying the build, and
reserve a dedicated cache partition. For example, only if the core image fits:

| Region | Offset | Planned size |
| --- | --- | --- |
| app0 | 0x10000 | 0x280000 (2.5 MiB) |
| asset cache | 0x290000 | 0x80000 (512 KiB) |
| recovery | 0x310000 | 0xE0000 (896 KiB), unchanged address |

This is a plan, not the installed partition table. The existing updater checks
a 3 MiB main slot and must also be revised before that transition. Stage two
153600-byte cache entries in flash; verify the complete digest before switching
the active metadata. Map/read raw pixels from flash using a bounded decoder or
fixed descriptors; never download into the currently displayed cache entry.
Invalidate private cloud assets on an explicit ownership reset, not on a timeout.
Provisioning/storage capacity must be tested before releasing such a build.

Keep one built-in wallpaper, weather icons, clock/calendar, fallback layout,
last valid settings and local transport. Cloud failure should select those paths
without rebuilding the entire LVGL tree. Backend migration should remove upstream
provider parsers/keys from firmware, but TLS/network buffers still cost RAM.
Measure largest heap block, stack high water marks and radio coexistence on board.

## OTA and reliability

Firmware release discovery is outside this API. The companion app reads manifests
directly from GitHub Releases and compares monotonic build numbers. The backend has
no firmware release collection, publisher credential or update endpoint.

OTA remains app-assisted and compatible with alpha.31 `.whfw` packages. Recovery
firmware and local fallback stay installed. Download binaries in bounded blocks,
verify size/digest, and only switch after complete validation.
The current package format has checksum integrity but not publisher signatures;
add signatures before unattended remote installation.

On timeout/5xx/429, continue rendering, use exponential backoff + jitter (5s,
10s, 20s ... capped at 5m), and respect Retry-After. 401 should enter a credential
repair state, not an infinite rapid retry. Never clear Wi-Fi/NVS or disable local
HUD because cloud authentication or DNS is unavailable. Keep cloud requests in a
low-priority provider task; callbacks publish bounded state and render owns LVGL.

## Integration gates

1. Deploy a real HTTPS origin and configured CDN; provision a distinct key per mạch.
2. Add opt-in cloud client and app account/claim UI; retain current local UI.
3. Check two physical devices with two users, stale config, claim/unbind and key rotation.
4. Exercise router loss, cloud 5xx, expired tokens, wrong model/digest, reset mid-cache
   and mid-update, and verify local fallback + navigation expiry.
5. Measure RAM/flash before removing optional assets or changing partitions.
