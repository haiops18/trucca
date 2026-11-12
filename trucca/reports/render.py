"""Render báo cáo ca: Jinja2 HTML (+ SVG inline) -> PDF bằng Chromium headless."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def trend_svg(trend: list[dict[str, Any]], width: int = 760, height: int = 136) -> Markup:
    """Biểu đồ cột xu thế phát sinh, nhãn số trên đầu cột, nhãn trục dd/MM."""
    n = max(len(trend), 1)
    pad_l, pad_r, pad_t, pad_b = 16, 16, 22, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    max_v = max((t["count"] for t in trend), default=0) or 1
    slot = plot_w / n
    bar_w = min(34.0, slot * 0.45)
    base_y = pad_t + plot_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="Xu thế phát sinh theo ngày">',
        f'<line x1="{pad_l}" y1="{base_y + 0.5}" x2="{width - pad_r}" y2="{base_y + 0.5}" '
        f'stroke="#e2e8f0" stroke-width="1"/>',
    ]
    for i, t in enumerate(trend):
        v = int(t["count"])
        h = (v / max_v) * plot_h if v else 0
        cx = pad_l + slot * i + slot / 2
        x = cx - bar_w / 2
        y = base_y - h
        if h > 0:
            r = min(4.0, h / 2)
            # Cột bo góc trên
            parts.append(
                f'<path d="M{x:.1f},{base_y:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                f'H{x + bar_w - r:.1f} Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + r:.1f} V{base_y:.1f} Z" '
                f'fill="#b42318"/>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" '
            f'fill="#1f2937">{v}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{base_y + 16:.1f}" text-anchor="middle" font-size="10" '
            f'fill="#64748b">{escape(t["label"])}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["trend_svg"] = trend_svg
    return env


class ReportRenderer:
    def __init__(self, pdf_timeout_seconds: int = 60):
        self.env = make_env()
        self.pdf_timeout_ms = pdf_timeout_seconds * 1000
        self._lock = threading.Lock()  # 1 Chromium tại một thời điểm, tránh tốn RAM

    def render_html(self, payload: dict[str, Any], font_base: str = "") -> str:
        """font_base: URL thư mục font cho bản xem trên web. PDF dùng font đã cài trong hệ thống."""
        return self.env.get_template("reports/shift_report.html").render(r=payload, font_base=font_base)

    def render_pdf(self, payload: dict[str, Any]) -> bytes:
        from playwright.sync_api import sync_playwright

        html = self.render_html(payload)
        with self._lock, sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu"])
            try:
                page = browser.new_page()
                # Chặn mọi request ra ngoài: báo cáo tự chứa toàn bộ nội dung
                page.route("**/*", lambda route: route.abort()
                           if not route.request.url.startswith(("data:", "about:")) else route.continue_())
                page.set_default_timeout(self.pdf_timeout_ms)
                page.set_content(html, wait_until="load")
                page.evaluate("document.fonts.ready")
                return page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
