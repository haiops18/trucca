// Trang báo cáo cuối ca: chọn ngày + ca, xem trước (iframe), tải PDF, danh sách đã chốt.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const frame = $("report-frame");
  const loading = $("frame-loading");
  const err = $("report-error");

  function params() { return "date=" + encodeURIComponent($("r-date").value) + "&shift=" + encodeURIComponent($("r-shift").value); }

  function updateLinks() {
    $("dl-pdf").href = TC.base + "/api/reports/shift?" + params() + "&format=pdf&download=1";
  }

  async function preview() {
    err.textContent = "";
    if (!$("r-date").value || !$("r-shift").value) { err.textContent = "Chọn ngày và ca trực."; return; }
    updateLinks();
    history.replaceState(null, "", location.pathname + "?" + params());
    loading.hidden = false;
    // Kiểm tra trước bằng JSON để hiển thị lỗi rõ ràng thay vì trang lỗi trong iframe
    try {
      await TC.api("/api/reports/shift?" + params() + "&format=json");
      frame.src = TC.base + "/api/reports/shift?" + params() + "&format=html&_=" + Date.now();
    } catch (e) {
      loading.hidden = true;
      err.textContent = e.message;
    }
  }
  frame.addEventListener("load", () => { loading.hidden = true; });

  async function loadHistory() {
    const ul = $("history");
    try {
      const rows = await TC.api("/api/reports");
      if (!rows.length) { ul.innerHTML = "<li>Chưa có báo cáo nào được chốt. Báo cáo được chốt tự động khi hết ca.</li>"; return; }
      ul.innerHTML = rows.map((r) => {
        const t = r.totals || {};
        const q = "date=" + encodeURIComponent(r.date) + "&shift=" + encodeURIComponent(r.shift);
        const dl = r.has_pdf
          ? '<a class="btn btn-sm" href="' + TC.base + "/api/reports/shift?" + q + '&format=pdf&download=1" aria-label="Tải PDF ' + TC.esc(r.shift + " " + r.date) + '">PDF</a>'
          : '<span class="sev" title="Chưa có file PDF">chưa có PDF</span>';
        return '<li><div><a href="?' + q + '" data-date="' + TC.esc(r.date) + '" data-shift="' + TC.esc(r.shift) + '"><b>' + TC.esc(r.shift) + "</b> " +
          TC.esc(r.date.split("-").reverse().join("/")) + '</a><div class="nums">Phát sinh <b>' + (t.new ?? 0) + "</b>, clear <b>" + (t.cleared ?? 0) +
          "</b>, tồn <b>" + (t.carried_over ?? "--") + "</b></div></div>" + dl + "</li>";
      }).join("");
    } catch (e) { ul.innerHTML = "<li>" + TC.esc(e.message) + "</li>"; }
  }

  async function init() {
    let status;
    try { status = await TC.api("/api/status"); }
    catch (e) { err.textContent = e.message; return; }
    $("r-shift").innerHTML = status.shifts.map((s) => '<option value="' + TC.esc(s.name) + '">' + TC.esc(s.name + " (" + s.label + ")") + "</option>").join("");
    const p = new URLSearchParams(location.search);
    const cur = status.current_shift;
    $("r-date").value = p.get("date") || (cur ? cur.date : new Date().toISOString().slice(0, 10));
    if (p.get("shift")) $("r-shift").value = p.get("shift");
    else if (cur) $("r-shift").value = cur.name;
    $("report-form").addEventListener("submit", (e) => { e.preventDefault(); preview(); });
    $("r-date").addEventListener("change", updateLinks);
    $("r-shift").addEventListener("change", updateLinks);
    $("dl-pdf").addEventListener("click", () => TC.toast("Đang tạo file PDF, có thể mất vài giây…"));
    $("history").addEventListener("click", (e) => {
      const a = e.target.closest("a[data-date]");
      if (!a) return;
      e.preventDefault();
      $("r-date").value = a.dataset.date; $("r-shift").value = a.dataset.shift; preview();
    });
    const regen = $("regen");
    if (regen) regen.addEventListener("click", async () => {
      err.textContent = "";
      TC.busy(regen, true, "Đang sinh PDF…");
      try {
        await TC.api("/api/reports/shift/generate", { method: "POST", body: { date: $("r-date").value, shift: $("r-shift").value }, timeout: 120000 });
        TC.toast("Đã chốt lại số liệu và sinh PDF.");
        loadHistory(); preview();
      } catch (e) { err.textContent = e.message; }
      finally { TC.busy(regen, false); }
    });
    updateLinks();
    preview();
    loadHistory();
  }
  init();
})();
