# Lịch sử thay đổi

## 1.1.0 – 2026-11-12

Chi tiết xem `CHANGELOG.md`.

- Cột "Thời gian bắt đầu" trên web và Excel; sắp xếp mặc định theo thời gian bắt đầu giảm dần.
- Chuẩn hoá mức độ không phân biệt hoa thường (`severity_mapping`), migration `0003` chuẩn hoá dữ liệu lịch sử.
- Module "Quản trị" gộp Người dùng / Hệ thống / Đổi mật khẩu; `/account` chuyển hướng tới `/admin/password`.
- Đã gộp các thay đổi 1.0.1 bên dưới (chưa từng phát hành riêng).

## 1.0.1 – 2026-11-12 (gộp vào 1.1.0)

- Tài khoản quản trị mặc định `admin` / `admin` khi DB chưa có người dùng và `.env` không đặt
  `TRUCCA_ADMIN_USERNAME` / `TRUCCA_ADMIN_PASSWORD`; bắt buộc đổi mật khẩu ở lần đăng nhập đầu.
- docker compose: mock Alertmanager mở cổng host `MOCK_AM_PORT` (mặc định 19093).
- Logo đổi thành chữ **VTrucca** (chữ V màu vàng nhấn), favicon chữ V.
- Thêm "Ghi nhớ đăng nhập" (mặc định bật, `auth.remember_days` = 30): phiên sống qua việc tải lại trang/đóng
  trình duyệt, tự gia hạn khi còn dùng. Migration `0002` thêm cột `sessions.remember`.
- Sửa lỗi: cookie phiên trước đây hết hạn cứng sau 12 giờ kể từ lúc đăng nhập dù người dùng vẫn đang làm việc.

## 1.0.0 – 2026-11-12

Phiên bản đầu tiên của TrucCa.

- Dữ liệu mock/test dùng tên hệ thống giả; không kèm PDF mẫu trong repo (tránh lộ thông tin nội bộ).

- Thu thập cảnh báo từ Alertmanager API v2 mỗi 30 giây (chỉ polling, không webhook), lưu lịch sử theo
  fingerprint vào PostgreSQL; chống clear hàng loạt khi mất kết nối hoặc Alertmanager trả danh sách rỗng.
- Trang cảnh báo đang diễn ra: tìm kiếm, lọc, sắp xếp, phân trang, gộp nhóm ×N, thanh "thời gian tồn" kiêm bộ lọc,
  tự làm mới, xuất Excel theo bộ lọc hiện tại (chống formula injection).
- Báo cáo cuối ca theo bố cục báo cáo mẫu do NOC cung cấp, xuất PDF A4, tự chốt
  snapshot + sinh PDF khi hết ca. Bỏ mục "Đơn vị sản phẩm" và "Sự cố" theo yêu cầu.
- Sửa 2 điểm lệch của mẫu: dòng tiêu đề lấy đúng ca được chọn (mẫu ghi "Ca 1" trong báo cáo CA_2);
  mục bàn giao giới hạn 8 nhóm để báo cáo vừa 1 trang.
- Đăng nhập tài khoản nội bộ, 2 vai trò Xem / Quản trị, khoá khi sai mật khẩu nhiều lần, nhật ký thao tác.
- Đóng gói Docker (non-root, HEALTHCHECK), docker compose kèm PostgreSQL và mock Alertmanager (profile `demo`).
