"""공용 pytest 설정."""

import builtins
import os
import shutil

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PROTECTED = {
    os.path.join(_ROOT, "config", "settings.yaml"),
    os.path.join(_ROOT, ".env"),
}


@pytest.fixture(autouse=True)
def _no_cancel_wait(monkeypatch):
    """order.canceller.cancel_confirmed 의 1초 대기를 테스트에서 제거한다."""
    import order.canceller as canceller
    monkeypatch.setattr(canceller, "_sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _protect_real_config(monkeypatch, tmp_path):
    """테스트가 운영 중인 config/settings.yaml·.env 를 덮어쓰지 못하게 막는다.

    웹 설정 저장 API(/api/settings 등)는 실제 파일 경로에 직접 쓰므로, 테스트에서
    호출하면 운영 봇의 설정(예: IP 화이트리스트)이 바뀌어 버린다 — 2026-11-08 실제로
    test_auth 가 화이트리스트를 10.73.56.0/24 로 덮어써 대시보드 접속이 막혔다.
    쓰기 모드로 열면 tmp_path 의 사본으로 돌린다(읽기는 그대로).
    """
    real_open = builtins.open
    redirected: dict[str, str] = {}

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and any(m in mode for m in "wax+"):
            path = os.path.abspath(os.fspath(file))
            if path in _PROTECTED:
                if path not in redirected:
                    copy = str(tmp_path / ("protected_" + os.path.basename(path)))
                    if os.path.exists(path):
                        shutil.copy(path, copy)
                    redirected[path] = copy
                file = redirected[path]
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    try:
        import dotenv
        import dotenv.main

        real_set_key = dotenv.main.set_key

        def safe_set_key(dotenv_path, *args, **kwargs):
            if os.path.abspath(os.fspath(dotenv_path)) in _PROTECTED:
                raise RuntimeError(f"테스트에서 실제 .env 수정 금지: {dotenv_path}")
            return real_set_key(dotenv_path, *args, **kwargs)

        monkeypatch.setattr(dotenv, "set_key", safe_set_key)
        monkeypatch.setattr(dotenv.main, "set_key", safe_set_key)
    except ImportError:
        pass
