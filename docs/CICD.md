# CI/CD từ GitHub tới VPS

Workflow `.github/workflows/backend.yml` có hai job:

1. `test`: lint/format, test với MongoDB 7 container, kiểm tra OpenAPI, validate Compose
   và build Docker image production.
2. `deploy`: chỉ chạy sau `test`, SSH vào VPS, fast-forward `main` và gọi `deploy.sh`.

CD mặc định tắt. Repository variable `VPS_DEPLOY_ENABLED=true` mới bật deploy.

## 1. Chuẩn bị VPS lần đầu

Tạo DNS và Atlas/firewall theo `docs/DEPLOY_VPS.md`, cài Docker Engine + Compose
plugin, sau đó tạo user deploy:

```sh
sudo adduser deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/lumi-be
sudo chown deploy:deploy /opt/lumi-be
```

Đăng xuất/đăng nhập lại để group `docker` có hiệu lực, rồi:

```sh
sudo -iu deploy
git clone https://github.com/ngoctanz/lumi_be.git /opt/lumi-be
cd /opt/lumi-be
cp .env.production.example .env
nano .env
chmod 600 .env
sh deploy.sh
```

Xác nhận `https://<domain>/health/ready` trả `{"status":"ready"}` trước khi bật CD.

## 2. Tạo SSH key riêng cho GitHub Actions

Tạo key trên máy quản trị, không tạo trong repository:

```sh
ssh-keygen -t ed25519 -a 100 -f lumi_vps_deploy -C github-actions-lumi
```

Thêm nội dung `lumi_vps_deploy.pub` vào `/home/deploy/.ssh/authorized_keys` trên VPS.
Private key `lumi_vps_deploy` chỉ lưu trong GitHub secret.

Trên VPS xem fingerprint host key thật:

```sh
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Từ một máy đã xác minh đúng fingerprint, lấy known-host line:

```sh
ssh-keyscan -H -t ed25519 api.example.com
```

Không dùng `StrictHostKeyChecking=no`. Workflow dùng `VPS_KNOWN_HOSTS` cố định để
không tự tin một host key lạ.

## 3. GitHub Secrets và Variables

Vào **Repository → Settings → Secrets and variables → Actions**.

Repository secrets:

| Tên | Giá trị |
| --- | --- |
| `VPS_SSH_HOST` | Domain hoặc IP SSH của VPS |
| `VPS_SSH_USER` | `deploy` |
| `VPS_SSH_PRIVATE_KEY` | Toàn bộ private key, gồm BEGIN/END lines |
| `VPS_KNOWN_HOSTS` | Dòng đã xác minh từ `ssh-keyscan` |

Repository variables:

| Tên | Giá trị |
| --- | --- |
| `VPS_SSH_PORT` | Port SSH, mặc định `22` |
| `VPS_DEPLOY_ENABLED` | Đặt `true` sau khi deploy tay lần đầu thành công |

Mongo URI và app secrets chỉ nằm trong `/opt/lumi-be/.env` trên VPS. Không đưa chúng
vào GitHub Actions vì workflow deploy không cần đọc các giá trị đó.

## 4. Bật pipeline

Push hoặc merge vào `main` sẽ:

```text
CI test/build đạt → SSH VPS → git pull --ff-only → sh deploy.sh → healthcheck
```

Có thể chạy lại thủ công trong **Actions → Backend CI/CD → Run workflow**. Nên tạo
GitHub Environment tên `production` và bật required reviewer nếu muốn duyệt tay trước
mỗi lần deploy.

Khuyến nghị bảo vệ branch `main`: yêu cầu pull request và status check `test` trước
khi merge. Khi làm một mình và push thẳng main, CI vẫn chạy trước job deploy.

## 5. Sự cố và rollback

Xem GitHub Actions log trước. Trên VPS:

```sh
cd /opt/lumi-be
docker compose ps
docker compose logs --tail=200 api worker caddy init
```

Rollback bằng Git, không sửa source trực tiếp trên VPS:

```sh
git revert <bad-commit>
git push origin main
```

Revert tạo commit mới; pipeline test và deploy trạng thái cũ. Nếu CD hỏng hoàn toàn,
SSH vào VPS, checkout commit tốt đã biết rồi chạy `sh deploy.sh`, sau đó sửa `main`.

Đổi `.env` là thao tác production thủ công: sửa file trên VPS rồi chạy `sh deploy.sh`.
File này không bị `git pull` ghi đè vì đã Git-ignore.
