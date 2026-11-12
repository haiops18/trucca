import pytest

from trucca.collector import normalize_alert
from trucca.config import AppConfig, ConfigError, LabelsConfig, load_config
from trucca.severity import LABELS, normalize_severity, parse_level


@pytest.mark.parametrize("raw", ["warning", "Warning", "WARNING", " warning ", "\tWaRnInG\n"])
def test_warning_variants(raw):
    assert normalize_severity(raw) == "warning"


@pytest.mark.parametrize("raw", ["critical", "Critical", "CRITICAL", "  critical", "Critical "])
def test_critical_variants(raw):
    assert normalize_severity(raw) == "critical"


@pytest.mark.parametrize("raw", [None, "", "   ", "info", "page", "warn", "crit", "critical!", "warning-high", 3])
def test_unknown_or_missing_is_other(raw):
    assert normalize_severity(raw) == "other"


def test_custom_mapping():
    mapping = {"critical": ["critical", "page", "p1"], "warning": ["warning", "warn", "minor"]}
    assert normalize_severity("PAGE", mapping) == "critical"
    assert normalize_severity(" P1 ", mapping) == "critical"
    assert normalize_severity("Warn", mapping) == "warning"
    assert normalize_severity("info", mapping) == "other"


@pytest.mark.parametrize("value,expected", [
    ("warning", "warning"), ("Warning", "warning"), ("WARNING", "warning"), (" critical ", "critical"),
    ("other", "other"), ("Khác", "other"), ("khác", "other"), ("info", None), ("", None),
])
def test_parse_filter_value(value, expected):
    assert parse_level(value) == expected


def test_labels_vietnamese():
    assert LABELS == {"critical": "Critical", "warning": "Warning", "other": "Khác"}


def test_collector_stores_raw_and_normalized():
    raw = {"fingerprint": "f1", "startsAt": "2026-11-12T01:00:00Z",
           "labels": {"alertname": "X", "severity": " WARNING "}, "annotations": {}}
    a = normalize_alert(raw, LabelsConfig())
    assert a.severity == "WARNING"  # giá trị gốc (chỉ bỏ khoảng trắng hai đầu)
    assert a.labels["severity"] == " WARNING "  # labels giữ nguyên tuyệt đối
    assert a.severity_normalized == "warning"
    raw["labels"]["severity"] = "P1"
    assert normalize_alert(raw, LabelsConfig()).severity_normalized == "other"
    assert normalize_alert(raw, LabelsConfig(), {"critical": ["p1"], "warning": []}).severity_normalized == "critical"


def test_config_mapping_normalized_and_validated():
    cfg = AppConfig(severity_mapping={"critical": [" Critical ", "PAGE", "page"], "warning": ["Warning"]})
    assert cfg.severity_mapping == {"critical": ["critical", "page"], "warning": ["warning"]}
    # Mặc định
    assert AppConfig().severity_mapping == {"critical": ["critical"], "warning": ["warning"]}
    with pytest.raises(ValueError, match="nhóm mức độ không hợp lệ"):
        AppConfig(severity_mapping={"major": ["x"]})
    with pytest.raises(ValueError, match="cả nhóm"):
        AppConfig(severity_mapping={"critical": ["high"], "warning": ["HIGH"]})
    with pytest.raises(ValueError, match="rỗng"):
        AppConfig(severity_mapping={"critical": ["  "]})


def test_config_mapping_from_yaml(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text("severity_mapping:\n  critical: [Critical, P1]\n  warning: [warning]\n", encoding="utf-8")
    assert load_config(p, env={}).severity_mapping["critical"] == ["critical", "p1"]
    p.write_text("severity_mapping:\n  urgent: [x]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, env={})
