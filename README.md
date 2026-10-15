# Lumi data backend

Backend FastAPI + MongoDB cho Lumi/WazeHUD. Đây là **public, read-only data
plane**, không phải nơi sở hữu hay điều khiển thiết bị. ESP giữ Wi-Fi/layout/config;
app nói chuyện trực tiếp với ESP. Production origin: `https://api.fdoc.app`.

## Trách nhiệm

- Chuẩn hóa weather để firmware/app không phụ thuộc trực tiếp provider.
- Cache, chống request stampede, giữ last-good và giới hạn lưu lượng.
- Trả catalogue ảnh public; Mongo giữ metadata, binary ở Cloudinary/CDN.
- Đọc GitHub Releases, validate manifest và proxy package firmware cho app.
- Cung cấp health, OpenAPI, Docker Compose và CI/CD VPS.

Không có user account, login, device enrollment, owner mapping, remote config hay
GPS history. Collection legacy nếu còn trong DB không được route hiện tại công khai.

```text
App / ESP ── HTTPS ──> FastAPI
                         ├── Open-Meteo: fetch + normalize
                         ├── MongoDB: cache + asset metadata
                         ├── Cloudinary/CDN: binary ảnh
                         └── GitHub Releases: manifest + .whfw
```

## Các luồng dữ liệu

### Weather

1. Client gọi `GET /api/v1/widgets/weather`.
2. Tọa độ được validate và làm tròn 2 chữ số; backend không lưu lịch sử di chuyển.
3. Key cache tạo từ source version + params canonical.
4. Cache miss/stale giành lease ngắn trong Mongo để tránh nhiều request đánh upstream.
5. Open-Meteo được normalize, payload provider giới hạn 256 byte.
6. Lỗi provider giữ stale payload còn trong max-age; không có dữ liệu thì trả `pending`.
7. Response hoàn chỉnh được kiểm budget HLP 512 byte.

### Ảnh công khai

1. Operator dùng CLI; không có anonymous upload API.
2. `publish-image` decode/crop 320 × 240, tạo RGB565-LE và preview.
3. File lên Cloudinary; Mongo giữ URL, SHA-256, size và provider ID.
4. Insert DB lỗi thì object Cloudinary vừa upload được dọn.
5. `GET /api/v1/assets` chỉ trả record `owner_id: null`.
6. `publish-asset` đăng ký file RGB565 153,600 byte trên CDN allowlist.

### Firmware release

1. App gọi `GET /api/v1/firmware/releases/latest` theo model/channel/build.
2. Service đọc repository cấu hình và cache metadata GitHub trong memory.
3. Manifest sai model/schema/build/size/SHA hoặc sai repository bị từ chối.
4. Backend trả metadata/asset ID, không build hay release firmware.
5. `GET /api/v1/firmware/releases/assets/{id}` stream package theo chunk.
6. App kiểm package lần nữa trước khi upload recovery.

GitHub token chỉ nằm server-side và chủ yếu tăng rate limit.

## API hiện tại

| Method | Path | Mục đích |
| --- | --- | --- |
| GET | `/health/live` | Process còn sống |
| GET | `/health/ready` | Mongo ping + schema version 3 |
| GET | `/api/v1/widget-sources` | Source/schema widget |
| GET | `/api/v1/widgets/weather` | Weather normalized/cache |
| GET | `/api/v1/assets` | Catalogue ảnh public |
| GET | `/api/v1/firmware/releases/latest` | Release theo model/channel/build |
| GET | `/api/v1/firmware/releases/assets/{id}` | Stream package GitHub |

Swagger ở `/docs`. Xem [contract firmware](docs/FIRMWARE_CONTRACT.md) và
[OpenAPI snapshot](docs/openapi.json).

## MongoDB schema

Schema version **3**.

| Collection | Nội dung | Giới hạn |
| --- | --- | --- |
| `assets` | Metadata asset public/legacy | model + owner scope, provider ID unique |
| `provider_cache` | Payload normalized + lease | deterministic key, TTL |
| `rate_limits` | Counter scope/window | atomic increment, TTL |
| `metadata` | Marker schema | readiness yêu cầu version 3 |

`python -m lumi_be.cli init-db` tạo/cập nhật validator và index idempotent. API
startup không tự migrate. Binary ảnh/firmware không nằm trong MongoDB.

## Tổ chức code

```text
src/lumi_be/
├── app.py                    FastAPI factory, middleware, CORS, health
├── config.py                 LUMI_* settings + validation
├── db.py                     Mongo pool, schema/index, readiness
├── errors.py                 error envelope + request ID
├── services.py               limiter, wire budget, asset/CDN guard
├── firmware_releases.py      GitHub release/package validation
├── cloud_images.py           image conversion + Cloudinary
├── providers/                registry và Open-Meteo adapter
├── routers/                  public data và firmware routes
├── models.py                 bounded persistence models
└── cli.py                    init, cleanup, publish
tests/                        integration/contract tests
tools/                        OpenAPI và VPS/DNS helpers
compose.yaml                  init + API + Caddy
deploy.sh                     build/up/health gate
Caddyfile                     TLS reverse proxy
.github/workflows/backend.yml validate + optional deploy
```

PyMongo sync chạy trong FastAPI threadpool. Mỗi process giữ một MongoClient/pool
bounded, không tạo client theo request.

## Cấu hình môi trường

Copy `.env.example` thành `.env` và không commit file thật.

| Biến | Ý nghĩa |
| --- | --- |
| `LUMI_MONGODB_URI` | URI Mongo/Atlas có TLS và DB credential |
| `LUMI_MONGODB_DATABASE` | Database, mặc định `lumi` |
| `LUMI_MONGODB_POOL_SIZE` | Pool 2–50, mặc định 10 |
| `LUMI_CORS_ORIGINS` | JSON list origin app/web |
| `LUMI_CDN_HOSTS` | JSON list host asset được phép |
| `LUMI_REQUEST_MAX_BYTES` | Request body chung |
| `LUMI_IMAGE_UPLOAD_MAX_BYTES` | File operator tối đa |
| `LUMI_CLOUDINARY_*` | Cloud name, key, secret, folder; phải đủ bộ |
| `LUMI_GITHUB_FIRMWARE_REPOSITORY` | Repository owner/name |
| `LUMI_GITHUB_API_TOKEN` | Token server-side tùy chọn |
| `LUMI_GITHUB_RELEASE_CACHE_SECONDS` | TTL metadata GitHub |
| `LUMI_DOMAIN`, `ACME_EMAIL` | Domain và email ACME cho Caddy |
| `LUMI_TUNNEL_PORT` | Loopback cho Cloudflare Tunnel, mặc định 18082 |

Atlas API key không phải Mongo password. Percent-encode ký tự đặc biệt trong
username/password. Không log URI/token/secret.

## Chạy local

Python 3.11+, khuyến nghị 3.12.

```powershell
python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -c requirements.lock -e \".[dev]\"
Copy-Item .env.example .env
.\\.venv\\Scripts\\python.exe -m lumi_be.cli init-db
.\\.venv\\Scripts\\python.exe -m uvicorn lumi_be.app:create_app --factory --reload
```

Trong Command Prompt dùng `copy .env.example .env` thay `Copy-Item`.

## Kiểm tra code

Tests dùng MongoDB test riêng, mặc định `127.0.0.1:27018`; không trỏ production.

```powershell
$env:LUMI_TEST_MONGODB_URI=\"mongodb://127.0.0.1:27018\"
.\\.venv\\Scripts\\python.exe -m pytest
.\\.venv\\Scripts\\python.exe -m ruff check src tests tools
.\\.venv\\Scripts\\python.exe -m ruff format --check src tests tools
.\\.venv\\Scripts\\python.exe tools\\export_openapi.py --check
docker compose --env-file .env.production.example config --quiet
docker build -t lumi-be:local .
```

## Publish ảnh

```sh
docker compose run --rm --no-deps \\
  -v \"$PWD/operator-input:/input:ro\" \\
  init python -m lumi_be.cli publish-image \\
  --file /input/wallpaper.png --name \"Wallpaper\"
```

Không thêm public upload endpoint nếu chưa có auth, quota, moderation và abuse model.

## Deploy VPS

```sh
git clone https://github.com/ngoctanz/lumi_be.git /opt/lumi-be
cd /opt/lumi-be
cp .env.production.example .env
nano .env
chmod 600 .env
sh deploy.sh
```

`deploy.sh` validate Compose, build image, chạy init schema, start service và chờ
API healthy + Caddy running. Compose dùng read-only filesystem, non-root user,
`no-new-privileges`, tmpfs nhỏ và log rotation.

```sh
docker compose ps
docker compose logs --tail=200 api init caddy
curl -fsS https://api.fdoc.app/health/live
curl -fsS https://api.fdoc.app/health/ready
```

Mongo nằm ở Atlas; `docker compose down` không xóa dữ liệu Atlas. Không dùng
`down -v` nếu không chủ động xóa certificate/config volume.

Cloudflare Tunnel có thể trỏ hostname tới `http://127.0.0.1:18082`. Nếu dùng Caddy
public trực tiếp, DNS trỏ VPS và mở 80/443. Giữ một đường ingress production rõ ràng.

## CI/CD

Workflow `.github/workflows/backend.yml`:

1. **validate:** cài dependency, Ruff format/lint, pip check, validate Compose, build image.
2. **deploy:** chỉ chạy khi variable `VPS_DEPLOY_ENABLED=true`, sau validate, trên
   push `main` hoặc manual dispatch.
3. SSH dùng key riêng, strict known_hosts, `git pull --ff-only` và `sh deploy.sh`.

Secrets: `VPS_SSH_HOST`, `VPS_SSH_USER`, `VPS_SSH_PRIVATE_KEY`,
`VPS_KNOWN_HOSTS`. Variables: `VPS_SSH_PORT`, `VPS_DEPLOY_ENABLED`.
Mongo/Cloudinary/GitHub token chỉ ở `/opt/lumi-be/.env`.

Xem [deploy VPS](docs/DEPLOY_VPS.md) và [CI/CD](docs/CICD.md).

## Ràng buộc an toàn và ổn định

- Public API chỉ GET; ghi catalogue qua CLI operator.
- CORS dùng origin cụ thể, không dùng wildcard.
- Request/response/upstream/pool/timeout/rate đều bounded.
- Asset URL phải HTTPS và host thuộc allowlist.
- Provider trả schema nội bộ ổn định; đổi upstream không buộc firmware đổi.
- Lỗi cloud không được thay đổi cấu hình ESP.
- Không lưu GPS liên tục, Wi-Fi password, pairing key hay device ownership.
- Response firmware-facing giữ budget HLP; firmware binary stream riêng.
- Release bất biến; backend không build hoặc ký firmware.
- Muốn thêm account/API ghi phải thiết kế auth, isolation, audit, quota, migration
  và threat model riêng thay vì gắn vào public plane.

