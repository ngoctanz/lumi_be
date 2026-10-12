# Deploy Lumi Cloud lên VPS

Stack production gồm bốn service:

- `init`: chạy idempotent một lần để tạo/cập nhật MongoDB validators và indexes.
- `api`: FastAPI/Uvicorn, chỉ expose trong Docker network.
- `worker`: refresh provider cache, dừng mềm khi container nhận SIGTERM.
- `caddy`: public cổng 80/443, tự xin/gia hạn TLS và reverse proxy tới API.

MongoDB dùng Atlas bên ngoài. VPS không mở cổng database.

## 1. Chuẩn bị DNS, firewall và Atlas

1. Tạo bản ghi DNS `A` cho domain API trỏ tới IPv4 của VPS.
2. Mở inbound TCP 22, 80, 443 và UDP 443. Không mở 8000 hoặc 27017.
3. Trong Atlas **Network Access**, thêm public IP của VPS dưới dạng `/32`.
4. Database user chỉ cần `readWrite` trên database `lumi`.
5. Cài Docker Engine cùng Docker Compose plugin theo tài liệu chính thức của Docker.

## 2. Chuẩn bị source và env

Copy/clone project vào VPS, ví dụ `/opt/lumi-be`, rồi:

```sh
cd /opt/lumi-be
cp .env.production.example .env
nano .env
chmod 600 .env
```

Các giá trị bắt buộc:

```dotenv
LUMI_DOMAIN=api.example.com
ACME_EMAIL=admin@example.com
LUMI_MONGODB_URI=mongodb+srv://...
LUMI_CORS_ORIGINS=["https://app.example.com","https://localhost","capacitor://localhost"]
LUMI_CLOUDINARY_CLOUD_NAME=hiuauphb
LUMI_CLOUDINARY_API_KEY=554889889541598
LUMI_CLOUDINARY_API_SECRET=<copy từ Cloudinary Console>
```

`LUMI_DOMAIN` chỉ là hostname, không có `https://` hoặc path. URI Atlas chứa password
phải ở `.env`, không commit Git. Nếu password có ký tự đặc biệt, percent-encode nó.
Cloudinary API secret cũng chỉ nằm trong `.env` trên VPS; cloud name và API key không
đủ quyền upload nếu thiếu secret. Có thể đổi `LUMI_CLOUDINARY_FOLDER` để tách môi trường.

## 3. Deploy

```sh
sh deploy.sh
```

Script validate Compose, build image mới, chạy init schema, khởi động API/worker/Caddy
và chờ healthcheck. Sau khi DNS đã propagate, kiểm tra:

```sh
curl -fsS https://api.example.com/health/live
curl -fsS https://api.example.com/health/ready
docker compose ps
```

Hai health endpoint phải lần lượt trả `{"status":"ok"}` và
`{"status":"ready"}`. Swagger ở `https://api.example.com/docs`.

## 4. Vận hành

Xem log mà không in `.env`:

```sh
docker compose logs -f --tail=200 api worker caddy
```

Deploy source mới:

```sh
git pull --ff-only
sh deploy.sh
```

Restart, stop và xem config đã render:

```sh
docker compose restart api worker
docker compose down
docker compose config --quiet
```

`docker compose down` không xóa certificate Caddy. Không dùng `down -v` trừ khi chủ
động muốn xóa volume certificate/config. Dữ liệu ứng dụng nằm trên Atlas.

Tạo credential cho thiết bị từ container init dùng cùng env production:

```sh
mkdir -p .secrets
chmod 700 .secrets
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/.secrets:/app/.secrets" \
  init python -m lumi_be.cli provision-device --serial CYD-000001
```

Credential được ghi vào `.secrets/` trên host, thư mục này đã Git-ignore. Chép file
về máy quản trị qua kênh an toàn, nạp credential vào NVS của đúng thiết bị, rồi quản
lý/xóa bản trên VPS theo chính sách backup secret của bro.

## 5. Giới hạn và rollback

- Một `worker` là chủ đích; không scale worker khi chưa có distributed lease.
- API chạy một Uvicorn process để phù hợp VPS nhỏ và Mongo pool mặc định 10.
- Caddy là đường public duy nhất; API không publish port 8000 ra host.
- Rollback bằng cách checkout commit trước rồi chạy lại `sh deploy.sh`. `init-db` chỉ
  phù hợp schema tương thích ngược; migration phá vỡ dữ liệu cần quy trình riêng.
- Atlas backup/PITR tùy plan. Với free tier, export định kỳ trước thay đổi schema lớn.
