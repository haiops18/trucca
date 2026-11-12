# Lịch sử thay đổi

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
