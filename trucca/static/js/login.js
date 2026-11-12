(function () {
  "use strict";
  const form = document.getElementById("login-form");
  const errBox = document.getElementById("login-error");
  const errText = document.getElementById("login-error-text");
  const btn = document.getElementById("login-submit");
  function showError(msg) { errText.textContent = msg; errBox.hidden = false; }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errBox.hidden = true;
    const username = form.username.value.trim();
    const password = form.password.value;
    form.username.setAttribute("aria-invalid", String(!username));
    form.password.setAttribute("aria-invalid", String(!password));
    if (!username || !password) { showError("Nhập tên đăng nhập và mật khẩu."); return; }
    TC.busy(btn, true, "Đang đăng nhập…");
    try {
      const r = await TC.api("/auth/login", { method: "POST", body: { username, password, remember: form.remember.checked }, noRedirect: true });
      location.href = r.redirect;
    } catch (e) {
      showError(e.message);
      form.password.value = "";
      form.password.focus();
    } finally { TC.busy(btn, false); }
  });
})();
