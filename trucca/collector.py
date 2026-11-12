"""Thu thập cảnh báo từ Alertmanager và đồng bộ vào DB.

Luồng mỗi chu kỳ (mặc định 30s):
  1. GET /api/v2/alerts (lấy cả silenced/inhibited, lọc tại chỗ theo config).
  2. Chỉ khi gọi thành công trọn vẹn mới chạy reconcile; lỗi => giữ nguyên trạng thái.
  3. ``plan_reconcile`` (hàm thuần) quyết định: phát sinh mới / cập nhật /
     tạm vắng / clear. ``apply_plan`` ghi vào DB trong 1 transaction.

Chống clear hàng loạt: cảnh báo phải vắng mặt ``resolve_after_missing_polls``
lần poll thành công liên tiếp mới bị đánh dấu clear (resolved_at = lúc vắng
mặt lần đầu).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import bindparam, select, update
from sqlalchemy.engine import Engine

from . import __version__
from .config import AppConfig, FiltersConfig, LabelsConfig
from .db import alerts, occurrences
from .timeutil import parse_am_time, utcnow

log = logging.getLogger(__name__)

STARTS_AT_TOLERANCE = timedelta(seconds=1)


# --------------------------------------------------------------------------- client


class FetchError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind  # timeout | auth | http | network | invalid
        self.status = status
        self.message = message


class AlertmanagerClient:
    def __init__(self, cfg: AppConfig):
        am = cfg.alertmanager
        self.base_url = am.url
        auth: httpx.Auth | None = None
        headers = {"Accept": "application/json", "User-Agent": f"TrucCa/{__version__}"}
        if am.auth.type == "basic":
            auth = httpx.BasicAuth(am.auth.username, am.auth.password.get_secret_value())
        elif am.auth.type == "bearer":
            headers["Authorization"] = f"Bearer {am.auth.token.get_secret_value()}"
        verify: bool | str = am.ca_file if (am.verify_tls and am.ca_file) else am.verify_tls
        self._client = httpx.Client(
            base_url=self.base_url, auth=auth, headers=headers, verify=verify, timeout=am.timeout_seconds
        )

    def close(self) -> None:
        self._client.close()

    def fetch_alerts(self) -> list[dict[str, Any]]:
        params = {"active": "true", "silenced": "true", "inhibited": "true", "unprocessed": "true"}
        try:
            resp = self._client.get("/api/v2/alerts", params=params)
        except httpx.TimeoutException as e:
            raise FetchError("timeout", "Hết thời gian chờ phản hồi từ Alertmanager") from e
        except httpx.HTTPError as e:
            raise FetchError("network", f"Không kết nối được tới Alertmanager ({type(e).__name__})") from e
        if resp.status_code in (401, 403):
            raise FetchError("auth", f"Alertmanager từ chối xác thực (HTTP {resp.status_code})", resp.status_code)
        if resp.status_code >= 400:
            raise FetchError("http", f"Alertmanager trả lỗi HTTP {resp.status_code}", resp.status_code)
        try:
            data = resp.json()
        except ValueError as e:
            raise FetchError("invalid", "Phản hồi từ Alertmanager không phải JSON hợp lệ") from e
        if not isinstance(data, list):
            raise FetchError("invalid", "Phản hồi từ Alertmanager không đúng định dạng (cần danh sách)")
        return data


# --------------------------------------------------------------------------- normalize / filter


@dataclass
class FetchedAlert:
    fingerprint: str
    alertname: str
    summary: str
    severity: str | None
    owner: str | None
    application: str | None
    instance: str | None
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None
    am_state: str | None


def _blank_to_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize_alert(raw: dict[str, Any], labels_cfg: LabelsConfig) -> FetchedAlert | None:
    fp = raw.get("fingerprint")
    labels = raw.get("labels") or {}
    annotations = raw.get("annotations") or {}
    try:
        starts_at = parse_am_time(raw.get("startsAt"))
        ends_at = parse_am_time(raw.get("endsAt"))
    except (ValueError, TypeError, AttributeError):
        return None
    if not fp or not isinstance(labels, dict) or starts_at is None:
        return None
    labels = {str(k): str(v) for k, v in labels.items()}
    annotations = {str(k): str(v) for k, v in annotations.items()} if isinstance(annotations, dict) else {}
    alertname = labels.get(labels_cfg.alertname) or "(không tên)"
    status = raw.get("status") or {}
    return FetchedAlert(
        fingerprint=str(fp)[:64],
        alertname=alertname,
        summary=annotations.get(labels_cfg.summary_annotation, "") or "",
        severity=_blank_to_none(labels.get(labels_cfg.severity)),
        owner=_blank_to_none(labels.get(labels_cfg.owner)),
        application=_blank_to_none(labels.get(labels_cfg.application)),
        instance=_blank_to_none(labels.get(labels_cfg.instance)),
        labels=labels,
        annotations=annotations,
        starts_at=starts_at,
        ends_at=ends_at,
        am_state=status.get("state") if isinstance(status, dict) else None,
    )


def passes_filters(a: FetchedAlert, f: FiltersConfig) -> bool:
    if a.alertname in f.exclude_alertnames:
        return False
    for key, expected in f.match.items():
        val = a.labels.get(key)
        allowed = expected if isinstance(expected, list) else [expected]
        if val not in [str(x) for x in allowed]:
            return False
    return all(re.fullmatch(pattern, a.labels.get(key, "")) for key, pattern in f.match_regex.items())


def passes_suppression(raw: dict[str, Any], f: FiltersConfig) -> bool:
    status = raw.get("status") or {}
    if not isinstance(status, dict):
        return True
    if status.get("silencedBy") and not f.include_silenced:
        return False
    return not (status.get("inhibitedBy") and not f.include_inhibited)


# --------------------------------------------------------------------------- reconcile (pure)


@dataclass
class KnownAlert:
    """Trạng thái đã lưu của một fingerprint."""

    fingerprint: str
    status: str
    starts_at: datetime
    occurrence_id: int | None
    missing_polls: int = 0
    first_missing_at: datetime | None = None
    summary: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    am_state: str | None = None


@dataclass
class Plan:
    new: list[FetchedAlert] = field(default_factory=list)  # phát sinh mới (đang firing)
    new_resolved: list[tuple[FetchedAlert, datetime]] = field(default_factory=list)  # phát sinh + đã kết thúc
    refire: list[tuple[FetchedAlert, KnownAlert]] = field(default_factory=list)  # đóng occurrence cũ, mở mới
    # firing lại rồi kết thúc giữa 2 lần poll: đóng occurrence cũ + thêm occurrence mới đã kết thúc
    refire_ended: list[tuple[FetchedAlert, KnownAlert]] = field(default_factory=list)
    reopen: list[tuple[FetchedAlert, KnownAlert]] = field(default_factory=list)  # clear nhầm, mở lại
    changed: list[FetchedAlert] = field(default_factory=list)  # nội dung đổi
    seen: list[str] = field(default_factory=list)  # fingerprint đang active còn thấy
    missing: list[tuple[str, int, datetime]] = field(default_factory=list)  # (fp, missing_polls, first_missing_at)
    resolve: list[tuple[str, datetime, str]] = field(default_factory=list)  # (fp, resolved_at, source)
    mass_disappearance: bool = False

    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new) + len(self.new_resolved) + len(self.refire) + len(self.refire_ended),
            "reopened": len(self.reopen),
            "changed": len(self.changed),
            "missing": len(self.missing),
            "resolved": len(self.resolve) + len(self.refire) + len(self.refire_ended),
        }


def _content_changed(a: FetchedAlert, k: KnownAlert) -> bool:
    return a.summary != k.summary or a.labels != k.labels or a.annotations != k.annotations or a.am_state != k.am_state


def plan_reconcile(
    fetched: list[FetchedAlert],
    filtered_out: set[str],
    known: dict[str, KnownAlert],
    now: datetime,
    resolve_after_missing_polls: int,
    mass_guard: tuple[float, int, int] | None = None,
    present_invalid: set[str] | None = None,
) -> Plan:
    """mass_guard = (ratio, min_alerts, polls): nếu số cảnh báo vắng mặt >= min_alerts và
    >= ratio * số đang active thì cần ``polls`` lần vắng liên tiếp mới clear.
    present_invalid: fingerprint có trong phản hồi nhưng dữ liệu lỗi => coi như vẫn còn (không đếm vắng).
    filtered_out: không còn khớp filter => cũng phải qua đủ số lần poll mới đóng (nguồn 'filtered_out')."""
    plan = Plan()
    fetched_fps: set[str] = set()

    for a in fetched:
        fetched_fps.add(a.fingerprint)
        k = known.get(a.fingerprint)
        ended = a.ends_at is not None and a.ends_at <= now

        if ended:
            if k is not None and k.status == "active" and a.starts_at - k.starts_at > STARTS_AT_TOLERANCE:
                plan.refire_ended.append((a, k))
            elif k is not None and k.status == "active":
                plan.resolve.append((a.fingerprint, a.ends_at, "ends_at"))  # type: ignore[arg-type]
            elif k is None or abs(k.starts_at - a.starts_at) > STARTS_AT_TOLERANCE:
                plan.new_resolved.append((a, a.ends_at))  # type: ignore[arg-type]
            continue

        if k is None:
            plan.new.append(a)
        elif k.status == "resolved":
            if abs(k.starts_at - a.starts_at) <= STARTS_AT_TOLERANCE:
                plan.reopen.append((a, k))  # cùng startsAt => chưa từng thực sự clear
            else:
                plan.new.append(a)
        elif a.starts_at - k.starts_at > STARTS_AT_TOLERANCE:
            plan.refire.append((a, k))  # resolved rồi firing lại giữa 2 lần poll
        else:
            plan.seen.append(a.fingerprint)
            if _content_changed(a, k):
                plan.changed.append(a)

    present = fetched_fps | (present_invalid or set())
    active_fps = [fp for fp, k in known.items() if k.status == "active"]
    gone = [fp for fp in active_fps if fp not in present]
    threshold = resolve_after_missing_polls
    if mass_guard is not None and len(gone) >= mass_guard[1] and len(gone) >= mass_guard[0] * len(active_fps):
        threshold = max(threshold, mass_guard[2])
        plan.mass_disappearance = True

    for fp in gone:
        k = known[fp]
        polls = k.missing_polls + 1
        first_missing = k.first_missing_at or now
        if polls >= threshold:
            plan.resolve.append((fp, first_missing, "filtered_out" if fp in filtered_out else "api_missing"))
        else:
            plan.missing.append((fp, polls, first_missing))
    return plan


# --------------------------------------------------------------------------- apply (DB)


def load_known(engine: Engine) -> dict[str, KnownAlert]:
    cols = [
        alerts.c.fingerprint,
        alerts.c.status,
        alerts.c.starts_at,
        alerts.c.current_occurrence_id,
        alerts.c.missing_polls,
        alerts.c.first_missing_at,
        alerts.c.summary,
        alerts.c.labels,
        alerts.c.annotations,
        alerts.c.am_state,
    ]
    with engine.connect() as conn:
        rows = conn.execute(select(*cols)).all()
    return {
        r.fingerprint: KnownAlert(
            r.fingerprint, r.status, r.starts_at, r.current_occurrence_id, r.missing_polls,
            r.first_missing_at, r.summary, r.labels, r.annotations, r.am_state,
        )
        for r in rows
    }


def _alert_values(a: FetchedAlert) -> dict[str, Any]:
    return {
        "alertname": a.alertname,
        "summary": a.summary,
        "severity": a.severity,
        "owner": a.owner,
        "application": a.application,
        "instance": a.instance,
        "labels": a.labels,
    }


def apply_plan(engine: Engine, plan: Plan, now: datetime) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    with engine.begin() as conn:

        def open_occurrence(a: FetchedAlert, resolved_at: datetime | None = None, source: str | None = None) -> int:
            return conn.execute(
                occurrences.insert()
                .values(
                    fingerprint=a.fingerprint,
                    starts_at=a.starts_at,
                    first_seen_at=now,
                    resolved_at=resolved_at,
                    resolve_source=source,
                    **_alert_values(a),
                )
                .returning(occurrences.c.id)
            ).scalar_one()

        def upsert_alert(a: FetchedAlert, occ_id: int, status: str = "active",
                         resolved_at: datetime | None = None) -> None:
            values = {
                **_alert_values(a),
                "annotations": a.annotations,
                "starts_at": a.starts_at,
                "first_seen_at": now,
                "last_seen_at": now,
                "resolved_at": resolved_at,
                "status": status,
                "am_state": a.am_state,
                "current_occurrence_id": occ_id,
                "missing_polls": 0,
                "first_missing_at": None,
            }
            stmt = pg_insert(alerts).values(fingerprint=a.fingerprint, **values)
            stmt = stmt.on_conflict_do_update(index_elements=[alerts.c.fingerprint], set_=values)
            conn.execute(stmt)

        for a in plan.new:
            upsert_alert(a, open_occurrence(a))
        for a, ended in plan.new_resolved:
            upsert_alert(a, open_occurrence(a, ended, "ends_at"), status="resolved", resolved_at=ended)
        for a, k in plan.refire:
            if k.occurrence_id:
                conn.execute(
                    update(occurrences)
                    .where(occurrences.c.id == k.occurrence_id)
                    .values(resolved_at=a.starts_at, resolve_source="refire")
                )
            upsert_alert(a, open_occurrence(a))
        for a, k in plan.refire_ended:
            if k.occurrence_id:
                conn.execute(
                    update(occurrences)
                    .where(occurrences.c.id == k.occurrence_id)
                    .values(resolved_at=a.starts_at, resolve_source="refire")
                )
            upsert_alert(a, open_occurrence(a, a.ends_at, "ends_at"), status="resolved", resolved_at=a.ends_at)
        for a, k in plan.reopen:
            if k.occurrence_id:
                conn.execute(
                    update(occurrences)
                    .where(occurrences.c.id == k.occurrence_id)
                    .values(resolved_at=None, resolve_source=None, **_alert_values(a))
                )
            conn.execute(
                update(alerts)
                .where(alerts.c.fingerprint == a.fingerprint)
                .values(
                    status="active", resolved_at=None, last_seen_at=now, missing_polls=0, first_missing_at=None,
                    annotations=a.annotations, am_state=a.am_state, **_alert_values(a),
                )
            )
            log.warning("alert_reopened", extra={"fingerprint": a.fingerprint, "alertname": a.alertname})

        if plan.seen:
            conn.execute(
                update(alerts)
                .where(alerts.c.fingerprint.in_(plan.seen))
                .values(last_seen_at=now, missing_polls=0, first_missing_at=None)
            )
        if plan.changed:
            conn.execute(
                update(alerts).where(alerts.c.fingerprint == bindparam("fp")).values(
                    alertname=bindparam("alertname"), summary=bindparam("summary"), severity=bindparam("severity"),
                    owner=bindparam("owner"), application=bindparam("application"), instance=bindparam("instance"),
                    labels=bindparam("labels"), annotations=bindparam("annotations"), am_state=bindparam("am_state"),
                ),
                [{"fp": a.fingerprint, **_alert_values(a), "annotations": a.annotations, "am_state": a.am_state}
                 for a in plan.changed],
            )
            # Đồng bộ thông tin hiển thị (owner/application/summary) cho occurrence hiện tại
            conn.execute(
                update(occurrences)
                .where(occurrences.c.id == select(alerts.c.current_occurrence_id)
                       .where(alerts.c.fingerprint == bindparam("fp")).scalar_subquery())
                .values(
                    alertname=bindparam("alertname"), summary=bindparam("summary"), severity=bindparam("severity"),
                    owner=bindparam("owner"), application=bindparam("application"), instance=bindparam("instance"),
                    labels=bindparam("labels"),
                ),
                [{"fp": a.fingerprint, **_alert_values(a)} for a in plan.changed],
            )
        if plan.missing:
            conn.execute(
                update(alerts).where(alerts.c.fingerprint == bindparam("fp")).values(
                    missing_polls=bindparam("polls"), first_missing_at=bindparam("first_missing")
                ),
                [{"fp": fp, "polls": n, "first_missing": t} for fp, n, t in plan.missing],
            )
        if plan.resolve:
            params = [{"fp": fp, "rat": t, "src": src} for fp, t, src in plan.resolve]
            conn.execute(
                update(occurrences)
                .where(occurrences.c.id == select(alerts.c.current_occurrence_id)
                       .where(alerts.c.fingerprint == bindparam("fp")).scalar_subquery())
                .values(resolved_at=bindparam("rat"), resolve_source=bindparam("src")),
                params,
            )
            conn.execute(
                update(alerts).where(alerts.c.fingerprint == bindparam("fp")).values(
                    status="resolved", resolved_at=bindparam("rat"), missing_polls=0, first_missing_at=None
                ),
                params,
            )


# --------------------------------------------------------------------------- collector


@dataclass
class CollectorStatus:
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: dict[str, Any] | None = None
    consecutive_failures: int = 0
    last_fetch_count: int = 0
    last_duration_ms: int = 0
    last_plan: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "connected": self.last_error is None and self.last_success_at is not None,
            "last_fetch_count": self.last_fetch_count,
            "last_duration_ms": self.last_duration_ms,
            "last_plan": self.last_plan,
        }


class Collector:
    def __init__(self, engine: Engine, cfg: AppConfig):
        self.engine = engine
        self.cfg = cfg
        self.client = AlertmanagerClient(cfg)
        self.status = CollectorStatus()
        self._lock = threading.Lock()

    def update_config(self, cfg: AppConfig) -> None:
        with self._lock:
            old = self.client
            self.cfg = cfg
            self.client = AlertmanagerClient(cfg)
            old.close()

    def poll_once(self) -> None:
        with self._lock:
            self._poll()

    def _poll(self) -> None:
        t0 = time.monotonic()
        now = utcnow()
        self.status.last_attempt_at = now
        try:
            raw = self.client.fetch_alerts()
        except FetchError as e:
            self.status.consecutive_failures += 1
            self.status.last_error = {"kind": e.kind, "message": e.message, "status": e.status, "at": now.isoformat()}
            log.error(
                "alertmanager_fetch_failed",
                extra={"kind": e.kind, "status": e.status, "error": e.message,
                       "consecutive_failures": self.status.consecutive_failures},
            )
            return

        try:
            self._process(raw, now, t0)
        except Exception as e:
            # Lỗi xử lý (dữ liệu lạ, DB...) phải hiện lên giao diện, không âm thầm "OK"
            self.status.consecutive_failures += 1
            self.status.last_error = {"kind": "internal", "status": None, "at": now.isoformat(),
                                      "message": f"Lỗi xử lý dữ liệu cảnh báo ({type(e).__name__}), xem log TrucCa"}
            log.exception("poll_processing_failed")

    def _process(self, raw: list[Any], now: datetime, t0: float) -> None:
        fetched: list[FetchedAlert] = []
        filtered_out: set[str] = set()
        present_invalid: set[str] = set()
        invalid = 0
        for item in raw:
            if not isinstance(item, dict):
                invalid += 1
                continue
            a = normalize_alert(item, self.cfg.labels)
            if a is None:
                invalid += 1
                if item.get("fingerprint"):
                    present_invalid.add(str(item["fingerprint"])[:64])
                continue
            if passes_suppression(item, self.cfg.filters) and passes_filters(a, self.cfg.filters):
                fetched.append(a)
            else:
                filtered_out.add(a.fingerprint)
        if invalid:
            log.warning("alertmanager_invalid_items", extra={"count": invalid})

        known = load_known(self.engine)
        am = self.cfg.alertmanager
        guard = am.mass_clear_guard
        plan = plan_reconcile(
            fetched, filtered_out, known, now, am.resolve_after_missing_polls,
            (guard.ratio, guard.min_alerts, guard.polls), present_invalid,
        )
        if plan.mass_disappearance:
            log.warning("mass_disappearance_suspected",
                        extra={"missing": len(plan.missing) + len(plan.resolve), "wait_polls": guard.polls})
        apply_plan(self.engine, plan, now)

        self.status.last_success_at = now
        self.status.last_error = None
        self.status.consecutive_failures = 0
        self.status.last_fetch_count = len(fetched)
        self.status.last_duration_ms = int((time.monotonic() - t0) * 1000)
        self.status.last_plan = plan.summary()
        log.info(
            "poll_ok",
            extra={"fetched": len(fetched), "filtered_out": len(filtered_out),
                   "duration_ms": self.status.last_duration_ms, **self.status.last_plan},
        )
