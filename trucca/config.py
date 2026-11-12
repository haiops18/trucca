"""Cấu hình ứng dụng.

Thứ tự ưu tiên: biến môi trường > config.yml > giá trị mặc định.

- Mọi khoá trong config.yml đều ghi đè được bằng biến môi trường dạng
  ``TRUCCA__<SECTION>__<KEY>`` (ví dụ ``TRUCCA__ALERTMANAGER__URL``). Giá trị
  được parse bằng YAML nên ``true``/``30``/``[a, b]`` đều hợp lệ.
- Một số secret có tên ngắn gọn (xem ``_SHORT_ENV``) để dùng trong ``.env``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "/app/config/config.yml"

ENV_PREFIX = "TRUCCA__"
_SHORT_ENV: dict[str, tuple[str, ...]] = {
    "ALERTMANAGER_URL": ("alertmanager", "url"),
    "ALERTMANAGER_USERNAME": ("alertmanager", "auth", "username"),
    "ALERTMANAGER_PASSWORD": ("alertmanager", "auth", "password"),
    "ALERTMANAGER_TOKEN": ("alertmanager", "auth", "token"),
    "DATABASE_URL": ("database", "url"),
}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlertmanagerAuth(_Base):
    type: Literal["none", "basic", "bearer"] = "none"
    username: str = ""
    password: SecretStr = SecretStr("")
    token: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _check(self) -> AlertmanagerAuth:
        if self.type == "basic" and not self.username:
            raise ValueError("auth.type=basic cần username (đặt ALERTMANAGER_USERNAME trong .env)")
        if self.type == "bearer" and not self.token.get_secret_value():
            raise ValueError("auth.type=bearer cần token (đặt ALERTMANAGER_TOKEN trong .env)")
        return self


class MassClearGuard(_Base):
    """Khi nhiều cảnh báo cùng biến mất một lúc (thường do Alertmanager restart),
    chờ lâu hơn trước khi đánh dấu clear."""

    ratio: float = Field(0.5, gt=0, le=1)
    min_alerts: int = Field(10, ge=1)
    polls: int = Field(10, ge=1, le=1000)


class AlertmanagerConfig(_Base):
    url: str = "http://alertmanager:9093"
    api_version: Literal["v2"] = "v2"
    auth: AlertmanagerAuth = AlertmanagerAuth()
    verify_tls: bool = True
    ca_file: str | None = None
    timeout_seconds: float = Field(10, gt=0, le=300)
    poll_interval_seconds: int = Field(30, ge=5, le=3600)
    resolve_after_missing_polls: int = Field(2, ge=1, le=100)
    mass_clear_guard: MassClearGuard = MassClearGuard()

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not re.match(r"^https?://", v):
            raise ValueError("phải bắt đầu bằng http:// hoặc https://")
        return v.rstrip("/")


class LabelsConfig(_Base):
    owner: str = "owner"
    application: str = "application"
    alertname: str = "alertname"
    severity: str = "severity"
    instance: str = "instance"
    summary_annotation: str = "summary"


class FiltersConfig(_Base):
    match: dict[str, str | list[str]] = {}
    match_regex: dict[str, str] = {}
    exclude_alertnames: list[str] = []
    include_silenced: bool = False
    include_inhibited: bool = False

    @field_validator("match_regex")
    @classmethod
    def _regex(cls, v: dict[str, str]) -> dict[str, str]:
        for k, pattern in v.items():
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"regex không hợp lệ cho label '{k}': {e}") from e
        return v


class ShiftConfig(_Base):
    name: str = Field(min_length=1, max_length=32)
    start: time
    end: time

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9_\-]+$", v):
            raise ValueError("tên ca chỉ gồm chữ, số, '_' hoặc '-'")
        return v

    @model_validator(mode="after")
    def _check(self) -> ShiftConfig:
        if self.start == self.end:
            raise ValueError(f"ca {self.name}: giờ bắt đầu và kết thúc không được trùng nhau")
        return self

    @property
    def overnight(self) -> bool:
        return self.end <= self.start

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M} - {self.end:%H:%M}"


class DisplayConfig(_Base):
    refresh_seconds: int = Field(30, ge=5, le=3600)
    group_same_alert: bool = False
    missing_label_text: str = "(chưa gán)"
    page_size: int = Field(50, ge=10, le=1000)


class ReportConfig(_Base):
    handover_threshold_hours: float = Field(10, ge=0)
    top_n: int = Field(5, ge=1, le=20)
    handover_max_items: int = Field(8, ge=1, le=200)
    trend_days: int = Field(7, ge=1, le=31)
    auto_generate_at_shift_end: bool = True
    output_dir: str = "/data/reports"
    title: str = "Báo cáo cuối ca"
    pdf_timeout_seconds: int = Field(60, ge=5, le=600)


class ServerConfig(_Base):
    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)
    base_path: str = "/"

    @field_validator("base_path")
    @classmethod
    def _bp(cls, v: str) -> str:
        v = "/" + v.strip("/")
        return v


class AuthConfig(_Base):
    session_ttl_hours: float = Field(12, gt=0, le=24 * 30)
    remember_days: int = Field(30, ge=1, le=365)
    cookie_secure: bool = False
    max_failed_attempts: int = Field(5, ge=1, le=100)
    lockout_minutes: int = Field(15, ge=1, le=24 * 60)
    min_password_length: int = Field(8, ge=6, le=128)


class DatabaseConfig(_Base):
    url: SecretStr = SecretStr("postgresql+psycopg://trucca:trucca@postgres:5432/trucca")
    pool_size: int = Field(5, ge=1, le=50)

    @field_validator("url")
    @classmethod
    def _url(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw.startswith("postgresql://"):
            raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("chỉ hỗ trợ PostgreSQL (postgresql://...)")
        return SecretStr(raw)


SEVERITY_GROUPS = ("critical", "warning")


class AppConfig(_Base):
    alertmanager: AlertmanagerConfig = AlertmanagerConfig()
    labels: LabelsConfig = LabelsConfig()
    # Nhóm mức độ -> các giá trị label severity (so khớp không phân biệt hoa thường, bỏ khoảng trắng).
    # Giá trị không thuộc nhóm nào => "other" (hiển thị "Khác").
    severity_mapping: dict[str, list[str]] = Field(
        default_factory=lambda: {"critical": ["critical"], "warning": ["warning"]})
    filters: FiltersConfig = FiltersConfig()
    timezone: str = "Asia/Ho_Chi_Minh"
    shifts: list[ShiftConfig] = Field(
        default_factory=lambda: [
            ShiftConfig(name="CA_1", start=time(8), end=time(18)),
            ShiftConfig(name="CA_2", start=time(18), end=time(8)),
        ],
        min_length=1,
    )
    display: DisplayConfig = DisplayConfig()
    report: ReportConfig = ReportConfig()
    retention_days: int = Field(90, ge=1, le=3650)
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    database: DatabaseConfig = DatabaseConfig()
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"timezone không hợp lệ: {v}") from e
        return v

    @field_validator("severity_mapping")
    @classmethod
    def _severity_mapping(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(v) - set(SEVERITY_GROUPS))
        if unknown:
            raise ValueError(f"nhóm mức độ không hợp lệ: {', '.join(unknown)} (chỉ dùng: critical, warning)")
        out: dict[str, list[str]] = {}
        owner: dict[str, str] = {}
        for group in SEVERITY_GROUPS:
            values = []
            for raw in v.get(group, []):
                key = str(raw).strip().casefold()
                if not key:
                    raise ValueError(f"severity_mapping.{group} có giá trị rỗng")
                if key in owner and owner[key] != group:
                    raise ValueError(f"giá trị '{raw}' nằm ở cả nhóm {owner[key]} và {group}")
                owner[key] = group
                if key not in values:
                    values.append(key)
            out[group] = values
        return out

    @field_validator("shifts")
    @classmethod
    def _shifts(cls, v: list[ShiftConfig]) -> list[ShiftConfig]:
        names = [s.name for s in v]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"tên ca bị trùng: {', '.join(sorted(dup))}")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def shift(self, name: str) -> ShiftConfig | None:
        return next((s for s in self.shifts if s.name == name), None)

    def redacted(self) -> dict[str, Any]:
        """Dạng dict an toàn để hiển thị (secret bị che)."""
        return self.model_dump(mode="json")  # SecretStr serialize thành '**********'


class ConfigError(Exception):
    """Config không hợp lệ; message đã được định dạng để đọc được."""


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        node = d.get(key)
        if not isinstance(node, dict):
            node = {}
            d[key] = node
        d = node
    d[path[-1]] = value


def _env_overrides(env: dict[str, str]) -> list[tuple[tuple[str, ...], Any]]:
    out: list[tuple[tuple[str, ...], Any]] = []
    for name, path in _SHORT_ENV.items():
        if env.get(name):
            out.append((path, env[name]))
    for name, raw in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        path = tuple(p.lower() for p in name[len(ENV_PREFIX):].split("__") if p)
        if not path:
            continue
        try:
            value = yaml.safe_load(raw) if raw != "" else ""
        except yaml.YAMLError:
            value = raw
        out.append((path, value))
    return out


def format_validation_error(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "(gốc)"
        msg = e["msg"].removeprefix("Value error, ")
        lines.append(f"  - {loc}: {msg}")
    return "Cấu hình không hợp lệ:\n" + "\n".join(lines)


def load_config(path: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None) -> AppConfig:
    env = dict(os.environ) if env is None else env
    cfg_path = Path(path or env.get("TRUCCA_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigError(f"Không đọc được {cfg_path}: lỗi cú pháp YAML: {e}") from e
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{cfg_path}: nội dung gốc phải là mapping (key: value)")
        data = loaded
    else:
        log.warning("config_file_missing", extra={"path": str(cfg_path)})
    for p, value in _env_overrides(env):
        _set_path(data, p, value)
    try:
        return AppConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(format_validation_error(e)) from e


class ConfigManager:
    """Giữ config hiện hành, hỗ trợ reload khi file thay đổi."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.environ.get("TRUCCA_CONFIG", DEFAULT_CONFIG_PATH))
        self._lock = threading.Lock()
        self._config = load_config(self.path)
        self._mtime = self._stat()
        self._listeners: list[Any] = []
        self.last_reload_error: str | None = None

    def _stat(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    @property
    def config(self) -> AppConfig:
        return self._config

    def on_change(self, fn: Any) -> None:
        self._listeners.append(fn)

    def reload(self) -> AppConfig:
        """Đọc lại config. Nếu lỗi: giữ config cũ và ném ConfigError."""
        with self._lock:
            self._mtime = self._stat()
            try:
                new = load_config(self.path)
            except ConfigError as e:
                self.last_reload_error = str(e)
                log.error("config_reload_failed", extra={"error": str(e)})
                raise
            old = self._config
            self._config = new
            self.last_reload_error = None
        log.info("config_reloaded", extra={"path": str(self.path)})
        for fn in self._listeners:
            try:
                fn(old, new)
            except Exception:
                log.exception("config_listener_failed")
        return new

    def check_for_changes(self) -> None:
        if self._stat() != self._mtime:
            try:
                self.reload()
            except ConfigError:
                pass  # đã log; giữ config cũ
