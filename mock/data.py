"""Dữ liệu mẫu dùng chung cho mock Alertmanager và script seed lịch sử."""

APPLICATIONS = [
    "Cong_thong_tin", "CSDL_Dan_cu", "CSDL_Dan_cu_Replica", "Van_phong_dien_tu", "Hoc_truc_tuyen",
    "Dich_vu_cong_C1", "Quan_ly_ho_tich", "So_lien_lac", "Tiem_chung", "Quan_ly_kho", "Bao_hiem_Portal",
    "Benh_vien_HIS", "Dieu_hanh_Tinh", "Dinh_danh_Gateway", "Payment_Hub",
]
OWNERS = ["nguyenvana", "tranthib", "lequangc", "phamthid", "hoangvane", "", "doanthif"]
# (alertname, summary template, severity, trọng số)
ALERTS = [
    ("Node high storage usage", "Node high storage usage", "warning", 22),
    ("Node rebooted", "Node rebooted", "warning", 12),
    ("Node mount points missing", "Mount point /data missing on {inst}", "critical", 9),
    ("Server Down", "Server Down", "critical", 9),
    ("Node high io usage", "Node high io usage", "warning", 8),
    ("APISIXTargetDown", "APISIXTargetDown", "critical", 6),
    ("HighCPUUsage", "CPU > 90% trên {inst} trong 10 phút", "warning", 7),
    ("MemoryPressure", "Bộ nhớ khả dụng < 5% trên {inst}", "warning", 5),
    ("PostgresReplicationLag", "Độ trễ replication > 60s", "critical", 3),
    ("HTTP5xxRateHigh", "Tỷ lệ HTTP 5xx > 5% (=SUM test injection)", "critical", 4),
    ("CertificateExpiring", "Chứng chỉ TLS hết hạn trong 7 ngày", "info", 2),
]
