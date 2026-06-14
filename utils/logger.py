"""싱글턴 로거 — 콘솔 + 파일 로테이션."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_loggers: dict[str, logging.Logger] = {}
_ROOT = Path(__file__).parent.parent


def get_logger(name: str = "cryptosplit") -> logging.Logger:
    """name 로거를 반환한다. 같은 name은 싱글턴."""
    if name in _loggers:
        return _loggers[name]

    level_str, log_dir, max_bytes, backup_count = _load_log_cfg()
    level = getattr(logging, level_str.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 콘솔 핸들러
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # 파일 핸들러
        log_path = _ROOT / log_dir
        log_path.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_path / f"{name}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    _loggers[name] = logger
    return logger


def _load_log_cfg() -> tuple[str, str, int, int]:
    """설정에서 로그 파라미터를 읽는다. 실패 시 기본값 반환."""
    try:
        from config.loader import get
        return (
            get("logging.level", "INFO"),
            get("logging.dir", "logs"),
            get("logging.max_bytes", 10 * 1024 * 1024),
            get("logging.backup_count", 5),
        )
    except Exception:
        return "INFO", "logs", 10 * 1024 * 1024, 5


def reset_loggers() -> None:
    """테스트에서 싱글턴을 초기화할 때 사용한다."""
    for logger in _loggers.values():
        logger.handlers.clear()
    _loggers.clear()
