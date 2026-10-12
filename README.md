# Lumi Cloud backend — FastAPI + MongoDB

Backend đặt cạnh `lumi_smartscreen` và `lumi_ui`. API v1 giữ hợp đồng app/firmware;
MongoDB lưu tài khoản, thiết bị, cấu hình widget, metadata ảnh và cache.

## Chạy với MongoDB Atlas

Môi trường `.venv` đã cài dependencies. `.env` local đã được chuẩn bị và được ignore.
Mẫu Atlas nằm ở `.env.example` và `.env.atlas.example`.

1. Trong Atlas tạo database user, cho quyền `readWrite` trên DB `lumi`, cấu hình
   Network Access cho IP máy/server chạy backend, lấy URI tại **Connect → Drivers**.
2. Điền URI vào `LUMI_MONGODB_URI` trong `.env`. Dùng mật khẩu database user;
   không dùng Atlas API key. Percent-encode ký tự đặc biệt của username/password.
3. Giữ `LUMI_MONGODB_DATABASE=lumi`; khởi tạo collection, validator, unique/TTL index:

```powershell
cd D:/Work/arduno/lumi_be
.venv/Scripts/python.exe -m lumi_be.cli init-db
.venv/Scripts/python.exe -m uvicorn lumi_be.app:create_app --factory --host 127.0.0.1 --port 8000
```

Chạy worker ở terminal khác:

```powershell
.venv/Scripts/python.exe -m lumi_be.worker
```

Swagger: http://127.0.0.1:8000/docs. OpenAPI xuất ở `docs/openapi.json`.
`/health/live` kiểm tra tiến trình; `/health/ready` kiểm tra MongoDB và schema marker.
API không tạo index trên mỗi request hoặc tự sửa DB khi startup. Chạy `init-db`
một lần trước khi chạy API; chạy lại được an toàn khi triển khai bản tương thích.
Máy mới: tạo Python 3.11+ venv, `python -m pip install -c requirements.lock -e ".[dev]"`.

## Cấu trúc

```text
src/lumi_be/
  app.py                  app factory, body bounds, errors, health
  config.py               env, URI secret, giới hạn pool
  db.py                   PyMongo pool, validators, indexes, schema version
  models.py               Pydantic documents, UUID string IDs
  deps.py / security.py   user/device bearer auth, Argon2
  schemas.py              request, layout schema-1, HLP bounds
  services.py             ownership, atomic revision, rate limit, CDN policy
  routers/auth.py         đăng ký/login/logout
  routers/app_devices.py  device control theo owner
  routers/device.py       API nhỏ dành cho firmware
  routers/widgets.py      nguồn dữ liệu và cấu hình widget
  providers/registry.py   source ID, params schema, TTL, canonical cache key
  providers/widgets.py    scheduler/cache, bỏ qua widget ẩn/tắt
  providers/weather.py    Open-Meteo adapter
  worker.py               một scheduler process
  cli.py                  init DB, cấp khóa, đăng ký file, cleanup
tests/                    kiểm thử trên MongoDB thật
docs/DATABASE.md           mô hình document, indexes và Atlas
docs/FIRMWARE_CONTRACT.md  hợp đồng tích hợp firmware/offline
```

## Account và pairing

- App đăng ký/login email/password qua `/api/v1/auth/register`, `/login`.
  Password 12–128 ký tự, hash Argon2. Session server giữ SHA-256 token, hết hạn,
  logout thu hồi ngay; TTL MongoDB chỉ dọn record, API vẫn kiểm tra hạn độc lập.
- CLI `provision-device --serial CYD-000001` cấp UUID + khóa riêng, ghi vào
  `.secrets/`; không in token. Nạp khóa vào NVS từng mạch, không nhúng vào firmware chung.
- Firmware sau thao tác ghép tại mạch gọi `/api/v1/device/pairing`, nhận mã 10 ký tự
  và hạn 5 phút. App gọi `/api/v1/me/devices/claim` với UUID + mã.
- MongoDB update có điều kiện owner trống + code + expiry: một mạch chỉ có một chủ,
  mã dùng một lần. UUID/MAC/serial không phải mật khẩu.
- Mọi sửa config/layout/widget đều kiểm tra owner + `expected_revision` nguyên tử.
  Unbind xóa config/layout/widgets/nav của riêng mạch trong một update.
- User token `usr_` và device token `dev_` tách quyền. Firmware không gửi ID để
  chọn mạch, backend suy ra từ khóa. `rotate-device-key --device-id UUID` đổi khóa.

Email verification, password reset, MFA và UI đăng nhập còn cần thêm trước khi mở
dịch vụ công khai. Chưa tích hợp cloud client/pairing screen vào firmware/app.

## API chính

| Client | API | Mục đích |
| --- | --- | --- |
| App | `/api/v1/auth/register`, `/login`, `/me`, `/logout`, `/logout-all` | Account/session |
| App | `GET /api/v1/me/devices`, `POST .../claim` | Danh sách và ghép |
| App | `GET`, `DELETE .../devices/{id}` | Đọc/unbind |
| App | `PUT .../{id}/config`, `/layout`, `/widgets/{type}` | Cấu hình theo revision |
| App | `GET /api/v1/widget-sources`, `GET .../{id}/widgets` | Catalog và binding |
| App | `POST .../{id}/navigation` | Full HLP snapshot, TTL 3s |
| App | `GET .../{id}/assets` | Catalog ảnh được phép |
| Mạch | `POST /api/v1/device/hello`, `/pairing` | Heartbeat/ghép |
| Mạch | `GET /api/v1/device/snapshot` | JSON <=512 byte |
| Mạch | `GET /api/v1/device/widgets/{type}` | Cache chuẩn hóa <=512 byte, ETag/304 |
| Mạch | `GET /api/v1/device/layout` | <=4096 byte, ETag/304 |
| Mạch | `GET /api/v1/device/navigation` | HLP <=512 byte, 204 khi stale |
| Mạch | `GET /api/v1/device/assets/{id}` | Metadata <=1024 byte |

Cả app và mạch gửi `Authorization: Bearer ...`. Field ngoài schema bị từ chối;
error không echo password hoặc payload. Body giới hạn 16 KiB.

## Nguồn widget và ảnh

Widget lưu source ID + params trong document device. Backend giữ URL/provider
adapter; firmware nhận dữ liệu chuẩn hóa. Cache theo source version + params giống
nhau, dùng chung giữa các mạch cho nguồn công khai; không lưu lịch sử polling.
Weather đã dùng Open-Meteo; clock/calendar local; navigation API riêng; stock/bot
chưa có upstream thật. Worker cache weather một giờ, stale tối đa 24 giờ, lỗi giữ
last-good cache. Một scheduler instance; không chạy worker trong từng API process.

Ảnh binary nằm ở cloud/CDN, MongoDB chỉ giữ metadata và quyền chọn.
Ảnh `rgb565-le` 320×240, đúng 153,600 byte; SHA-256. CLI `publish-asset`
kiểm tra file thật; chỉ chấp nhận HTTPS host trong `LUMI_CDN_HOSTS`.
Catalog asset riêng tư không bảo vệ file CDN: cần signed URL/origin auth khi làm
ảnh private. URL CDN/khóa provider không phải URI MongoDB.

OTA vẫn `app-assisted`; app đọc manifest trực tiếp từ GitHub Releases. Backend không
lưu metadata firmware và không tham gia build, publish hay kiểm tra update.
Cloud outage phải giữ config/layout cuối hợp lệ, clock, tài nguyên fallback, SPP/LAN;
nav hết hạn 3000ms. Cloud không bỏ được bước cấp Wi-Fi lần đầu.
Firmware/app và COM5 không bị thay đổi trong lần chuyển DB này.

Script local giữ `.whfw`/manifest trên GitHub Releases. Hướng dẫn đầy đủ nằm tại
`D:/Work/arduno/lumi_smartscreen/docs/RELEASE_FIRMWARE.md`.

## Deploy và kiểm thử

Docker Compose production dùng MongoDB Atlas ngoài và gồm init, API, worker, Caddy.
Caddy là service duy nhất mở cổng 80/443 và tự quản lý HTTPS; API port 8000 chỉ tồn
tại trong Docker network. Copy `.env.production.example` thành `.env`, điền domain,
Atlas URI, CORS origins rồi chạy `sh deploy.sh`. Hướng dẫn DNS, firewall, Atlas,
update và rollback nằm tại `docs/DEPLOY_VPS.md`. Pipeline test/deploy qua GitHub
Actions được hướng dẫn từ đầu tại `docs/CICD.md`.

```powershell
$env:LUMI_TEST_MONGODB_URI="mongodb://127.0.0.1:27018"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests tools
.venv/Scripts/python.exe -m ruff format --check src tests tools
.venv/Scripts/python.exe -m pip check
```

Tests cần MongoDB thật; tạo/xóa riêng DB `lumi_test_<uuid>`, không dùng DB app.
Runtime MongoDB Community đã chuẩn bị local ở `.tools/`, port 27018, bind loopback.
`python -m lumi_be.cli cleanup` có thể dọn expiry ngay; Mongo TTL dọn tự động bất đồng bộ.
Source SQL trước chuyển được backup ở `data/backend-before-mongodb-*.zip`, DB SQLite
`data/lumi.db` giữ nguyên. Khi kiểm tra, DB cũ chưa có account/device nào để nhập.
