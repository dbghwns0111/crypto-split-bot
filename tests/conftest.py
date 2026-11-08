"""공용 pytest 설정."""

import pytest


@pytest.fixture(autouse=True)
def _no_cancel_wait(monkeypatch):
    """order.canceller.cancel_confirmed 의 1초 대기를 테스트에서 제거한다."""
    import order.canceller as canceller
    monkeypatch.setattr(canceller, "_sleep", lambda _s: None)
