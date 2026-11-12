// Trang hệ thống (chỉ admin): trạng thái thu thập, cấu hình, nhật ký.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const JOBS = { poll: "Lấy cảnh báo từ Alertmanager", finalize_shifts: "Chốt báo cáo khi hết ca",
    config_watch: "Theo dõi thay đổi config.yml", retention: "Dọn dữ liệu cũ" };
  const ACTIONS = { "auth.login": "Đăng nhập", "auth.logout": "Đăng xuất", "auth.login_failed": "Đăng nhập thất bại",
    "user.create": "Tạo người dùng", "user.update": "Sửa người dùng", "user.delete": "Xoá người dùng",
    "user.reset_password": "Đặt lại mật khẩu", "user.password_changed": "Đổi mật khẩu", "user.bootstrap": "Tạo admin ban đầu",
    "config.reload": "Nạp lại cấu hình", "config.reload_failed": "Nạp cấu hình lỗi", "report.generate": "Sinh lại báo cáo" };

  function kv(el, pairs) {
    el.innerHTML = pairs.map(([k, v]) => "<dt>" + TC.esc(k) + "</dt><dd>" + v + "</dd>").join("");
  }

  async function load() {
    try {
      const s = await TC.api("/api/admin/system");
      const c = s.collector;
      kv($("collector"), [
        ["Trạng thái", c.last_error ? '<span class="status-bad">Lỗi – ' + TC.esc(c.last_error.message) + "</span>"
          : c.last_success_at ? '<span class="status-ok">Hoạt động bình thường</span>' : "Chưa lấy dữ liệu lần nào"],
        ["Alertmanager", TC.esc(s.config.alertmanager.url)],
        ["Chu kỳ lấy", TC.esc(s.config.alertmanager.poll_interval_seconds + " giây")],
        ["Lần gọi gần nhất", TC.esc(TC.fmtDateTime(c.last_attempt_at))],
        ["Thành công gần nhất", TC.esc(TC.fmtDateTime(c.last_success_at))],
        ["Lỗi liên tiếp", String(c.consecutive_failures)],
        ["Số cảnh báo lần cuối", TC.esc(TC.fmtNum(c.last_fetch_count)) + " (" + c.last_duration_ms + " ms)"],
        ["Phiên bản", TC.esc(s.version)],
      ]);
      kv($("jobs"), s.jobs.map((j) => [JOBS[j.id] || j.id, "Lần chạy tới: " + TC.esc(TC.fmtDateTime(j.next_run_time))]));
      $("config").textContent = JSON.stringify(s.config, null, 2);
      $("cfg-path").textContent = "File: " + s.config_path + " – sửa file rồi bấm Nạp lại cấu hình (hoặc chờ tối đa 10 giây để tự nạp).";
      $("cfg-error").hidden = !s.last_reload_error;
      $("cfg-error-text").textContent = s.last_reload_error || "";
    } catch (e) { TC.toast(e.message, "error"); }
    try {
      const rows = await TC.api("/api/admin/audit");
      $("audit").innerHTML = rows.length ? rows.map((r) =>
        "<tr><td>" + TC.esc(TC.fmtDateTime(r.created_at)) + "</td><td>" + TC.esc(r.username || "–") + "</td><td>" +
        TC.esc(ACTIONS[r.action] || r.action) + "</td><td>" + TC.esc(r.target || "") + "</td><td>" + TC.esc(r.ip || "") + "</td></tr>").join("")
        : '<tr><td colspan="5">Chưa có thao tác nào.</td></tr>';
    } catch (e) { $("audit").innerHTML = '<tr><td colspan="5">' + TC.esc(e.message) + "</td></tr>"; }
  }

  $("reload-config").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    TC.busy(btn, true, "Đang nạp…");
    try { const r = await TC.api("/api/admin/config/reload", { method: "POST" }); TC.toast(r.message); }
    catch (e) { TC.toast("Cấu hình không hợp lệ, đang giữ cấu hình cũ.", "error"); }
    finally { TC.busy(btn, false); load(); }
  });
  load();
})();
