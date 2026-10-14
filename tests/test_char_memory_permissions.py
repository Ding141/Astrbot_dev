from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from data.plugins.astrbot_plugin_char import memory_panel_utils, memory_utils


@pytest.mark.parametrize(
    ("raw_ids", "expected"),
    [
        (None, ()),
        (["", "  "], ()),
        (
            [123, "123", " wx-user@im.wechat ", "wx-user@im.wechat"],
            ("123", "wx-user@im.wechat"),
        ),
        ("123，wx-user@im.wechat astrbot", ("123", "wx-user@im.wechat", "astrbot")),
    ],
)
def test_memory_admin_uids_accept_non_numeric_platform_ids(raw_ids, expected):
    assert memory_utils._normalize_admin_qq_ids(raw_ids) == expected


@pytest.mark.parametrize(
    ("role", "uid", "extra_admins", "allowed"),
    [
        ("admin", "wx-user@im.wechat", (), True),
        ("admin", "astrbot", (), True),
        ("member", "123", ("123",), True),
        ("member", "wx-user@im.wechat", ("wx-user@im.wechat",), True),
        ("member", "wx-user@im.wechat", (), False),
        ("member", "456", ("123",), False),
        (
            "member",
            "wx-user@im.wechat",
            ("test:FriendMessage:wx-user@im.wechat",),
            False,
        ),
    ],
)
def test_memory_permissions_use_core_role_or_explicit_uid(
    monkeypatch, role, uid, extra_admins, allowed
):
    monkeypatch.setattr(memory_utils, "_configured_admin_qq_ids", extra_admins)
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember(user_id=uid, nickname="Test User")
    event = AstrMessageEvent(
        "", message, PlatformMetadata("weixin_oc", "Test", "test"), uid
    )
    event.role = role

    assert memory_utils.UserMemoryStore._is_admin_event(event) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "member"])
async def test_memory_panel_command_preserves_authorization(monkeypatch, role):
    monkeypatch.setattr(memory_utils, "_configured_admin_qq_ids", ())
    uid = "wx-user@im.wechat"
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember(user_id=uid, nickname="Test User")
    event = AstrMessageEvent(
        "/记忆面板 状态", message, PlatformMetadata("weixin_oc", "Test", "test"), uid
    )
    event.role = role
    store = memory_utils.UserMemoryStore.__new__(memory_utils.UserMemoryStore)
    store.observe_user = Mock()
    manager = Mock()
    manager.status.return_value = {
        "running": False,
        "require_token": False,
        "url": "http://127.0.0.1:7835/",
    }
    manager_factory = Mock(return_value=manager)
    monkeypatch.setattr(memory_panel_utils, "init_user_memory_store", lambda: store)
    monkeypatch.setattr(
        memory_panel_utils, "init_memory_panel_manager", manager_factory
    )

    replies = [
        reply
        async for reply in memory_panel_utils.handle_memory_panel_command(
            event, SimpleNamespace(), SimpleNamespace()
        )
    ]
    assert len(replies) == 1
    text = replies[0].chain[0].text
    if role == "admin":
        assert "记忆面板未启动" in text
        manager_factory.assert_called_once()
    else:
        assert text == "记忆面板仅允许管理员使用。"
        manager_factory.assert_not_called()
    manager.ensure_started.assert_not_called()
