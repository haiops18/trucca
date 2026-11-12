// Trang cảnh báo đang diễn ra: lọc, sắp xếp, phân trang, tự làm mới, xuất Excel.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const FILTER_KEYS = ["q", "application", "owner", "alertname", "severity"];
  const LABELS = { q: "Tìm", application: "Hệ thống", owner: "Người xử lý", alertname: "Tên cảnh báo", severity: "Mức độ" };
  const MISSING = "__missing__";
  const MISSING_TEXT = "(chưa gán)";

  const state = {
    q: "", application: "", owner: "", alertname: "", severity: "",
    min_hours: null, max_hours: null, group: false,
    sort: "duration", order: "desc", page: 1, page_size: 50,
  };
  let refreshSeconds = 30;
  let timer = null;
  let inflight = null;
  let buckets = [];
  let firstLoad = true;

  // ---------- URL <-> state (giữ bộ lọc khi tải lại / chia sẻ link) ----------
  function readUrl() {
    const p = new URLSearchParams(location.search);
    FILTER_KEYS.forEach((k) => { if (p.has(k)) state[k] = p.get(k); });
    ["min_hours", "max_hours"].forEach((k) => { if (p.has(k) && p.get(k) !== "") state[k] = Number(p.get(k)); });
    if (p.has("group")) state.group = p.get("group") === "1";
    if (p.has("sort")) state.sort = p.get("sort");
    if (p.has("order")) state.order = p.get("order") === "asc" ? "asc" : "desc";
    if (p.has("page")) state.page = Math.max(1, parseInt(p.get("page"), 10) || 1);
    if (p.has("page_size")) state.page_size = parseInt(p.get("page_size"), 10) || 50;
  }

  function queryString(forExport) {
    const p = new URLSearchParams();
    FILTER_KEYS.forEach((k) => { if (state[k]) p.set(k, state[k]); });
    if (state.min_hours != null) p.set("min_hours", state.min_hours);
    if (state.max_hours != null) p.set("max_hours", state.max_hours);
    if (state.group) p.set("group", forExport ? "true" : "1");
    p.set("sort", state.sort); p.set("order", state.order);
    if (!forExport) { p.set("page", state.page); p.set("page_size", state.page_size); }
    return p.toString();
  }

  function writeUrl() {
    history.replaceState(null, "", location.pathname + "?" + queryString(false));
    $("export-btn").href = TC.base + "/api/alerts/export.xlsx?" + queryString(true);
  }

  // ---------- Hiển thị ----------
  function levelFor(hours) {
    for (const b of buckets) {
      if (hours >= b.min_hours && (b.max_hours == null || hours < b.max_hours)) return b.level;
    }
    return 0;
  }

  function cell(value, cls, label) {
    const missing = value === MISSING_TEXT;
    return '<td class="' + (cls || "") + (missing ? " missing" : "") + '"' + (label ? ' data-label="' + label + '"' : "") + ">" + TC.esc(value) + "</td>";
  }

  function renderRows(items) {
    const tbody = $("rows");
    if (!items.length) { tbody.innerHTML = ""; return; }
    tbody.innerHTML = items.map((r) => {
      const lvl = levelFor(r.duration_hours);
      const multi = r.count > 1 ? '<span class="multi" title="' + r.count + ' cảnh báo được gộp">×' + r.count + "</span>" : "";
      const silenced = r.silenced ? '<span class="tag" title="Đang bị silence/inhibit trong Alertmanager">silenced</span>' : "";
      const sev = r.severity ? '<div class="sev">' + TC.esc(r.severity) + "</div>" : "";
      return '<tr class="lvl-' + lvl + '">' +
        "<td>" + r.stt + "</td>" +
        '<td class="name">' + TC.esc(r.alertname) + multi + silenced + sev + "</td>" +
        '<td class="sum">' + TC.esc(r.summary) + "</td>" +
        '<td class="dur" data-label="Thời gian tồn" title="Bắt đầu ' + TC.esc(TC.fmtDateTime(r.starts_at)) + '">' + TC.esc(r.duration) + "</td>" +
        cell(r.owner, "", "Người xử lý") + cell(r.application, "", "Hệ thống") + "</tr>";
    }).join("");
  }

  function renderSkeleton() {
    const row = "<tr class=\"skeleton\">" + "<td><div></div></td>".repeat(6) + "</tr>";
    $("rows").innerHTML = row.repeat(8);
  }

  function renderEmpty(data) {
    const box = $("empty");
    const filtered = FILTER_KEYS.some((k) => state[k]) || state.min_hours != null || state.max_hours != null;
    if (data.total > 0) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = filtered
      ? "<h3>Không có cảnh báo khớp bộ lọc</h3><p>Thử bỏ bớt điều kiện lọc hoặc từ khoá tìm kiếm.</p><button type=\"button\" class=\"btn\" id=\"empty-clear\">Xoá bộ lọc</button>"
      : "<h3>Không có cảnh báo đang diễn ra</h3><p>Danh sách sẽ tự cập nhật khi Alertmanager có cảnh báo mới.</p>";
    const btn = $("empty-clear");
    if (btn) btn.addEventListener("click", clearFilters);
  }

  function renderAgeBar(data) {
    buckets = data.age_buckets || [];
    const total = buckets.reduce((s, b) => s + b.count, 0);
    const bar = $("agebar");
    const legend = $("agelegend");
    const active = state.min_hours != null || state.max_hours != null;
    bar.classList.toggle("filtering", active);
    legend.style.setProperty("--n", buckets.length);
    bar.innerHTML = buckets.map((b, i) => {
      const pressed = active && state.min_hours === b.min_hours && (state.max_hours ?? null) === (b.max_hours ?? null);
      const grow = total ? Math.max(b.count / total, 0.03) : 1;
      const label = b.label + ": " + b.count + " cảnh báo" + (b.over_handover ? " (cần bàn giao)" : "");
      return '<button type="button" class="lvl-' + b.level + (b.count ? "" : " zero") + '" style="flex:' + grow.toFixed(4) +
        '" data-i="' + i + '" aria-pressed="' + pressed + '" aria-label="' + TC.esc(label) + '" title="' + TC.esc(label) + '"></button>';
    }).join("");
    legend.innerHTML = buckets.map((b, i) =>
      '<button type="button" tabindex="-1" data-i="' + i + '" class="lvl-' + b.level + (b.over_handover ? " handover" : "") + '"><b>' +
      TC.fmtNum(b.count) + "</b>" + TC.esc(b.label) + "</button>").join("");
    $("age-hint").textContent = active ? "Đang lọc theo thời gian tồn – bấm lại để bỏ" :
      "Từ " + data.handover_threshold_hours + "h trở lên cần bàn giao";
  }

  function renderConn(col) {
    const pill = $("conn-pill");
    const banner = $("conn-banner");
    if (col && col.last_error) {
      pill.className = "conn down"; pill.textContent = "Mất kết nối Alertmanager";
      banner.hidden = false;
      const last = col.last_success_at ? " Lần lấy dữ liệu thành công gần nhất: " + TC.fmtDateTime(col.last_success_at) + "." : "";
      $("conn-banner-text").textContent = " " + col.last_error.message + "." + last +
        " Dữ liệu đang hiển thị được giữ nguyên, không có cảnh báo nào bị đánh dấu clear.";
    } else if (col && col.last_success_at) {
      pill.className = "conn ok"; pill.textContent = "Alertmanager OK";
      banner.hidden = true;
    } else {
      pill.className = "conn"; pill.textContent = "Đang chờ lần lấy dữ liệu đầu tiên";
      banner.hidden = true;
    }
  }

  function renderChips() {
    const chips = [];
    FILTER_KEYS.forEach((k) => {
      if (!state[k]) return;
      const v = state[k] === MISSING ? MISSING_TEXT : state[k];
      chips.push('<span class="chip">' + LABELS[k] + ": " + TC.esc(v) + '<button type="button" data-k="' + k + '" aria-label="Bỏ lọc ' + LABELS[k] + '">×</button></span>');
    });
    if (state.min_hours != null || state.max_hours != null) {
      const b = buckets.find((x) => x.min_hours === state.min_hours && (x.max_hours ?? null) === (state.max_hours ?? null));
      chips.push('<span class="chip">Thời gian tồn: ' + TC.esc(b ? b.label : "tuỳ chọn") + '<button type="button" data-k="age" aria-label="Bỏ lọc thời gian tồn">×</button></span>');
    }
    $("chips").innerHTML = chips.join("");
  }

  function renderSortHeaders() {
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const on = th.dataset.sort === state.sort;
      th.setAttribute("aria-sort", on ? (state.order === "asc" ? "ascending" : "descending") : "none");
      th.querySelector(".arrow").textContent = on ? (state.order === "asc" ? "▲" : "▼") : "▲▼";
    });
  }

  function fillSelect(sel, values, current) {
    const opts = ['<option value="">Tất cả</option>'];
    const seen = new Set();
    values.forEach((v) => {
      seen.add(v);
      opts.push('<option value="' + TC.esc(v) + '">' + TC.esc(v === MISSING ? MISSING_TEXT : v) + "</option>");
    });
    if (current && !seen.has(current)) opts.push('<option value="' + TC.esc(current) + '">' + TC.esc(current) + " (hiện không có)</option>");
    sel.innerHTML = opts.join("");
    sel.value = current || "";
  }

  // ---------- Tải dữ liệu ----------
  async function loadFilters() {
    try {
      const f = await TC.api("/api/alerts/filters");
      ["application", "owner", "alertname", "severity"].forEach((k) => fillSelect($("f-" + k), f[k] || [], state[k]));
    } catch (e) { /* lỗi đã hiện ở banner chính khi load() */ }
  }

  async function load(opts) {
    if (inflight) inflight.abort();
    const ctrl = new AbortController();
    inflight = ctrl;
    writeUrl();
    renderSortHeaders();
    if (firstLoad) renderSkeleton();
    $("table-wrap").setAttribute("aria-busy", "true");
    try {
      const data = await TC.api("/api/alerts?" + queryString(false), { signal: ctrl.signal });
      if (ctrl !== inflight) return;
      state.page = data.page;
      $("load-banner").hidden = true;
      $("total-count").textContent = TC.fmtNum(data.total_alerts);
      $("updated-at").textContent = TC.fmtTime(data.generated_at);
      $("updated-at").setAttribute("datetime", data.generated_at);
      renderAgeBar(data);
      renderRows(data.items);
      renderEmpty(data);
      renderChips();
      renderConn(data.collector);
      const unit = state.group ? " nhóm" : " cảnh báo";
      $("result-summary").textContent = data.total
        ? "Hiển thị " + TC.fmtNum((data.page - 1) * data.page_size + 1) + "–" + TC.fmtNum(Math.min(data.page * data.page_size, data.total)) + " / " + TC.fmtNum(data.total) + unit
        : "0" + unit;
      $("page-info").textContent = "Trang " + data.page + "/" + data.pages;
      $("prev-page").disabled = data.page <= 1;
      $("next-page").disabled = data.page >= data.pages;
      firstLoad = false;
      if (opts && opts.withFilters) loadFilters();
    } catch (e) {
      if (e.name === "AbortError" || ctrl !== inflight) return;
      $("load-banner").hidden = false;
      $("load-banner-text").textContent = " " + e.message;
      if (firstLoad) { $("rows").innerHTML = ""; }
    } finally {
      if (ctrl === inflight) { inflight = null; $("table-wrap").removeAttribute("aria-busy"); }
    }
  }

  function schedule() {
    clearInterval(timer);
    if (refreshSeconds > 0) timer = setInterval(() => { if (!document.hidden) load({ withFilters: true }); }, refreshSeconds * 1000);
  }

  function clearFilters() {
    FILTER_KEYS.forEach((k) => { state[k] = ""; });
    state.min_hours = null; state.max_hours = null; state.page = 1;
    $("f-q").value = "";
    ["application", "owner", "alertname", "severity"].forEach((k) => { $("f-" + k).value = ""; });
    load();
  }

  // ---------- Sự kiện ----------
  function bind() {
    let debounce = null;
    $("f-q").addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => { state.q = $("f-q").value.trim(); state.page = 1; load(); }, 300);
    });
    $("filters").addEventListener("submit", (e) => e.preventDefault());
    ["application", "owner", "alertname", "severity"].forEach((k) => {
      $("f-" + k).addEventListener("change", (e) => { state[k] = e.target.value; state.page = 1; load(); });
    });
    $("clear-filters").addEventListener("click", clearFilters);
    $("f-group").addEventListener("change", (e) => { state.group = e.target.checked; state.page = 1; load(); });
    $("refresh-sel").addEventListener("change", (e) => { refreshSeconds = Number(e.target.value); schedule(); });
    $("refresh-now").addEventListener("click", () => load({ withFilters: true }));
    $("page-size").addEventListener("change", (e) => { state.page_size = Number(e.target.value); state.page = 1; load(); });
    $("prev-page").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); load(); });
    $("next-page").addEventListener("click", () => { state.page += 1; load(); });
    document.querySelectorAll("th[data-sort] button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.parentElement.dataset.sort;
        if (state.sort === key) state.order = state.order === "asc" ? "desc" : "asc";
        else { state.sort = key; state.order = key === "duration" ? "desc" : "asc"; }
        state.page = 1; load();
      });
    });
    function toggleBucket(i) {
      const b = buckets[i];
      if (!b) return;
      const same = state.min_hours === b.min_hours && (state.max_hours ?? null) === (b.max_hours ?? null);
      state.min_hours = same ? null : b.min_hours;
      state.max_hours = same ? null : b.max_hours;
      state.page = 1; load();
    }
    $("agebar").addEventListener("click", (e) => { const t = e.target.closest("button"); if (t) toggleBucket(Number(t.dataset.i)); });
    $("agelegend").addEventListener("click", (e) => { const t = e.target.closest("button"); if (t) toggleBucket(Number(t.dataset.i)); });
    $("chips").addEventListener("click", (e) => {
      const t = e.target.closest("button[data-k]");
      if (!t) return;
      const k = t.dataset.k;
      if (k === "age") { state.min_hours = null; state.max_hours = null; }
      else { state[k] = ""; if (k === "q") $("f-q").value = ""; else $("f-" + k).value = ""; }
      state.page = 1; load();
    });
    $("export-btn").addEventListener("click", () => TC.toast("Đang chuẩn bị file Excel…"));
    document.addEventListener("visibilitychange", () => { if (!document.hidden && refreshSeconds > 0) load(); });
  }

  async function init() {
    let status = null;
    try { status = await TC.api("/api/status"); } catch (e) { /* load() sẽ hiện lỗi */ }
    const hasUrl = location.search.length > 1;
    if (status) {
      refreshSeconds = status.refresh_seconds;
      state.page_size = status.page_size;
      state.group = status.group_default;
      if (status.current_shift) $("shift-now").textContent = "Đang trực " + status.current_shift.name;
    }
    if (hasUrl) readUrl();
    const sel = $("refresh-sel");
    if (![...sel.options].some((o) => Number(o.value) === refreshSeconds)) {
      sel.insertAdjacentHTML("afterbegin", '<option value="' + refreshSeconds + '">' + refreshSeconds + " giây</option>");
    }
    sel.value = String(refreshSeconds);
    const ps = $("page-size");
    if (![...ps.options].some((o) => Number(o.value) === state.page_size)) ps.insertAdjacentHTML("beforeend", "<option>" + state.page_size + "</option>");
    ps.value = String(state.page_size);
    $("f-q").value = state.q;
    $("f-group").checked = state.group;
    bind();
    await Promise.all([load(), loadFilters()]);
    schedule();
  }

  init();
})();
