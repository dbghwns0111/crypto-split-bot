"""외부(텔레그램) DB 백업 테스트."""

import gzip
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from database.connection import close_connection, configure, get_connection
from notification.scheduler import NotificationScheduler
from notification.telegram import TelegramNotifier


@pytest.fixture
def file_db(tmp_path):
    path = tmp_path / "t.db"
    configure(str(path))
    conn = get_connection()
    conn.execute("CREATE TABLE x (v TEXT)")
    conn.execute("INSERT INTO x VALUES ('hello')")
    conn.commit()
    yield path
    close_connection()
    configure(None)


def _sched(notifier):
    return NotificationScheduler(notifier, MagicMock(), MagicMock(), {"system": {}})


def test_job_registered_by_default():
    assert _sched(MagicMock())._scheduler.get_job("offsite_backup") is not None


def test_job_sends_valid_gzipped_snapshot(file_db, tmp_path):
    captured = {}

    def fake_send(path, caption, silent=True):
        # 임시 디렉터리가 지워지기 전에 내용 검증
        out = tmp_path / "restored.db"
        with gzip.open(path, "rb") as src:
            out.write_bytes(src.read())
        captured["rows"] = sqlite3.connect(out).execute("SELECT v FROM x").fetchall()
        captured["silent"] = silent
        captured["name"] = path
        return True

    notifier = MagicMock()
    notifier.send_document.side_effect = fake_send
    assert _sched(notifier)._job_offsite_backup() is True
    assert captured["rows"] == [("hello",)]
    assert captured["silent"] is True
    assert captured["name"].endswith(".db.gz")
    notifier.send_db_backup_alert.assert_not_called()


def test_job_alerts_on_upload_failure(file_db):
    notifier = MagicMock()
    notifier.send_document.return_value = False
    assert _sched(notifier)._job_offsite_backup() is False
    notifier.send_db_backup_alert.assert_called_once()
    assert notifier.send_db_backup_alert.call_args[0][0] is False


def test_send_document_posts_multipart(tmp_path):
    f = tmp_path / "a.db.gz"
    f.write_bytes(b"data")
    n = TelegramNotifier("", "")          # 비활성 → 스레드 안 띄움
    n._enabled, n._token, n._chat_id = True, "TOKEN", "123"
    with patch("notification.telegram.requests.post") as post:
        post.return_value.status_code = 200
        assert n.send_document(str(f), "cap") is True
        kwargs = post.call_args.kwargs
        assert post.call_args[0][0].endswith("/sendDocument")
        assert kwargs["data"]["disable_notification"] == "true"
        assert "document" in kwargs["files"]


def test_send_document_disabled_returns_false(tmp_path):
    assert TelegramNotifier("", "").send_document(str(tmp_path / "none"), "") is False
