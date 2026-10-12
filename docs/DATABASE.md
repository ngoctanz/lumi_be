# MongoDB — document, index và Atlas

## Collections

| Collection | Dữ liệu | Index |
| --- | --- | --- |
| users | UUID, email chuẩn hóa, Argon2 hash | email unique |
| login_sessions | hash token, user_id, expires_at, expire_at | user_id; TTL expire_at |
| devices | UUID, serial, hash khóa, owner, revision, config/layout/widgets/nav | serial/token unique; pairing unique partial; owner + ID |
| provider_cache | canonical key, source_id, normalized payload, updated_at, expire_at | `_id` unique; TTL expire_at |
| assets | owner/model/name/raw URL/preview URL/hash/Cloudinary IDs | model + owner + ID; provider ID unique partial |
| rate_limits | scope-window hash, hits, expire_at | `_id` unique; TTL expire_at |
| metadata | schema version marker | `_id` |

ID API giữ UUID dạng string; dùng UUID đó làm `_id`, không cần chuyển ObjectId ở
firmware/app. Session/cache/limiter dùng `_id` từ hash phù hợp nghiệp vụ.
Không tạo index cho mọi field; không có index trên raw payload, layout hay widgets.

## Document thiết bị

```json
{
  "_id": "<device-uuid>",
  "serial": "CYD-000001",
  "model": "WazeHUD-CYD",
  "token_hash": "<sha256>",
  "owner_id": "<user-uuid>",
  "enabled": true,
  "revision": 5,
  "config": {"brightness": 70, "theme": "auto"},
  "layout": {"version": 1, "widgets": []},
  "widgets": {
    "weather": {
      "source_id": "open-meteo-weather-v1",
      "params": {"latitude": 16.0544, "longitude": 108.2022},
      "enabled": true
    }
  }
}
```

Ví dụ lược bỏ các field trạng thái; layout rỗng chỉ để minh họa document, API vẫn
validate layout schema-1 có 1–6 widget. Mỗi loại widget có một instance đúng contract
hiện tại; nếu đổi sang nhiều instance cùng loại phải version schema/API.

Embed config/layout/widgets trong device vì nhỏ, có giới hạn và cùng vòng đời.
Binding update dùng `$set: {"widgets.weather": ...}` và `$inc: {revision:1}` trong
một lệnh có điều kiện `_id + owner_id + enabled + revision`. Hai request cùng revision
chỉ một request thắng. Unbind xóa owner/config/layout/widgets/nav trong một lệnh.
Không cần transaction nhiều document để giữ quyền điều khiển nhất quán.

Pairing cũng cập nhật nguyên tử: thiết bị chưa có owner, code hash khớp, còn hạn.
Index pairing chỉ unique khi field là string (`partialFilterExpression`), nên nhiều
thiết bị chưa ghép với `pairing_hash=null` vẫn hợp lệ. Không dùng unique index thường
trên field nullable. API xác thực theo key của thiết bị, không nhận ID để chọn mạch.

## Widget sources và cache

`providers/registry.py` giữ adapter tin cậy, schema params, TTL và source version.
DB không chứa URL thực thi tùy ý hoặc provider API key do app gửi lên. Thêm API
bằng adapter normalize + schema + kiểm thử, không thêm collection/cột cho từng widget.

```text
App → owner/revision → devices.widgets
                        ↓ một worker gom nguồn + params
API ngoài → validate/normalize → provider_cache
                                      ↓ chỉ đọc cache
Firmware ← JSON <=512 byte + ETag/304 + state/expiry
```

Key = SHA-256(source version + JSON params canonical). Tọa độ làm tròn 4 chữ số
trước khi fetch và hash; hai mạch cùng vị trí dùng một cache công khai. Không tạo
bản cache theo user/device nếu dữ liệu upstream là công khai. Dữ liệu riêng tư sau
này phải thêm owner/credential scope vào key và kiểm tra quyền, không dùng namespace
cache công khai hiện tại. Endpoint firmware không lộ params, owner hoặc key provider.

Worker stream devices batch 100, không join/query bindings riêng; bỏ widget ẩn/tắt,
dedup cùng key. Chỉ một scheduler instance; khi cần nhiều worker phải thêm lease/queue.
Weather TTL một giờ, last-good tối đa 24 giờ; lỗi upstream giữ payload cũ. Scheduler
tick 5 phút, có thể refresh trễ TTL đến một tick. Adapter payload <=256 byte để
chừa metadata trong response <=512 byte. Không fetch upstream trong request của mạch.

`/api/v1/device/widgets/{type}` trả state `fresh`, `stale`, `pending`, `unconfigured`.
`at`/`expires_at` là Unix seconds; 304 không kéo dài hạn. ETag chứa data/revision/state,
fresh→stale cũng đổi ETag. Snapshot weather cũ dùng cùng cache mới, không lưu hai bản.
Weather chưa có binding dùng tọa độ device config; binding đã đặt được ưu tiên.
Stock firmware hiện là demo; chưa có stock/bot adapter. Clock/calendar local; nav riêng.

## TTL, validation và vận hành

Mongo TTL yêu cầu BSON Date: dùng `expire_at` UTC datetime; `expires_at` Unix seconds
vẫn giữ để kiểm tra session ngay trong API. TTL chạy bất đồng bộ, record hết hạn có
thể còn tồn tại một lúc; API luôn tự kiểm tra hạn session/cache/pairing/nav trước dùng.
Không đặt TTL lên document devices để dọn pairing, vì sẽ xóa cả thiết bị.
Rate limiter một document mỗi scope/window, `$inc` nguyên tử và TTL cuối cửa sổ.
Không ghi lịch sử navigation, polling hoặc response upstream thô.

`python -m lumi_be.cli init-db` tạo collections, JSON Schema validators và indexes;
chạy lại được an toàn. Metadata chỉ đánh dấu version sau khi tạo index thành công.
API ready kiểm tra ping + marker, không tự cấp quyền admin hoặc sửa index khi startup.
MongoDB không có foreign key: mọi đường app cần kiểm tra owner, CLI kiểm tra owner
khi publish private asset; khi thêm chức năng xóa account cần cleanup quan hệ rõ ràng.
Đăng ký user rồi issue session là hai write: nếu session write lỗi, user vẫn có thể login.

PyMongo sync dùng threadpool của FastAPI sync routes, một client/pool mỗi process.
Pool mặc định max 10, min 0, timeout bounded; không tạo client mới mỗi request.
Đổi env cần restart API/worker. Không nhúng URI vào source/image hoặc gửi ra firmware.
`.env`, `data/`, `.secrets/`, `.tools/` bị loại khỏi source/build context.

## Điền Atlas

1. Tạo database user với quyền `readWrite` trên `lumi` và Network Access cho IP backend.
2. Copy URI từ Atlas Connect → Drivers vào `.env`: `LUMI_MONGODB_URI`.
3. Giữ `LUMI_MONGODB_DATABASE=lumi`, chạy `init-db`, rồi API và một worker.
4. Giữ TLS của URI `mongodb+srv://`; không dùng `tlsAllowInvalidCertificates`.
   User/password là database credential, không phải API key của Atlas; ký tự đặc
   biệt cần percent-encode. Không log hoặc gửi URI có password vào chat.

Compose dùng Atlas ngoài (init + API + worker), không có PostgreSQL/service DB riêng.
Mongo Community thật đã được chạy và kiểm thử local; Atlas tài khoản của bro và Docker
chưa được kết nối/kiểm thử. Chỉ cần thay URI local bằng URI Atlas rồi restart/init.
Free vẫn có hạn mức storage/operations/network; giữ binary ảnh/firmware ngoài MongoDB.
Nguồn SQL cũ được backup và file SQLite giữ nguyên; DB cũ đang trống nên không có
account/device thực cần chuyển. Backend hiện chỉ chạy MongoDB, không hỗ trợ hai DB song song.
