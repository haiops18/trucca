# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), phiên bản theo [SemVer](https://semver.org/).
Ghi chú vận hành chi tiết hơn ở `history.md`.

## [2.1.1] – 2026-11-13

### Security
- Nâng `starlette` 0.41.3 → 1.10.0 (qua `fastapi` 0.115.6 → 0.162.5): sửa CVE-2025-62727 (header `Range` khiến
  CPU tăng bậc hai khi tải file tĩnh/PDF – DoS không cần đăng nhập), CVE-2025-54121, CVE-2026-48710,
  CVE-2026-54282, CVE-2026-54283 và các lỗi khác của Starlette.
- Nâng `Jinja2` 3.1.5 → 3.1.6 (CVE-2025-27516; TrucCa không dùng sandbox nên không bị ảnh hưởng trực tiếp).
- Giới hạn tối đa 4 lần băm mật khẩu (scrypt, ~32 MiB/lần) chạy đồng thời, để một loạt request đăng nhập không làm
  cạn RAM/CPU của container.
- Image PostgreSQL `16.4-alpine` → `16.16-alpine` (bản vá bảo mật cùng dòng 16, không cần chuyển đổi dữ liệu).

### Migration notes
- Không có thay đổi DB. Đổi image PostgreSQL chỉ khởi động lại container `postgres`, dữ liệu giữ nguyên.

## [2.1.0] – 2026-11-12

### Added
- **Tra cứu cảnh báo cũ** trên trang cảnh báo: ô "Xem tại thời điểm" (+ chọn nhanh 1/6/24 giờ trước). Để trống =
  cảnh báo đang diễn ra như cũ; chọn thời điểm = các cảnh báo đang diễn ra tại lúc đó kèm cột "Trạng thái"
  (Đang diễn ra / Đã clear lúc …). Thời gian tồn, nhóm tuổi tính tới thời điểm đã chọn; bộ lọc, sắp xếp, gộp nhóm,
  số đếm mức độ và Excel đều áp dụng.
- API: tham số `at` cho `/api/alerts`, `/api/alerts/filters`, `/api/alerts/export.xlsx` (400 nếu sai định dạng
  hoặc ở tương lai). Excel chế độ tra cứu thêm cột "Trạng thái", "Thời điểm clear".

### Migration notes
- Không có thay đổi DB; dữ liệu tra cứu lấy từ bảng `alert_occurrences` sẵn có.

## [2.0.0] – 2026-11-12

Phiên bản major vì thay đổi hành vi mặc định (sắp xếp theo thời gian bắt đầu, cột Excel, URL module Quản trị,
địa chỉ trang đổi mật khẩu). URL và API cũ vẫn được giữ tương thích (redirect / deprecated).

### Added
- **`install.sh`**: cài đặt / nâng cấp một lệnh – hỏi địa chỉ Alertmanager, thư mục lưu cấu hình + dữ liệu, cổng;
  tự `docker load` file image (kiểm tra SHA-256), tạo cấu hình, sinh mật khẩu PostgreSQL, chạy docker compose.
- Release kèm image PostgreSQL (`postgres-16.4-alpine.tar.gz`); `install.sh` tự tải cả 2 image từ Release
  (`gh` hoặc `curl`) nên cài được chỉ với `git clone` + `./install.sh`, hoặc offline khi chép sẵn file.
- `scripts/package-image.sh`: đóng gói image thành `trucca-image-<version>.tar.gz` (+ `.sha256`) để phát hành.
- `docker-compose.yml`: thư mục cấu hình / dữ liệu / file env cấu hình được qua `TRUCCA_CONFIG_DIR`,
  `TRUCCA_DATA_DIR`, `TRUCCA_ENV_FILE` (mặc định giữ nguyên `./config`, `./data`, `.env`).
- Cột **"Thời gian bắt đầu"** (`startsAt`, `dd/MM/yyyy HH:mm:ss` theo `timezone`) ngay trước "Thời gian tồn" trên
  bảng cảnh báo và trong Excel. Trong Excel là kiểu ngày giờ thật, định dạng `dd/mm/yyyy hh:mm:ss`.
- Excel thêm cột "Mức độ" (Critical / Warning / Khác) sau "Hệ thống".
- Chuẩn hoá **mức độ (severity)** không phân biệt hoa thường / khoảng trắng về 3 nhóm critical / warning / other,
  cấu hình bằng `severity_mapping` trong `config.yml` (đổi không cần build lại image).
- Badge màu mức độ, số đếm Critical / Warning / Khác (bấm để lọc) trên trang cảnh báo.
- **Module "Quản trị"** (`/admin`) gộp Người dùng, Hệ thống, Đổi mật khẩu; có breadcrumb và các mục con.
- API `POST /api/admin/password` (đổi mật khẩu của chính mình).
- Mục "Cảnh báo tồn cần bàn giao" trong báo cáo ca có thêm thời gian bắt đầu.
- "Ghi nhớ đăng nhập" (mặc định bật, `auth.remember_days` = 30).
- Test tích hợp với PostgreSQL thật (`tests/integration`, cần `TRUCCA_TEST_DATABASE_URL`).

### Changed
- Sắp xếp mặc định của bảng cảnh báo và Excel: **thời gian bắt đầu giảm dần** (mới nhất trên cùng);
  trước đây là thời gian tồn giảm dần. Excel xuất đúng thứ tự đang hiển thị trên web; STT đánh lại theo thứ tự đó.
- Khi gộp nhóm, các nhóm được sắp theo thời gian bắt đầu (cảnh báo sớm nhất của nhóm) khi sắp theo thời gian.
- Bộ lọc "Mức độ" (web + API `severity=`) lọc theo nhóm chuẩn: `warning` khớp Warning / WARNING / warning.
  Giá trị không phải nhóm chuẩn vẫn được nhận và so khớp với giá trị gốc không phân biệt hoa thường.
- Menu chính: bỏ "Người dùng", "Hệ thống" rời rạc, thay bằng "Quản trị". Người dùng thường chỉ thấy mục Đổi mật khẩu.
- Người dùng phải đổi mật khẩu được chuyển tới `/admin/password` (trước là `/account`).
- Logo "VTrucca"; tài khoản quản trị mặc định `admin` / `admin` (bắt buộc đổi ở lần đăng nhập đầu).

### Deprecated
- `POST /api/account/password` – dùng `POST /api/admin/password`. API cũ vẫn chạy, trả header `Deprecation: true`.
- URL `/account` – chuyển hướng 301 tới `/admin/password`.

### Fixed
- Cookie phiên hết hạn cứng 12 giờ sau đăng nhập dù người dùng vẫn đang làm việc.

### Migration notes
- `0002`: thêm `sessions.remember`.
- `0003`: thêm `severity_normalized` (varchar(16), NOT NULL, mặc định `other`) cho `alerts` và `alert_occurrences`,
  index `ix_alerts_severity_norm`, và chuẩn hoá dữ liệu lịch sử. An toàn khi chạy lại (`IF NOT EXISTS`, UPDATE chỉ
  đụng dòng chưa đúng); không sửa giá trị gốc (`severity`, `labels`). Khi khởi động, ứng dụng tính lại theo
  `severity_mapping` hiện hành.
- Migration chạy tự động khi container khởi động. Nên sao lưu DB trước khi nâng cấp
  (`docker compose exec postgres pg_dump -U trucca trucca > backup.sql`).
- Không cần đổi `config.yml`; thêm `severity_mapping` nếu Alertmanager dùng giá trị mức độ khác `critical`/`warning`.

## [1.0.0] – 2026-11-12

### Added
- Phiên bản đầu tiên: thu thập cảnh báo từ Alertmanager mỗi 30 giây, trang cảnh báo đang diễn ra, xuất Excel,
  báo cáo cuối ca PDF, đăng nhập 2 vai trò Xem / Quản trị, Docker + docker compose kèm PostgreSQL và mock.
