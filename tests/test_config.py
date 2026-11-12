import pytest

from trucca.config import ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / "config.yml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_and_env_precedence(tmp_path):
    p = write(tmp_path, "alertmanager:\n  url: http://am:9093/\n  poll_interval_seconds: 60\n")
    cfg = load_config(p, env={"TRUCCA__ALERTMANAGER__POLL_INTERVAL_SECONDS": "15",
                              "ALERTMANAGER_TOKEN": "secret", "TRUCCA__ALERTMANAGER__AUTH__TYPE": "bearer",
                              "DATABASE_URL": "postgresql://u:p@db:5432/x"})
    assert cfg.alertmanager.url == "http://am:9093"
    assert cfg.alertmanager.poll_interval_seconds == 15          # env > yml
    assert cfg.alertmanager.auth.token.get_secret_value() == "secret"
    assert cfg.database.url.get_secret_value().startswith("postgresql+psycopg://")
    assert cfg.shifts[1].overnight and cfg.report.top_n == 5     # default
    assert "secret" not in str(cfg.redacted())


def test_invalid_config_has_clear_message(tmp_path):
    p = write(tmp_path, "alertmanager:\n  url: am:9093\n  poll_interval_seconds: 1\ntimezone: Mars/Base\n"
                        "shifts:\n  - {name: CA_1, start: '08:00', end: '08:00'}\nunknown_key: 1\n")
    with pytest.raises(ConfigError) as e:
        load_config(p, env={})
    msg = str(e.value)
    for part in ("alertmanager.url", "alertmanager.poll_interval_seconds", "timezone", "shifts.0", "unknown_key"):
        assert part in msg


def test_bad_yaml(tmp_path):
    p = write(tmp_path, "alertmanager: [unclosed\n")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(p, env={})


def test_basic_auth_requires_username(tmp_path):
    p = write(tmp_path, "alertmanager:\n  auth:\n    type: basic\n")
    with pytest.raises(ConfigError, match="username"):
        load_config(p, env={})


def test_duplicate_shift_names(tmp_path):
    p = write(tmp_path, "shifts:\n  - {name: A, start: '08:00', end: '18:00'}\n  - {name: A, start: '18:00', end: '08:00'}\n")
    with pytest.raises(ConfigError, match="trùng"):
        load_config(p, env={})
