// Quản lý người dùng (chỉ admin).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ROLE = { admin: "Quản trị", viewer: "Xem" };
  let users = [];
  let resetId = null;

  function statusText(u) {
    if (!u.is_active) return '<span class="status-bad">Vô hiệu hoá</span>';
    if (u.locked) return '<span class="status-bad">Tạm khoá</span>';
    if (u.must_change_password) return "Chờ đổi mật khẩu";
    return '<span class="status-ok">Hoạt động</span>';
  }

  function render() {
    const tb = $("users");
    if (!users.length) { tb.innerHTML = '<tr><td colspan="6">Chưa có người dùng.</td></tr>'; return; }
    tb.innerHTML = users.map((u) =>
      "<tr><td><b>" + TC.esc(u.username) + "</b></td><td>" + TC.esc(u.full_name || "") + "</td>" +
      '<td><label class="visually-hidden" for="role-' + u.id + '">Vai trò của ' + TC.esc(u.username) + '</label><select id="role-' + u.id + '" data-act="role" data-id="' + u.id + '">' +
      Object.entries(ROLE).map(([k, v]) => '<option value="' + k + '"' + (u.role === k ? " selected" : "") + ">" + v + "</option>").join("") + "</select></td>" +
      "<td>" + statusText(u) + "</td><td>" + TC.esc(TC.fmtDateTime(u.last_login_at)) + "</td>" +
      '<td><div class="row-actions">' +
      '<button type="button" class="btn btn-sm" data-act="toggle" data-id="' + u.id + '">' + (u.is_active ? "Vô hiệu hoá" : "Kích hoạt") + "</button>" +
      (u.locked ? '<button type="button" class="btn btn-sm" data-act="unlock" data-id="' + u.id + '">Mở khoá</button>' : "") +
      '<button type="button" class="btn btn-sm" data-act="reset" data-id="' + u.id + '">Đặt lại mật khẩu</button>' +
      '<button type="button" class="btn btn-sm btn-danger" data-act="delete" data-id="' + u.id + '">Xoá</button>' +
      "</div></td></tr>").join("");
  }

  async function load() {
    try { users = await TC.api("/api/admin/users"); render(); }
    catch (e) { $("users").innerHTML = '<tr><td colspan="6">' + TC.esc(e.message) + "</td></tr>"; }
  }

  async function patch(id, body, okMsg) {
    try { await TC.api("/api/admin/users/" + id, { method: "PATCH", body }); TC.toast(okMsg); }
    catch (e) { TC.toast(e.message, "error"); }
    load();
  }

  // Thêm người dùng
  const dlg = $("user-dialog");
  $("new-user").addEventListener("click", () => {
    $("user-form").reset(); $("user-error").textContent = ""; dlg.showModal(); $("u-username").focus();
  });
  $("user-form").addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    $("user-error").textContent = "";
    const btn = $("user-save");
    TC.busy(btn, true, "Đang lưu…");
    try {
      await TC.api("/api/admin/users", { method: "POST", body: {
        username: $("u-username").value.trim(), full_name: $("u-fullname").value.trim(),
        role: $("u-role").value, password: $("u-password").value } });
      dlg.close(); TC.toast("Đã thêm người dùng."); load();
    } catch (err) { $("user-error").textContent = err.message; }
    finally { TC.busy(btn, false); }
  });

  // Đặt lại mật khẩu
  const rdlg = $("reset-dialog");
  $("reset-form").addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    $("reset-error").textContent = "";
    try {
      await TC.api("/api/admin/users/" + resetId + "/reset-password", { method: "POST", body: { new_password: $("r-password").value } });
      rdlg.close(); TC.toast("Đã đặt lại mật khẩu. Người dùng phải đổi mật khẩu khi đăng nhập."); load();
    } catch (err) { $("reset-error").textContent = err.message; }
  });

  $("users").addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.act === "role") patch(t.dataset.id, { role: t.value }, "Đã đổi vai trò.");
  });
  $("users").addEventListener("click", async (e) => {
    const t = e.target.closest("button[data-act]");
    if (!t) return;
    const u = users.find((x) => String(x.id) === t.dataset.id);
    if (!u) return;
    if (t.dataset.act === "toggle") {
      if (u.is_active && !(await TC.confirmDialog("Vô hiệu hoá tài khoản " + u.username + "? Người dùng sẽ bị đăng xuất ngay.", "Vô hiệu hoá"))) return;
      patch(u.id, { is_active: !u.is_active }, u.is_active ? "Đã vô hiệu hoá." : "Đã kích hoạt.");
    } else if (t.dataset.act === "unlock") {
      patch(u.id, { unlock: true }, "Đã mở khoá.");
    } else if (t.dataset.act === "reset") {
      resetId = u.id; $("reset-form").reset(); $("reset-error").textContent = "";
      $("reset-who").textContent = "Tài khoản: " + u.username; rdlg.showModal(); $("r-password").focus();
    } else if (t.dataset.act === "delete") {
      if (!(await TC.confirmDialog("Xoá vĩnh viễn tài khoản " + u.username + "? Thao tác không hoàn tác được.", "Xoá tài khoản"))) return;
      try { await TC.api("/api/admin/users/" + u.id, { method: "DELETE" }); TC.toast("Đã xoá tài khoản."); }
      catch (err) { TC.toast(err.message, "error"); }
      load();
    }
  });

  load();
})();
