# TrucCa – Alert Exporter & NOC Shift Report

TrucCa lấy cảnh báo từ **Prometheus Alertmanager** (định kỳ 30 giây), lưu lịch sử vào **PostgreSQL**,
hiển thị cảnh báo đang diễn ra trên web, xuất **Excel** và sinh **báo cáo cuối ca (PDF)** cho đội NOC.
Có đăng nhập và phân quyền **Xem / Quản trị**. Chạy bằng `docker compose`; mọi cấu hình nằm ngoài image.

- [Kiến trúc](#kiến-trúc)
- [Chạy nhanh (demo với mock Alertmanager)](#chạy-nhanh-demo-với-mock-alertmanager)
- [Cài đặt bằng script](#cài-đặt-bằng-script-khuyến-nghị)
- [Triển khai thủ công](#triển-khai-thủ-công)
- [Cấu hình](#cấu-hình)
- [Trang cảnh báo và Excel](#trang-cảnh-báo-và-excel)
- [Mức độ (severity)](#mức-độ-severity)
- [Người dùng và phân quyền (module Quản trị)](#người-dùng-và-phân-quyền-module-quản-trị)
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
  severity.py           chuẩn hoá mức độ (critical / warning / other) theo severity_mapping
  export_xlsx.py        xuất Excel
  reports/calc.py       tính số liệu báo cáo ca (hàm thuần, có unit test)
  reports/service.py    lấy dữ liệu, snapshot hết ca, sinh PDF
  reports/render.py     HTML/SVG/PDF
  auth/                 mật khẩu (scrypt), phiên, người dùng, nhật ký
  web/                  route trang HTML và API nội bộ
  templates/ static/    giao diện
migrations/             Alembic
mock/                   mock Alertmanager + script sinh lịch sử giả
tests/                  unit test; tests/integration: test tích hợp với PostgreSQL thật
config/config.example.yml, .env.example, Dockerfile, docker-compose.yml
install.sh              cài đặt / nâng cấp bằng script
scripts/package-image.sh đóng gói image thành file phát hành
```

### Bảng dữ liệu chính

| Bảng | Ý nghĩa |
|---|---|
| `alerts` | trạng thái mới nhất mỗi `fingerprint` (active/resolved) – nguồn của bảng "đang diễn ra"; `severity` (gốc) + `severity_normalized` |
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

## Cài đặt bằng script (khuyến nghị)

Trên máy chủ chỉ cần Docker (có `docker compose` v2), `git` và `curl`:

```bash
git clone https://github.com/haiops18/trucca.git && cd trucca
sudo ./install.sh
```

Script tự tải 2 image từ GitHub Release `v<version>`: `trucca-image-<version>.tar.gz` và
`postgres-16.16-alpine.tar.gz` (kèm `.sha256`). **Máy không có internet:** tải trước 4 file đó ở trang
[Releases](https://github.com/haiops18/trucca/releases), chép vào thư mục repo (cạnh `install.sh`) rồi chạy script.

Script hỏi 3 thông tin rồi tự làm phần còn lại:

| Câu hỏi | Ví dụ | Ghi chú |
|---|---|---|
| Địa chỉ Alertmanager | `http://10.0.0.5:9093` | dùng IP/hostname thật, **không** dùng `localhost` |
| Thư mục lưu cấu hình và dữ liệu | `/opt/trucca` | tạo `config/config.yml`, `data/` (PostgreSQL + PDF), `trucca.env` |
| Cổng web | `8080` | báo lỗi nếu cổng đang bị chương trình khác dùng |

Các bước script thực hiện: kiểm tra Docker → nạp image TrucCa + PostgreSQL (dùng image có sẵn, hoặc `docker load`
file cạnh script, hoặc tự tải từ Release bằng `gh`/`curl`; luôn kiểm tra SHA-256) → tạo thư mục, phân quyền UID 10001 → tạo `config.yml` và ghi
`alertmanager.url` → tạo `trucca.env` với mật khẩu PostgreSQL ngẫu nhiên (quyền 600) → `docker compose up --wait`
→ in địa chỉ truy cập. Đăng nhập lần đầu `admin` / `admin`, hệ thống bắt đổi mật khẩu ngay.

Không hỏi (tự động hoá): `./install.sh -u http://10.0.0.5:9093 -d /opt/trucca -p 8080 -y`
(`-i FILE` chỉ định file image, `--project NAME` đổi tên compose project, `-h` xem hướng dẫn).

**Nâng cấp:** `git pull`, tải file image version mới, chạy lại `./install.sh -d /opt/trucca`. Cấu hình (có sao lưu
`config.yml.bak.*`), mật khẩu và dữ liệu được giữ nguyên; migration DB tự chạy khi khởi động.

Sau khi cài, các lệnh quản lý (script in sẵn ở cuối):

```bash
docker compose -p trucca --env-file /opt/trucca/trucca.env -f ./docker-compose.yml logs -f trucca
docker compose -p trucca --env-file /opt/trucca/trucca.env -f ./docker-compose.yml down
```

Đóng gói image để phát hành (người build): `docker compose build && ./scripts/package-image.sh`
→ `dist/trucca-image-<version>.tar.gz`, `dist/postgres-<tag>.tar.gz` + `.sha256`, đính kèm vào GitHub Release `v<version>`.

## Triển khai thủ công

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
   Muốn lưu cấu hình/dữ liệu ở thư mục khác: đặt `TRUCCA_CONFIG_DIR`, `TRUCCA_DATA_DIR` trong `.env`.

### Build và đẩy image lên registry nội bộ

```bash
export TRUCCA_IMAGE=registry.local/noc/trucca TRUCCA_VERSION=2.1.0
docker build \
  --build-arg APP_VERSION=$TRUCCA_VERSION \
  --build-arg BUILD_DATE=$(date -u +%FT%TZ) \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  -t $TRUCCA_IMAGE:$TRUCCA_VERSION -t $TRUCCA_IMAGE:latest .
docker push $TRUCCA_IMAGE:$TRUCCA_VERSION && docker push $TRUCCA_IMAGE:latest
```

Máy chạy chỉ cần `docker-compose.yml`, `.env`, thư mục `config/` rồi `docker compose pull && docker compose up -d`.
Phiên bản hiển thị ở **Quản trị → Hệ thống** và `/healthz` lấy từ build arg `APP_VERSION`.

---

## Cấu hình

`config/config.yml` được mount **read-only** vào `/app/config/config.yml`. File mẫu có chú thích đầy đủ:
[`config/config.example.yml`](config/config.example.yml).

**Thứ tự ưu tiên:** biến môi trường > `config.yml` > mặc định. Mọi khoá ghi đè được bằng
`TRUCCA__<MỤC>__<KHOÁ>`, ví dụ `TRUCCA__ALERTMANAGER__POLL_INTERVAL_SECONDS=15`,
`TRUCCA__REPORT__TOP_N=10`. Secret chỉ đặt trong `.env`.

**Áp dụng thay đổi – không build lại image:**

- **Quản trị → Hệ thống → Nạp lại cấu hình**, hoặc
- chờ tối đa 10 giây (TrucCa tự phát hiện file thay đổi), hoặc
- `docker compose restart trucca`.

Mục `database`, `server` cần restart. Nếu file mới sai, TrucCa **giữ cấu hình cũ**, ghi log và hiện lỗi ở
trang Quản trị → Hệ thống. Khi khởi động với cấu hình sai, container dừng và in danh sách lỗi, ví dụ:

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
| `severity_mapping` | `critical`, `warning` | `[critical]`, `[warning]` | nhóm mức độ, so khớp không phân biệt hoa thường – xem [Mức độ](#mức-độ-severity) |
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

## Trang cảnh báo và Excel

Cột (web và Excel cùng thứ tự):

`STT | Tên cảnh báo | Summary | Thời gian bắt đầu | Thời gian tồn | Người xử lý | Hệ thống`

Excel có thêm 2 cột cuối: `Mức độ` (Critical / Warning / Khác) và `Số giờ tồn` (số, để lọc/sắp xếp trong Excel).

- **Thời gian bắt đầu** = `startsAt` của cảnh báo, hiển thị `dd/MM/yyyy HH:mm:ss` theo `timezone` trong config
  (mặc định Asia/Ho_Chi_Minh). Trong Excel là kiểu ngày giờ thật (định dạng `dd/mm/yyyy hh:mm:ss`), lọc/sắp xếp được.
- **Sắp xếp mặc định: cảnh báo bắt đầu MỚI NHẤT ở trên cùng** (`startsAt` giảm dần). Bấm tiêu đề cột để đổi
  (bấm lại để đảo chiều). Khi bật "Gộp cảnh báo", thời gian bắt đầu của nhóm là cảnh báo sớm nhất trong nhóm.
- **STT** đánh lại theo đúng thứ tự đang hiển thị sau khi lọc / sắp xếp.
- **Xuất Excel** dùng đúng bộ lọc và thứ tự sắp xếp đang hiển thị trên web (không phân trang).
- Báo cáo cuối ca (PDF): mục "Cảnh báo tồn cần bàn giao" có thêm thời gian bắt đầu, **vẫn sắp theo thời gian tồn
  giảm dần** như mẫu; bố cục và các mục khác giữ nguyên.

### Tra cứu cảnh báo cũ (xem tại một thời điểm)

Ô **"Xem tại thời điểm"** phía trên bộ lọc (có nút chọn nhanh 1 / 6 / 24 giờ trước):

- **Để trống (mặc định):** hiển thị cảnh báo đang diễn ra, tự làm mới như bình thường.
- **Chọn một thời điểm:** hiển thị các cảnh báo **đang diễn ra tại thời điểm đó** (bắt đầu trước hoặc đúng lúc đó và
  chưa clear tại lúc đó), kèm cột **Trạng thái** hiện tại: *Đang diễn ra* (chưa clear tới nay) hoặc *Đã clear lúc …*
  (kèm tổng thời gian tồn). "Thời gian tồn" và thanh nhóm tuổi tính **tới thời điểm đã chọn**.
- Bộ lọc, tìm kiếm, sắp xếp, gộp nhóm, số đếm mức độ và **Xuất Excel** đều áp dụng cho thời điểm đã chọn
  (file `canh-bao-tai-thoi-diem_<YYYYMMDD_HHmm>.xlsx`, thêm cột "Trạng thái" và "Thời điểm clear").
- Thời điểm nhập theo giờ hệ thống (`timezone`), không chọn được thời điểm ở tương lai; chế độ này không tự làm mới.
  Bấm **"Về hiện tại"** để quay lại. Link có tham số `at=` nên gửi được cho người khác.
- Dữ liệu lấy từ lịch sử phát sinh (`alert_occurrences`), có từ lúc TrucCa bắt đầu thu thập và giữ `retention_days`
  ngày (mặc định 90).

API: `GET /api/alerts?at=2026-11-12T08:30:00` (giờ theo `timezone`, hoặc có múi giờ như `...Z`); cũng áp dụng cho
`/api/alerts/filters` và `/api/alerts/export.xlsx`.

API (dùng nội bộ cho giao diện): `GET /api/alerts?sort=starts_at|duration|alertname|summary|owner|application|severity&order=desc|asc`
– mặc định `sort=starts_at&order=desc`.

## Mức độ (severity)

Label `severity` được chuẩn hoá về 3 nhóm **critical / warning / other** (hiển thị Critical / Warning / Khác):
bỏ khoảng trắng hai đầu và **so khớp không phân biệt hoa thường** – `Warning`, `WARNING`, `warning`, `" warning "`
đều là Warning. Giá trị không thuộc nhóm nào (vd `info`) hoặc thiếu label → **Khác**.

```yaml
severity_mapping:
  critical: [critical]          # thêm biến thể khác nếu cần, vd [critical, page, p1]
  warning: [warning]            # vd [warning, warn, minor]
```

- Áp dụng nhất quán cho: bộ lọc "Mức độ", badge màu, số đếm Critical/Warning/Khác (bấm để lọc), Excel và API
  (`severity=warning`, `severity=WARNING`, `severity=Warning` cho cùng kết quả; nhận thêm `other`/`Khác`).
- Lưu trong DB: cột `severity` giữ **giá trị gốc**, `labels` giữ nguyên toàn bộ label; cột mới `severity_normalized`
  lưu nhóm chuẩn (bảng `alerts` và `alert_occurrences`). Di chuột vào badge để xem giá trị gốc.
- Đổi `severity_mapping` **không cần build lại image**: nạp lại cấu hình (tự động ≤ 10 giây, hoặc trang
  Quản trị → Hệ thống → "Nạp lại cấu hình", hoặc restart container); toàn bộ dữ liệu hiện tại và lịch sử được
  chuẩn hoá lại theo mapping mới.
- Báo cáo cuối ca hiện không có mục thống kê theo mức độ (giữ bố cục mẫu).

## Người dùng và phân quyền (module Quản trị)

Menu **Quản trị** (`/admin`) gộp 3 mục:

| Mục | URL | Xem (`viewer`) | Quản trị (`admin`) |
|---|---|:-:|:-:|
| Người dùng | `/admin/users` | 403 | ✓ |
| Hệ thống (trạng thái, cấu hình, nạp lại cấu hình, nhật ký) | `/admin/system` | 403 | ✓ |
| Đổi mật khẩu (của chính mình) | `/admin/password` | ✓ | ✓ |

- `/admin` tự chuyển tới mục đầu tiên được phép (admin → Người dùng, viewer → Đổi mật khẩu). Viewer chỉ thấy mục
  Đổi mật khẩu; quyền được kiểm tra ở **từng route/API** phía server (gọi trái quyền trả 403), không chỉ ẩn giao diện.
- URL cũ vẫn dùng được: `/account` → chuyển hướng 301 tới `/admin/password`. `/admin/users`, `/admin/system` giữ nguyên.
- API đổi mật khẩu: `POST /api/admin/password` (mọi người dùng đã đăng nhập, chỉ đổi của chính mình, cần mật khẩu
  hiện tại). API cũ `POST /api/account/password` **deprecated** – vẫn hoạt động, trả thêm header `Deprecation: true`.

| Chức năng | Xem (`viewer`) | Quản trị (`admin`) |
|---|:-:|:-:|
| Xem cảnh báo, lọc, tìm kiếm, xuất Excel | ✓ | ✓ |
| Xem / tải báo cáo ca | ✓ | ✓ |
| Đổi mật khẩu của mình | ✓ | ✓ |
| Chốt lại & sinh lại PDF báo cáo | | ✓ |
| Thêm / sửa / khoá / xoá người dùng, đặt lại mật khẩu | | ✓ |
| Quản trị → Hệ thống: trạng thái, cấu hình, nạp lại cấu hình, nhật ký thao tác | | ✓ |

- Admin đầu tiên tạo khi DB chưa có người dùng (mặc định `admin` / `admin`, hoặc theo `.env`); lần đăng nhập đầu **bắt buộc đổi mật khẩu**.
- Người dùng mới / bị đặt lại mật khẩu cũng phải đổi mật khẩu ở lần đăng nhập kế tiếp.
- Mật khẩu: tối thiểu `min_password_length` ký tự, 3 trong 4 loại (thường, hoa, số, ký tự đặc biệt), băm scrypt.
- Tối đa 4 lần băm mật khẩu chạy cùng lúc (mỗi lần ~32 MiB RAM); request đăng nhập dồn dập sẽ phải chờ lượt
  thay vì làm cạn RAM/CPU của container.
- Sai mật khẩu `max_failed_attempts` lần → khoá `lockout_minutes` phút (admin có thể mở khoá).
- Luôn phải còn ít nhất 1 admin đang hoạt động; không tự xoá được tài khoản của mình.
- Đổi vai trò / vô hiệu hoá / đặt lại mật khẩu → các phiên của người đó bị đăng xuất ngay.
- Tải lại trang không làm mất đăng nhập. Ô **"Ghi nhớ đăng nhập"** (mặc định bật) giữ phiên `remember_days` ngày
  (30) kể cả khi đóng trình duyệt, tự gia hạn khi còn dùng. Bỏ chọn (máy dùng chung): đăng nhập mất khi đóng
  trình duyệt, hoặc sau `session_ttl_hours` giờ (12) không hoạt động. TrucCa không lưu mật khẩu ở trình duyệt;
  việc lưu mật khẩu (nếu muốn) do trình quản lý mật khẩu của trình duyệt đảm nhận.
- Mọi đăng nhập, thay đổi người dùng, nạp cấu hình, sinh báo cáo đều ghi vào nhật ký (Quản trị → Hệ thống).

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
| Trạng thái thu thập, tác vụ, cấu hình | **Quản trị → Hệ thống** (admin) |
| Sao lưu DB | `docker compose exec postgres pg_dump -U trucca trucca > backup.sql` |
| Khôi phục | `docker compose exec -T postgres psql -U trucca trucca < backup.sql` |
| Nâng cấp | đổi `TRUCCA_VERSION`, `docker compose pull && docker compose up -d` (migration tự chạy) |

Log không chứa mật khẩu/token; trang Quản trị → Hệ thống hiển thị secret dạng `**********`.

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
| Quên mật khẩu | xem mục [Người dùng và phân quyền](#người-dùng-và-phân-quyền-module-quản-trị) |

---

## Phát triển

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q                    # unit test: thời gian tồn, ca qua đêm, phát sinh/clear/tồn, top N, so sánh hôm qua,
                             # bàn giao, reconcile, Excel (đọc lại bằng openpyxl), chuẩn hoá severity, sắp xếp, config
```

### Test tích hợp (PostgreSQL thật)

Kiểm tra API lọc mức độ, sắp xếp, Excel qua API, migration chạy lặp lại, phân quyền module Quản trị, redirect URL cũ.
Cần một PostgreSQL dùng riêng cho test (user có quyền `CREATE DATABASE`; test tạo/xoá database `trucca_it`).
Không đặt `TRUCCA_TEST_DATABASE_URL` thì các test này được bỏ qua và pytest báo rõ lý do.

```bash
docker run -d --rm --name trucca-test-pg -e POSTGRES_USER=trucca_test -e POSTGRES_PASSWORD=<mật-khẩu> \
  -p 127.0.0.1:55432:5432 postgres:16.16-alpine
export TRUCCA_TEST_DATABASE_URL=postgresql+psycopg://trucca_test:<mật-khẩu>@127.0.0.1:55432/postgres
pytest -q                    # unit + tích hợp
docker stop trucca-test-pg
```

### Quy trình phát hành

1. `ruff check .` và `pytest -q` (có `TRUCCA_TEST_DATABASE_URL`) – tất cả phải pass.
   Quét CVE thư viện trong image (cần `pip install pip-audit`):
   `docker run --rm -i --entrypoint python registry.local/noc/trucca:$TRUCCA_VERSION - < scripts/freeze.py > /tmp/freeze.txt && pip-audit -r /tmp/freeze.txt --disable-pip --no-deps`
   – phải ra `No known vulnerabilities found`.
2. Thử end-to-end: `docker compose --profile demo up -d --wait` với mock (đặt `alertmanager.url` tới mock),
   kiểm tra trang cảnh báo, Excel, lọc mức độ, module Quản trị, sinh báo cáo ca.
3. Tăng `TRUCCA_VERSION` trong `.env` (semver), `docker compose build`, gắn thêm tag `latest`:
   `docker tag registry.local/noc/trucca:$TRUCCA_VERSION registry.local/noc/trucca:latest`.
4. `docker compose up -d --wait` – migration tự chạy khi khởi động; kiểm tra container `healthy`.
5. Cập nhật `CHANGELOG.md`.

Chạy local cần PostgreSQL (`DATABASE_URL`) và `TRUCCA_CONFIG=config/config.yml`:
`python -m trucca.main`. Render PDF cần `playwright install chromium`.
