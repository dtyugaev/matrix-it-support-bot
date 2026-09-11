# -*- coding: utf-8 -*-
"""Интеграционные тесты репозиториев на реальной SQLite-базе."""
from __future__ import annotations

import asyncio
import os
import tempfile

import conftest  # noqa: F401

from src.config import Config
from src.db.base import Database
from src.db.repository import Repositories


def _config(tmp_path: str) -> Config:
    return Config(
        {
            "matrix": {
                "homeserver": "https://x",
                "user_id": "@bot:otr.ru",
                "access_token": "t",
            },
            "jira": {"url": "https://j", "login": "l", "password": "p"},
            "database": {"type": "sqlite", "sqlite": {"path": tmp_path}},
        }
    )


def _run(coro):
    return asyncio.run(coro)


def test_user_lifecycle_and_multiple_rooms():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(_config(os.path.join(tmp, "bot.db")))
            await db.connect()
            await db.init_schema()
            repos = Repositories(db)

            await repos.users.add("!room1:otr.ru", "@user:otr.ru", "Иванов И.И.", "user", "u@otr.ru")
            await repos.users.add("!room2:otr.ru", "@user:otr.ru", "Иванов И.И.", "user", "u@otr.ru")

            # одна комната -> ровно один пользователь
            owner = await repos.users.get_by_room("!room1:otr.ru")
            assert owner is not None and owner["user_id"] == "@user:otr.ru"

            # уведомления должны уходить во все комнаты пользователя
            rooms = await repos.users.rooms_of_user("@user:otr.ru")
            assert len(rooms) == 2
            by_login = await repos.users.rooms_of_jira_login("USER")
            assert len(by_login) == 2

            # выход из одной комнаты не затрагивает вторую
            await repos.users.delete_binding("!room1:otr.ru", "@user:otr.ru")
            assert await repos.users.get_by_room("!room1:otr.ru") is None
            assert len(await repos.users.rooms_of_user("@user:otr.ru")) == 1

            counters = await repos.users.counters()
            assert counters == {"rooms": 1, "users": 1}
            await db.close()

    _run(scenario())


def test_menu_state_and_audit():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(_config(os.path.join(tmp, "bot.db")))
            await db.connect()
            await db.init_schema()
            repos = Repositories(db)

            await repos.menus.save(
                "$event1", "!room:otr.ru", "@user:otr.ru", "main", {"actions": {"📝": {"action": "create_issue"}}}
            )
            menu = await repos.menus.get("$event1")
            assert menu is not None
            assert menu["payload"]["actions"]["📝"]["action"] == "create_issue"

            await repos.states.set("!room:otr.ru", "@user:otr.ru", "wait_subject", {"a": 1})
            await repos.states.set("!room:otr.ru", "@user:otr.ru", "wait_description", {"a": 2})
            state = await repos.states.get("!room:otr.ru")
            assert state["state"] == "wait_description"
            assert state["payload"] == {"a": 2}
            await repos.states.clear("!room:otr.ru")
            assert await repos.states.get("!room:otr.ru") is None

            await repos.audit.add("!room:otr.ru", "@user:otr.ru", "Создание заявки", "IT-1")
            await repos.audit.add("!room:otr.ru", "@user:otr.ru", "Создание заявки", "IT-2")
            rows = await repos.audit.list("@user:otr.ru", days=1)
            assert len(rows) == 2
            stats = await repos.audit.stats(days=7)
            assert stats[0]["action"] == "Создание заявки"
            assert stats[0]["total"] == 2
            await db.close()

    _run(scenario())


def test_attachments_ttl_registry():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(_config(os.path.join(tmp, "bot.db")))
            await db.connect()
            await db.init_schema()
            repos = Repositories(db)

            # уже просроченное вложение (ttl = -10 секунд)
            await repos.attachments.register(
                "IT-1", "!room:otr.ru", "@user:otr.ru", "$ev", "mxc://otr.ru/abc",
                "/tmp/a.zip", "a.zip", ttl_seconds=-10,
            )
            # свежее вложение
            await repos.attachments.register(
                "IT-2", "!room:otr.ru", "@user:otr.ru", "$ev2", "mxc://otr.ru/def",
                "/tmp/b.zip", "b.zip", ttl_seconds=3600,
            )
            expired = await repos.attachments.expired()
            assert [row["issue_key"] for row in expired] == ["IT-1"]
            assert await repos.attachments.pending_count() == 2

            await repos.attachments.mark_deleted(int(expired[0]["id"]))
            assert await repos.attachments.expired() == []
            assert await repos.attachments.pending_count() == 1

            assert not await repos.events.is_processed("IT-1:В работе:1")
            await repos.events.mark("IT-1:В работе:1", "IT-1", "В работе")
            assert await repos.events.is_processed("IT-1:В работе:1")

            await repos.feedback.add("12345", "!room:otr.ru", "@user:otr.ru", "не работает меню")
            feedback = await repos.feedback.get("12345")
            assert feedback["message"] == "не работает меню"
            await repos.feedback.mark_answered("12345")
            assert (await repos.feedback.get("12345"))["answered_at"]
            await db.close()

    _run(scenario())
