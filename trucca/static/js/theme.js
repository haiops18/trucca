// Áp dụng giao diện sáng/tối đã chọn trước khi trang hiển thị (tránh nháy màu)
(function () {
  try {
    var t = localStorage.getItem("trucca-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* localStorage có thể bị chặn */ }
})();
