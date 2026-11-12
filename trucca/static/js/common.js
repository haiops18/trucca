// Tiện ích dùng chung: gọi API (kèm CSRF), escape HTML, thông báo, định dạng thời gian.
(function () {
  "use strict";
  const base = document.body.dataset.base || "";
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrf = csrfMeta ? csrfMeta.content : "";

  class ApiError extends Error {
    constructor(message, status, code) { super(message); this.status = status; this.code = code; }
  }

  async function api(path, opts = {}) {
    const method = opts.method || "GET";
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = csrf;
    let body;
    if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeout || 30000);
    let resp;
    try {
      resp = await fetch(base + path, { method, headers, body, credentials: "same-origin", signal: opts.signal || ctrl.signal });
    } catch (e) {
      if (e.name === "AbortError") throw new ApiError("Máy chủ phản hồi quá lâu. Kiểm tra kết nối rồi thử lại.", 0, "timeout");
      throw new ApiError("Không kết nối được tới máy chủ TrucCa. Kiểm tra mạng rồi thử lại.", 0, "network");
    } finally { clearTimeout(timer); }
    let data = null;
    const ctype = resp.headers.get("content-type") || "";
    if (ctype.includes("application/json")) { try { data = await resp.json(); } catch (e) { data = null; } }
    if (!resp.ok) {
      const err = (data && data.error) || {};
      if (resp.status === 401 && !opts.noRedirect) { location.href = base + "/login"; }
      if (resp.status === 403 && err.code === "must_change_password") { location.href = base + "/account"; }
      let msg = err.message || (resp.status >= 500 ? "Máy chủ gặp lỗi (HTTP " + resp.status + "). Thử lại sau ít phút." : "Yêu cầu không thành công (HTTP " + resp.status + ").");
      throw new ApiError(msg, resp.status, err.code || "http");
    }
    return data;
  }

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ESC[c]); }

  function toast(msg, type) {
    const region = document.getElementById("toasts");
    if (!region) return;
    const el = document.createElement("div");
    el.className = "toast" + (type === "error" ? " error" : "");
    el.textContent = msg;
    region.appendChild(el);
    setTimeout(() => el.remove(), type === "error" ? 8000 : 4000);
  }

  const timeFmt = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const dateTimeFmt = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  function fmtTime(iso) { return iso ? timeFmt.format(new Date(iso)) : "–"; }
  function fmtDateTime(iso) { return iso ? dateTimeFmt.format(new Date(iso)) : "–"; }
  function fmtNum(n) { return new Intl.NumberFormat("vi-VN").format(n || 0); }

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) {
      btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.setAttribute("aria-busy", "true");
      btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>' + esc(label || "Đang xử lý…");
    } else {
      btn.disabled = false; btn.removeAttribute("aria-busy");
      if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
    }
  }

  function confirmDialog(text, okLabel) {
    const dlg = document.getElementById("confirm-dialog");
    if (!dlg) return Promise.resolve(window.confirm(text));
    document.getElementById("confirm-text").textContent = text;
    document.getElementById("confirm-ok").textContent = okLabel || "Đồng ý";
    dlg.returnValue = "";
    dlg.showModal();
    return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
  }

  // Thanh điều hướng: đăng xuất, đổi theme
  document.addEventListener("DOMContentLoaded", () => {
    const lo = document.getElementById("logout-btn");
    if (lo) lo.addEventListener("click", async () => {
      try { const r = await api("/auth/logout", { method: "POST", noRedirect: true }); location.href = r.redirect; }
      catch (e) { location.href = base + "/login"; }
    });
    const tt = document.getElementById("theme-toggle");
    if (tt) tt.addEventListener("click", () => {
      const root = document.documentElement;
      const cur = root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("trucca-theme", next); } catch (e) { /* bỏ qua */ }
    });
  });

  window.TC = { base, api, esc, toast, fmtTime, fmtDateTime, fmtNum, busy, confirmDialog, ApiError };
})();
