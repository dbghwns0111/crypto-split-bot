"""설정 로더 — settings.yaml + .env 병합, 싱글턴 반환."""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_config: dict | None = None
_ROOT = Path(__file__).parent.parent


def load_config(yaml_path: str | Path | None = None) -> dict:
    """yaml 파일을 읽고 .env 환경변수를 오버라이드한 설정 dict를 반환한다."""
    if yaml_path is None:
        yaml_path = _ROOT / "config" / "settings.yaml"

    # .env 로드 (이미 설정된 환경변수는 덮어쓰지 않음)
    env_path = _ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    with open(yaml_path, encoding="utf-8") as f:
        cfg: dict = yaml.safe_load(f)

    # 민감한 값 환경변수 주입
    _inject_env(cfg)
    return cfg


def _inject_env(cfg: dict) -> None:
    """환경변수에서 API 키 / 토큰을 cfg에 주입한다."""
    cfg.setdefault("exchange", {})
    cfg["exchange"]["access_key"] = os.getenv("BITHUMB_ACCESS_KEY", "")
    cfg["exchange"]["secret_key"] = os.getenv("BITHUMB_SECRET_KEY", "")

    cfg.setdefault("telegram", {})
    cfg["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "")


def get_config(yaml_path: str | Path | None = None) -> dict:
    """싱글턴 설정 dict를 반환한다. 최초 호출 시 한 번만 로드한다."""
    global _config
    if _config is None:
        _config = load_config(yaml_path)
    return _config


def reset_config() -> None:
    """테스트에서 싱글턴을 초기화할 때 사용한다."""
    global _config
    _config = None


def get(key_path: str, default: Any = None) -> Any:
    """점(.) 구분 경로로 중첩 설정값을 조회한다.

    예: get("web.port") → 5000
    """
    cfg = get_config()
    keys = key_path.split(".")
    node: Any = cfg
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
        if node is None:
            return default
    return node
