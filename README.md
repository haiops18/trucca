# TrucCa – Alert Exporter & NOC Shift Report

TrucCa lấy cảnh báo từ **Prometheus Alertmanager** (định kỳ 30 giây), lưu lịch sử vào **PostgreSQL**,
hiển thị cảnh báo đang diễn ra trên web, xuất **Excel** và sinh **báo cáo cuối ca (PDF)** cho đội NOC.
Có đăng nhập và phân quyền **Xem / Quản trị**. Chạy bằng `docker compose`; mọi cấu hình nằm ngoài image.

- [Kiến trúc](#kiến-trúc)
- [Chạy nhanh (demo với mock Alertmanager)](#chạy-nhanh-demo-với-mock-alertmanager)
- [Triển khai thật](#triển-khai-thật)
- [Cấu hình](#cấu-hình)
- [Người dùng và phân quyền](#người-dùng-và-phân-quyền)
- [Quy tắc thu thập và tính báo cáo](#quy-tắc-thu-thập-và-tính-báo-cáo)
- [Vận hành](#vận-hành)
- [Troubleshooting](#troubleshooting)
- [Phát triển](#phát-triển)

---

## Kiến trúc

```
                  ┌──────────────── container trucca (UID 10001) ─────────────────┐
Alertmanager ◄────┤ Collector: GET /api/v2/alerts mỗi 30s → reconcile theo         │
 /api/v2/alerts   │            fingerprint → bảng alerts / alert_occurrences        │
                  │ Web (FastAPI): trang HTML + API nội bộ (bắt buộc đăng nhập)     │
 Trình duyệt ────►│ Báo cáo: Jinja2 HTML + SVG → PDF (Chromium headless)            │
                  │ Scheduler: poll, chốt báo cáo khi hết ca, theo dõi config,     │
                  │            dọn dữ liệu cũ                                       │
                  └──────────────┬─────────────────────────────────────────────────┘
                                 │ SQL
                  ┌──────────────▼──────────────┐
                  │ postgres:16 (chỉ nội bộ)     │
                  └─────────────────────────────┘
volume: ./config → /app/config (ro) · ./data/trucca → /data (PDF) · ./data/postgres → dữ liệu DB
```

| Thành phần | Công nghệ |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn (1 tiến trình), APScheduler, httpx |
| DB | PostgreSQL 16, SQLAlchemy Core, Alembic (tự migrate khi khởi động) |
| Excel | XlsxWriter |
| PDF | Jinja2 + SVG inline → Playwright/Chromium headless shell |
| Frontend | HTML + JavaScript thuần, không build, không CDN; font Be Vietnam Pro nhúng sẵn |

Cấu trúc thư mục:

```
trucca/                 mã nguồn ứng dụng
  config.py             đọc + validate config (pydantic)
  collector.py          gọi Alertmanager, reconcile, ghi DB
  alerts_query.py       lọc / sắp xếp / gộp nhóm cảnh báo đang diễn ra
  export_xlsx.py        xuất Excel
  reports/calc.py       tính số liệu báo cáo ca (hàm thuần, có unit test)
  reports/service.py    lấy dữ liệu, snapshot hết ca, sinh PDF
  reports/render.py     HTML/SVG/PDF
  auth/                 mật khẩu (scrypt), phiên, người dùng, nhật ký
  web/                  route trang HTML và API nội bộ
  templates/ static/    giao diện
migrations/             Alembic
mock/                   mock Alertmanager + script sinh lịch sử giả
tests/                  unit test
config/config.example.yml, .env.example, Dockerfile, docker-compose.yml
```

### Bảng dữ liệu chính

| Bảng | Ý nghĩa |
|---|---|
| `alerts` | trạng thái mới nhất mỗi `fingerprint` (active/resolved) – nguồn của bảng "đang diễn ra" |
| `alert_occurrences` | mỗi lần **phát sinh** là 1 dòng (`starts_at`, `resolved_at`, `resolve_source`) – nguồn của báo cáo ca |
| `shift_snapshots` | số liệu báo cáo chốt lúc hết ca (JSON) + đường dẫn PDF |
| `users`, `sessions`, `audit_log` | người dùng, phiên đăng nhập, nhật ký thao tác |

---

## Chạy nhanh (demo với mock Alertmanager)

Yêu cầu: Docker Engine 24+ với Docker Compose v2.

```bash
cp .env.example .env                       # đổi mật khẩu trong .env
cp config/config.example.yml config/config.yml
sed -i 's#http://alertmanager:9093#http://mock-alertmanager:9093#' config/config.yml

mkdir -p data/trucca data/postgres
sudo chown 10001:10001 data/trucca         # container chạy non-root UID 10001

docker compose build
docker compose --profile demo up -d --wait      # chờ tới khi các service healthy
docker compose exec trucca python -m mock.seed_history --days 8   # (tuỳ chọn) lịch sử 8 ngày cho báo cáo
```

Mở `http://<máy-chủ>:8080`, đăng nhập bằng tài khoản mặc định **`admin` / `admin`** (hoặc
`TRUCCA_ADMIN_USERNAME` / `TRUCCA_ADMIN_PASSWORD` nếu đã đặt trong `.env`). Hệ thống bắt buộc đổi mật khẩu ngay ở
lần đăng nhập đầu tiên, trước khi dùng được bất kỳ chức năng nào.

Thử các tình huống lỗi của Alertmanager (giao diện sẽ báo "Mất kết nối tới Alertmanager"):

```bash
docker compose exec mock-alertmanager python -c "import urllib.request as u;u.urlopen(u.Request('http://127.0.0.1:9093/mock/fail?mode=500',method='POST'))"
# mode: 500 | 401 | timeout | garbage | empty | off
```

---

## Triển khai thật

1. `cp .env.example .env` và đặt:
   - `POSTGRES_PASSWORD` – chỉ dùng chữ, số, `-`, `_` (để không phải URL-encode trong `DATABASE_URL`).
   - `TRUCCA_ADMIN_USERNAME`, `TRUCCA_ADMIN_PASSWORD` – admin đầu tiên, chỉ tạo khi DB chưa có người dùng.
     Bỏ trống thì mặc định `admin` / `admin`; luôn bắt đổi mật khẩu ở lần đăng nhập đầu.
   - `ALERTMANAGER_*` nếu Alertmanager có xác thực.
   - `TRUCCA_IMAGE`, `TRUCCA_VERSION` – image trên registry nội bộ; `TRUCCA_PORT` – cổng trên host.
2. `cp config/config.example.yml config/config.yml` và chỉnh `alertmanager.url`, `labels`, `filters`, `shifts`.
3. Cấp quyền thư mục: `mkdir -p data/trucca && sudo chown 10001:10001 data/trucca`.
4. Nếu Alertmanager chạy ở stack compose khác: bỏ comment khối `networks` trong `docker-compose.yml`,
   đặt `name:` bằng tên network của stack đó (`docker network ls`), và dùng tên service làm URL.
5. `docker compose up -d --wait` (không cần `--profile demo`).

### Build và đẩy image lên registry nội bộ

```bash
export TRUCCA_IMAGE=registry.local/noc/trucca TRUCCA_VERSION=1.0.0
docker build \
  --build-arg APP_VERSION=$TRUCCA_VERSION \
  --build-arg BUILD_DATE=$(date -u +%FT%TZ) \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  -t $TRUCCA_IMAGE:$TRUCCA_VERSION .
docker push $TRUCCA_IMAGE:$TRUCCA_VERSION
```

Máy chạy chỉ cần `docker-compose.yml`, `.env`, thư mục `config/` rồi `docker compose pull && docker compose up -d`.
Phiên bản hiển thị ở trang **Hệ thống** và `/healthz` lấy từ build arg `APP_VERSION`.

---

## Cấu hình

`config/config.yml` được mount **read-only** vào `/app/config/config.yml`. File mẫu có chú thích đầy đủ:
[`config/config.example.yml`](config/config.example.yml).

**Thứ tự ưu tiên:** biến môi trường > `config.yml` > mặc định. Mọi khoá ghi đè được bằng
`TRUCCA__<MỤC>__<KHOÁ>`, ví dụ `TRUCCA__ALERTMANAGER__POLL_INTERVAL_SECONDS=15`,
`TRUCCA__REPORT__TOP_N=10`. Secret chỉ đặt trong `.env`.

**Áp dụng thay đổi – không build lại image:**

- Trang **Hệ thống → Nạp lại cấu hình**, hoặc
- chờ tối đa 10 giây (TrucCa tự phát hiện file thay đổi), hoặc
- `docker compose restart trucca`.

Mục `database`, `server` cần restart. Nếu file mới sai, TrucCa **giữ cấu hình cũ**, ghi log và hiện lỗi ở
trang Hệ thống. Khi khởi động với cấu hình sai, container dừng và in danh sách lỗi, ví dụ:

```
[TrucCa] Cấu hình không hợp lệ:
  - alertmanager.url: phải bắt đầu bằng http:// hoặc https://
  - shifts.0: ca CA_1: giờ bắt đầu và kết thúc không được trùng nhau
```

### Các mục cấu hình

| Mục | Khoá | Mặc định | Ý nghĩa |
|---|---|---|---|
| `alertmanager` | `url` | `http://alertmanager:9093` | địa chỉ Alertmanager (container gọi tới) |
| | `auth.type` | `none` | `none` / `basic` (dùng `ALERTMANAGER_USERNAME/PASSWORD`) / `bearer` (dùng `ALERTMANAGER_TOKEN`) |
| | `verify_tls`, `ca_file` | `true`, `null` | kiểm tra chứng chỉ; CA nội bộ đặt trong `config/` rồi trỏ `/app/config/ca.pem` |
| | `timeout_seconds` | `10` | timeout mỗi lần gọi |
| | `poll_interval_seconds` | `30` | chu kỳ lấy cảnh báo |
| | `resolve_after_missing_polls` | `2` | số lần poll thành công liên tiếp vắng mặt thì coi là clear |
| | `mass_clear_guard` | `0.5 / 10 / 10` | nhiều cảnh báo biến mất cùng lúc thì chờ `polls` lần |
| `labels` | `owner`, `application`, `alertname`, `severity`, `instance` | trùng tên | label nào là "Người xử lý", "Hệ thống"... |
| | `summary_annotation` | `summary` | annotation dùng làm Summary |
| `filters` | `match`, `match_regex` | `{}` | chỉ lấy cảnh báo có label khớp |
| | `exclude_alertnames` | `[]` | bỏ qua theo tên |
| | `include_silenced`, `include_inhibited` | `false` | có hiển thị cảnh báo đang silence/inhibit |
| `timezone` | | `Asia/Ho_Chi_Minh` | múi giờ tính ca, ngày |
| `shifts` | `name`, `start`, `end` | CA_1 08–18, CA_2 18–08 | danh sách ca |
| `display` | `refresh_seconds`, `group_same_alert`, `missing_label_text`, `page_size` | 30, false, `(chưa gán)`, 50 | giao diện |
| `report` | `handover_threshold_hours` | `10` | ngưỡng "tồn cần bàn giao" |
| | `handover_max_items` | `8` | số nhóm tối đa trong mục bàn giao (8 vừa 1 trang A4) |
| | `top_n`, `trend_days` | 5, 7 | |
| | `auto_generate_at_shift_end` | `true` | tự chốt + sinh PDF khi hết ca |
| | `output_dir` | `/data/reports` | nơi lưu `noc_shift_report_<CA>_<YYYYMMDD>.pdf` |
| `retention_days` | | `90` | xoá lịch sử / snapshot / PDF / nhật ký cũ |
| `server` | `port`, `base_path` | 8080, `/` | `base_path: /trucca` khi đặt sau reverse proxy theo đường dẫn con |
| `auth` | `session_ttl_hours`, `remember_days`, `cookie_secure`, `max_failed_attempts`, `lockout_minutes`, `min_password_length` | 12, 30, false, 5, 15, 8 | đặt `cookie_secure: true` khi dùng HTTPS |

### Ánh xạ label

Nếu Alertmanager của bạn dùng label khác, chỉ cần đổi ở `labels`, ví dụ người xử lý nằm ở `assignee`,
hệ thống nằm ở `service`:

```yaml
labels:
  owner: assignee
  application: service
```

Cảnh báo thiếu label hiển thị `(chưa gán)` (đổi bằng `display.missing_label_text`) và lọc được bằng mục
"(chưa gán)" trong dropdown.

### Thêm / sửa ca

```yaml
shifts:
  - { name: CA_SANG, start: "06:00", end: "14:00" }
  - { name: CA_CHIEU, start: "14:00", end: "22:00" }
  - { name: CA_DEM, start: "22:00", end: "06:00" }   # qua đêm: end <= start
```

- Ca qua đêm kết thúc vào ngày hôm sau; **ngày báo cáo là ngày bắt đầu ca**.
- Báo cáo đã chốt của ca cũ vẫn giữ nguyên (đọc từ snapshot). Nếu đổi tên ca, snapshot cũ vẫn còn trong danh
  sách "Báo cáo đã chốt" nhưng không chọn lại được ở dropdown.

---

## Người dùng và phân quyền

| Chức năng | Xem (`viewer`) | Quản trị (`admin`) |
|---|:-:|:-:|
| Xem cảnh báo, lọc, tìm kiếm, xuất Excel | ✓ | ✓ |
| Xem / tải báo cáo ca | ✓ | ✓ |
| Đổi mật khẩu của mình | ✓ | ✓ |
| Chốt lại & sinh lại PDF báo cáo | | ✓ |
| Thêm / sửa / khoá / xoá người dùng, đặt lại mật khẩu | | ✓ |
| Trang Hệ thống: trạng thái, cấu hình, nạp lại cấu hình, nhật ký thao tác | | ✓ |

- Admin đầu tiên tạo khi DB chưa có người dùng (mặc định `admin` / `admin`, hoặc theo `.env`); lần đăng nhập đầu **bắt buộc đổi mật khẩu**.
- Người dùng mới / bị đặt lại mật khẩu cũng phải đổi mật khẩu ở lần đăng nhập kế tiếp.
- Mật khẩu: tối thiểu `min_password_length` ký tự, 3 trong 4 loại (thường, hoa, số, ký tự đặc biệt), băm scrypt.
- Sai mật khẩu `max_failed_attempts` lần → khoá `lockout_minutes` phút (admin có thể mở khoá).
- Luôn phải còn ít nhất 1 admin đang hoạt động; không tự xoá được tài khoản của mình.
- Đổi vai trò / vô hiệu hoá / đặt lại mật khẩu → các phiên của người đó bị đăng xuất ngay.
- Tải lại trang không làm mất đăng nhập. Ô **"Ghi nhớ đăng nhập"** (mặc định bật) giữ phiên `remember_days` ngày
  (30) kể cả khi đóng trình duyệt, tự gia hạn khi còn dùng. Bỏ chọn (máy dùng chung): đăng nhập mất khi đóng
  trình duyệt, hoặc sau `session_ttl_hours` giờ (12) không hoạt động. TrucCa không lưu mật khẩu ở trình duyệt;
  việc lưu mật khẩu (nếu muốn) do trình quản lý mật khẩu của trình duyệt đảm nhận.
- Mọi đăng nhập, thay đổi người dùng, nạp cấu hình, sinh báo cáo đều ghi vào nhật ký (trang Hệ thống).

**Quên mật khẩu admin duy nhất:** dùng một admin khác để đặt lại; nếu không còn admin nào:

```bash
docker compose exec postgres psql -U trucca -d trucca -c "DELETE FROM users;"
docker compose restart trucca   # admin được tạo lại từ .env
```

---

## Quy tắc thu thập và tính báo cáo

### Thu thập

- Mỗi `poll_interval_seconds` gọi `GET /api/v2/alerts`, định danh bằng `fingerprint`.
- **Phát sinh:** fingerprint mới, hoặc đã clear rồi firing lại với `startsAt` mới → 1 dòng occurrence mới.
- **Clear:** cảnh báo vắng mặt `resolve_after_missing_polls` lần poll **thành công** liên tiếp
  (`resolved_at` = lần vắng mặt đầu tiên), hoặc `endsAt` đã qua.
- **Mất kết nối** (timeout, 401/403, 5xx, phản hồi không phải JSON): **không clear gì cả**, giữ nguyên trạng thái,
  ghi log, giao diện hiện banner đỏ kèm nguyên nhân và thời điểm lấy dữ liệu thành công gần nhất.
- **Alertmanager restart trả danh sách rỗng:** nếu ≥ 50% (và ≥ 10) cảnh báo active cùng biến mất, chờ 10 lần poll
  (~5 phút) mới clear. Nếu cảnh báo quay lại với cùng `startsAt`, occurrence được **mở lại** (không đếm phát sinh
  lần nữa, không tính là đã clear).
- Cảnh báo không còn khớp `filters` (sửa config, hoặc bị silence khi `include_silenced: false`) cũng phải qua đủ
  số lần poll như trên mới được đóng, với nguồn `filtered_out`, và **không** tính vào "Đã clear".
- Một mục dữ liệu lỗi trong phản hồi (vd `startsAt` sai định dạng) bị bỏ qua và ghi log; cảnh báo đó **không** bị
  coi là vắng mặt. Lỗi xử lý bất ngờ (dữ liệu lạ, DB) hiện trên giao diện như mất kết nối.

### Thời gian tồn

`now − startsAt` khi đang active; `resolved_at − startsAt` khi đã clear.
Từ 24 giờ: `8d 9h`; dưới 24 giờ: `23.0h`, `12.7h` (1 chữ số thập phân, làm tròn xuống).

### Báo cáo ca (khoảng `[bắt đầu, kết thúc)`)

| Chỉ số | Cách tính |
|---|---|
| Phát sinh | số occurrence có `startsAt` trong ca |
| Đã clear | số occurrence có `resolved_at` trong ca (kể cả phát sinh từ ca trước) |
| Tồn sang ca sau | số occurrence bắt đầu trước khi hết ca và còn active tại thời điểm hết ca; hiện `--` khi ca chưa kết thúc |
| So với cùng ca hôm qua | so với ca cùng tên của ngày D−1 (dùng snapshot nếu có); tăng đỏ, giảm xanh; thanh = hôm nay / max(hôm nay, hôm qua) |
| Tồn cần bàn giao | còn active tại lúc hết ca (hoặc hiện tại nếu ca đang chạy) và tồn > `handover_threshold_hours`; gộp theo hệ thống + tên cảnh báo, kèm `×N` và `(N) instance đầu – instance cuối` |
| Hệ thống / Loại cảnh báo nổi bật | Top N theo số phát sinh trong ca, % trên tổng phát sinh |
| Xu thế N ngày | số phát sinh theo ngày lịch, N ngày kết thúc ở **ngày kết thúc ca** (ca CA_2 ngày 11/11 → tới 12/11), ngày cuối chỉ tính tới lúc hết ca |

- Sau giờ kết thúc ca, TrucCa chờ `(resolve_after_missing_polls + 1) × poll_interval_seconds` (mặc định 90 giây)
  để xác nhận các cảnh báo clear sát giờ, và chờ có một lần lấy dữ liệu thành công sau mốc đó, rồi **chốt snapshot**
  và sinh PDF vào `output_dir`. Xem lại sau này vẫn ra đúng số đã chốt. Nếu Alertmanager mất kết nối quá 30 phút
  sau giờ kết thúc ca thì vẫn chốt (ghi log cảnh báo `shift_finalized_with_stale_data`).
  Nếu container tắt lúc hết ca, khi bật lại sẽ chốt bù các ca kết thúc trong 2 ngày gần nhất.
- Ca kết thúc **trước khi TrucCa bắt đầu thu thập** không được tự chốt (dữ liệu không đầy đủ); vẫn xem trực tiếp
  được, hoặc admin bấm "Chốt lại & sinh PDF".
- Muốn xem PDF mẫu: chạy stack demo (profile `demo`) rồi tải ở trang Báo cáo ca. Báo cáo không có mục "Đơn vị sản phẩm" và "Sự cố" (đã bỏ theo yêu cầu).

---

## Vận hành

| Việc | Lệnh / nơi xem |
|---|---|
| Log (JSON, mỗi dòng 1 sự kiện) | `docker compose logs -f trucca` |
| Sức khoẻ | `GET /healthz` → `{"status":"ok","db":true,"alertmanager":"ok|degraded"}`; HEALTHCHECK của image gọi endpoint này |
| Trạng thái thu thập, tác vụ, cấu hình | trang **Hệ thống** (admin) |
| Sao lưu DB | `docker compose exec postgres pg_dump -U trucca trucca > backup.sql` |
| Khôi phục | `docker compose exec -T postgres psql -U trucca trucca < backup.sql` |
| Nâng cấp | đổi `TRUCCA_VERSION`, `docker compose pull && docker compose up -d` (migration tự chạy) |

Log không chứa mật khẩu/token; trang Hệ thống hiển thị secret dạng `**********`.

---

## Troubleshooting

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Container `trucca` dừng ngay, log `Cấu hình không hợp lệ` | sửa `config/config.yml` theo danh sách lỗi, `docker compose up -d` |
| `Không ghi được vào thư mục báo cáo /data/reports` | `sudo chown -R 10001:10001 data/trucca` |
| `Không kết nối được PostgreSQL sau 90s` | xem `docker compose logs postgres`; kiểm tra `POSTGRES_PASSWORD` không đổi sau lần khởi tạo đầu (Postgres chỉ đọc biến này khi thư mục `data/postgres` trống) |
| Banner "Mất kết nối tới Alertmanager – Không kết nối được" | sai `alertmanager.url` hoặc chưa join chung network; thử `docker compose exec trucca python -c "import httpx;print(httpx.get('http://alertmanager:9093/-/healthy').text)"` |
| "... từ chối xác thực (HTTP 401)" | sai `auth.type` hoặc thông tin trong `.env` |
| "Hết thời gian chờ" | Alertmanager chậm/quá tải; tăng `timeout_seconds` |
| Lỗi chứng chỉ TLS | đặt CA vào `config/ca.pem`, `ca_file: /app/config/ca.pem` |
| Cột Người xử lý / Hệ thống toàn `(chưa gán)` | sai tên label trong mục `labels` |
| Không thấy cảnh báo đang silence | đặt `filters.include_silenced: true` |
| Tải PDF báo lỗi | xem `docker compose logs trucca` (`report_pdf_failed`); thường do thiếu RAM – cấp ≥ 512 MB cho container |
| Báo cáo ca cũ sai số do lúc đó TrucCa chưa chạy | admin chọn ca đó → "Chốt lại & sinh PDF" |
| Cổng 8080 đã dùng | đặt `TRUCCA_PORT` khác trong `.env` |
| Quên mật khẩu | xem mục [Người dùng và phân quyền](#người-dùng-và-phân-quyền) |

---

## Phát triển

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q                    # unit test: thời gian tồn, ca qua đêm, phát sinh/clear/tồn, top N,
                             # so sánh hôm qua, bàn giao, reconcile, Excel, config, mật khẩu, render
```

Chạy local cần PostgreSQL (`DATABASE_URL`) và `TRUCCA_CONFIG=config/config.yml`:
`python -m trucca.main`. Render PDF cần `playwright install chromium`.
