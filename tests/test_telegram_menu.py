"""텔레그램 명령어 메뉴(setMyCommands) 테스트."""

import re
from unittest.mock import MagicMock, patch

from notification.telegram_handler import BOT_COMMANDS, TelegramCommandHandler


def _handler():
    return TelegramCommandHandler(MagicMock(), MagicMock(), {}, "TOKEN", "1")


def test_command_names_valid_for_telegram():
    for name, desc in BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name), name
        assert 1 <= len(desc) <= 256


def test_every_menu_command_is_handled():
    h = _handler()
    for name, _ in BOT_COMMANDS:
        if name in ("status", "balance", "pnl", "report", "stopall", "resumeall"):
            continue  # 실제 조회/동작이 일어나는 명령은 여기서 실행하지 않는다
        reply = h._dispatch(name, [])
        assert "알 수 없는 명령어" not in reply, name


def test_register_commands_posts_list():
    with patch("notification.telegram_handler.requests.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"ok": True}
        assert _handler().register_commands() is True
        url = post.call_args[0][0]
        body = post.call_args.kwargs["json"]
        assert url.endswith("/botTOKEN/setMyCommands")
        assert [c["command"] for c in body["commands"]] == [n for n, _ in BOT_COMMANDS]


def test_register_commands_failure_is_safe():
    with patch("notification.telegram_handler.requests.post", side_effect=OSError("down")):
        assert _handler().register_commands() is False
