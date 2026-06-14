"""1단계: 프로젝트 기본 세팅 검증 테스트."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


# ── 픽스처 ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 후 설정 싱글턴을 초기화한다."""
    from config.loader import reset_config
    reset_config()
    yield
    reset_config()


# ── settings.yaml 존재 및 로드 ───────────────────────────────────────────────

def test_settings_yaml_exists():
    assert (ROOT / "config" / "settings.yaml").is_file()


def test_load_config_returns_dict():
    from config.loader import load_config
    cfg = load_config()
    assert isinstance(cfg, dict)


def test_required_top_level_keys():
    from config.loader import load_config
    cfg = load_config()
    for key in ("exchange", "pairs", "strategy", "database", "telegram", "web", "logging", "report"):
        assert key in cfg, f"필수 키 없음: {key}"


# ── pairs 설정 ───────────────────────────────────────────────────────────────

def test_pairs_eth_present():
    from config.loader import load_config
    cfg = load_config()
    assert "ETH" in cfg["pairs"]
    eth = cfg["pairs"]["ETH"]
    assert eth["capital"] == 2500000
    assert eth["order_amount"] == 50000
    assert eth["max_rounds"] == 50


def test_pairs_usdt_present():
    from config.loader import load_config
    cfg = load_config()
    assert "USDT" in cfg["pairs"]
    usdt = cfg["pairs"]["USDT"]
    assert usdt["ticker"] == "KRW-USDT"


# ── strategy 설정 ────────────────────────────────────────────────────────────

def test_strategy_trigger_range():
    from config.loader import load_config
    cfg = load_config()
    s = cfg["strategy"]
    assert s["trigger_min_pct"] < s["trigger_max_pct"]
    assert s["trigger_min_pct"] == pytest.approx(0.3)
    assert s["trigger_max_pct"] == pytest.approx(1.0)


def test_strategy_zone_sum_100():
    from config.loader import load_config
    cfg = load_config()
    s = cfg["strategy"]
    total = s["zone_normal_pct"] + s["zone_defensive_pct"] + s["zone_emergency_pct"]
    assert total == 100


def test_strategy_order_timeout():
    from config.loader import load_config
    cfg = load_config()
    assert cfg["strategy"]["order_timeout"] == 300


# ── web / db 설정 ────────────────────────────────────────────────────────────

def test_web_port_default():
    from config.loader import load_config
    cfg = load_config()
    assert cfg["web"]["port"] == 5000


def test_database_path_set():
    from config.loader import load_config
    cfg = load_config()
    assert cfg["database"]["path"] == "data/cryptosplit.db"


# ── 환경변수 오버라이드 ──────────────────────────────────────────────────────

def test_env_override_access_key(monkeypatch):
    monkeypatch.setenv("BITHUMB_ACCESS_KEY", "test_access_key")
    from config.loader import load_config
    cfg = load_config()
    assert cfg["exchange"]["access_key"] == "test_access_key"


def test_env_override_secret_key(monkeypatch):
    monkeypatch.setenv("BITHUMB_SECRET_KEY", "test_secret_key")
    from config.loader import load_config
    cfg = load_config()
    assert cfg["exchange"]["secret_key"] == "test_secret_key"


def test_env_override_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    from config.loader import load_config
    cfg = load_config()
    assert cfg["telegram"]["bot_token"] == "1234:ABCD"
    assert cfg["telegram"]["chat_id"] == "9999"


# ── 싱글턴 동작 ─────────────────────────────────────────────────────────────

def test_get_config_singleton():
    from config.loader import get_config
    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


# ── get() 헬퍼 ──────────────────────────────────────────────────────────────

def test_get_helper_nested():
    from config.loader import get
    assert get("web.port") == 5000


def test_get_helper_default():
    from config.loader import get
    assert get("nonexistent.key", default=42) == 42


# ── 파일/폴더 구조 ───────────────────────────────────────────────────────────

def test_gitignore_exists():
    assert (ROOT / ".gitignore").is_file()


def test_env_example_exists():
    assert (ROOT / ".env.example").is_file()


def test_required_directories():
    for d in ("config", "database", "exchange", "strategy", "order",
              "portfolio", "notification", "utils", "tests", "web"):
        assert (ROOT / d).is_dir(), f"디렉토리 없음: {d}"
