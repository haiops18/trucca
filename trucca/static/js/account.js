(function () {
  "use strict";
  const form = document.getElementById("pw-form");
  const err = document.getElementById("pw-error");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    const cur = document.getElementById("pw-current").value;
    const nw = document.getElementById("pw-new").value;
    const cf = document.getElementById("pw-confirm").value;
    if (!cur || !nw) { err.textContent = "Nhập đầy đủ mật khẩu hiện tại và mật khẩu mới."; return; }
    if (nw !== cf) { err.textContent = "Mật khẩu nhập lại không khớp."; document.getElementById("pw-confirm").focus(); return; }
    const btn = form.querySelector("button[type=submit]");
    TC.busy(btn, true, "Đang lưu…");
    try {
      const r = await TC.api("/api/account/password", { method: "POST", body: { current_password: cur, new_password: nw } });
      TC.toast("Đã đổi mật khẩu.");
      setTimeout(() => { location.href = r.redirect; }, 600);
    } catch (e) { err.textContent = e.message; }
    finally { TC.busy(btn, false); }
  });
})();
