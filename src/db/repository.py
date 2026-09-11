# -*- coding: utf-8 -*-
"""Репозитории доступа к данным: пользователи, аудит, вложения, меню, состояния.

Все методы асинхронные и работают через :class:`src.db.base.Database`.
Формат метки времени единый (``TS_FORMAT``) — он корректно сравнивается
лексикографически в SQLite и приводится к TIMESTAMP в PostgreSQL.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from src.db.base import Database, Row

logger = logging.getLogger(__name__)

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    """Текущее локальное время в формате хранения."""
    return dt.datetime.now().strftime(TS_FORMAT)


def to_ts(value: dt.datetime) -> str:
    return value.strftime(TS_FORMAT)


class UserRepository:
    """Таблица ``users``: привязка (room_id, user_id) к профилю Jira."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_room(self, room_id: str) -> Row | None:
        """Владелец комнаты (одна комната = ровно один пользователь, п. 1.5)."""
        return await self._db.fetchone("SELECT * FROM users WHERE room_id = ?", (room_id,))

    async def get(self, room_id: str, user_id: str) -> Row | None:
        return await self._db.fetchone(
            "SELECT * FROM users WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        )

    async def add(
        self,
        room_id: str,
        user_id: str,
        displayname: str,
        jira_login: str,
        email: str | None,
    ) -> Row:
        """Создать привязку комнаты к пользователю."""
        timestamp = now_str()
        await self._db.execute(
            "INSERT INTO users (room_id, user_id, displayname, email, jira_login, "
            "created_at, updated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (room_id, user_id, displayname, email, jira_login, timestamp, timestamp, timestamp),
        )
        logger.info("В БД создана привязка комнаты %s к пользователю %s", room_id, user_id)
        row = await self.get(room_id, user_id)
        assert row is not None
        return row

    async def update_email(self, user_id: str, email: str | None) -> None:
        await self._db.execute(
            "UPDATE users SET email = ?, updated_at = ? WHERE user_id = ?",
            (email, now_str(), user_id),
        )

    async def touch(self, room_id: str) -> None:
        await self._db.execute(
            "UPDATE users SET last_seen_at = ? WHERE room_id = ?", (now_str(), room_id)
        )

    async def delete_binding(self, room_id: str, user_id: str) -> int:
        """Удалить связь (room_id, user_id) — п. 1.4 и 1.7 ТЗ."""
        deleted = await self._db.execute(
            "DELETE FROM users WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        )
        logger.info("Удалена привязка (%s, %s): строк=%s", room_id, user_id, deleted)
        return deleted

    async def delete_room(self, room_id: str) -> int:
        deleted = await self._db.execute("DELETE FROM users WHERE room_id = ?", (room_id,))
        logger.info("Удалены привязки комнаты %s: строк=%s", room_id, deleted)
        return deleted

    async def rooms_of_user(self, user_id: str) -> list[Row]:
        """Все комнаты пользователя — уведомления уходят во все (п. 1.6)."""
        return await self._db.fetchall(
            "SELECT * FROM users WHERE user_id = ? ORDER BY created_at", (user_id,)
        )

    async def rooms_of_jira_login(self, jira_login: str) -> list[Row]:
        return await self._db.fetchall(
            "SELECT * FROM users WHERE LOWER(jira_login) = LOWER(?) ORDER BY created_at",
            (jira_login,),
        )

    async def all_users(self) -> list[Row]:
        return await self._db.fetchall("SELECT * FROM users ORDER BY created_at")

    async def search(self, pattern: str) -> list[Row]:
        like = f"%{pattern.lower()}%"
        return await self._db.fetchall(
            "SELECT * FROM users WHERE LOWER(user_id) LIKE ? OR LOWER(displayname) LIKE ? "
            "OR LOWER(email) LIKE ? OR LOWER(jira_login) LIKE ? ORDER BY created_at",
            (like, like, like, like),
        )

    async def counters(self) -> dict[str, int]:
        rooms = await self._db.fetchone("SELECT COUNT(*) AS c FROM users")
        users = await self._db.fetchone("SELECT COUNT(DISTINCT user_id) AS c FROM users")
        return {"rooms": int((rooms or {}).get("c", 0)), "users": int((users or {}).get("c", 0))}


class AuditRepository:
    """Таблица ``audit_log`` — журнал действий пользователей (п. 7.2)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self, room_id: str | None, user_id: str | None, action: str, details: str = ""
    ) -> None:
        await self._db.execute(
            "INSERT INTO audit_log (room_id, user_id, action, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (room_id, user_id, action, details[:2000], now_str()),
        )

    async def list(self, user_id: str | None = None, days: int = 7, limit: int = 200) -> list[Row]:
        since = to_ts(dt.datetime.now() - dt.timedelta(days=days))
        if user_id:
            return await self._db.fetchall(
                "SELECT * FROM audit_log WHERE user_id = ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, since, limit),
            )
        return await self._db.fetchall(
            "SELECT * FROM audit_log WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        )

    async def stats(self, days: int | None) -> list[Row]:
        """Количество действий по типам за период (``None`` — за всё время)."""
        if days is None:
            return await self._db.fetchall(
                "SELECT action, COUNT(*) AS total FROM audit_log GROUP BY action ORDER BY total DESC"
            )
        since = to_ts(dt.datetime.now() - dt.timedelta(days=days))
        return await self._db.fetchall(
            "SELECT action, COUNT(*) AS total FROM audit_log WHERE created_at >= ? "
            "GROUP BY action ORDER BY total DESC",
            (since,),
        )


class AttachmentRepository:
    """Таблица ``attachments`` — реестр отправленных архивов для очистки по TTL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def register(
        self,
        issue_key: str,
        room_id: str,
        user_id: str,
        event_id: str | None,
        mxc_uri: str | None,
        local_path: str | None,
        filename: str,
        ttl_seconds: int,
    ) -> None:
        created = dt.datetime.now()
        await self._db.execute(
            "INSERT INTO attachments (issue_key, room_id, user_id, event_id, mxc_uri, "
            "local_path, filename, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                issue_key,
                room_id,
                user_id,
                event_id,
                mxc_uri,
                local_path,
                filename,
                to_ts(created),
                to_ts(created + dt.timedelta(seconds=ttl_seconds)),
            ),
        )

    async def expired(self) -> list[Row]:
        return await self._db.fetchall(
            "SELECT * FROM attachments WHERE deleted_at IS NULL AND expires_at <= ?",
            (now_str(),),
        )

    async def mark_deleted(self, attachment_id: int) -> None:
        await self._db.execute(
            "UPDATE attachments SET deleted_at = ? WHERE id = ?", (now_str(), attachment_id)
        )

    async def pending_count(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS c FROM attachments WHERE deleted_at IS NULL"
        )
        return int((row or {}).get("c", 0))


class MenuRepository:
    """Таблица ``menu_messages`` — сопоставление event_id сообщения и меню.

    Нужна для обработки событий ``m.reaction``: по ``event_id`` сообщения,
    на которое поставили реакцию, определяется активное меню и его контекст
    (например, номер заявки или страница списка).
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self,
        event_id: str,
        room_id: str,
        user_id: str | None,
        menu_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO menu_messages (event_id, room_id, user_id, menu_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, room_id, user_id, menu_id, json.dumps(payload or {}, ensure_ascii=False), now_str()),
        )

    async def get(self, event_id: str) -> Row | None:
        row = await self._db.fetchone("SELECT * FROM menu_messages WHERE event_id = ?", (event_id,))
        if row and row.get("payload"):
            try:
                row["payload"] = json.loads(row["payload"])
            except (TypeError, ValueError):
                row["payload"] = {}
        elif row:
            row["payload"] = {}
        return row

    async def cleanup_room(self, room_id: str) -> None:
        await self._db.execute("DELETE FROM menu_messages WHERE room_id = ?", (room_id,))

    async def cleanup_older_than(self, days: int = 30) -> None:
        since = to_ts(dt.datetime.now() - dt.timedelta(days=days))
        await self._db.execute("DELETE FROM menu_messages WHERE created_at < ?", (since,))


class StateRepository:
    """Таблица ``dialog_states`` — текущее состояние диалога в комнате."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def set(
        self, room_id: str, user_id: str, state: str, payload: dict[str, Any] | None = None
    ) -> None:
        serialized = json.dumps(payload or {}, ensure_ascii=False)
        updated = await self._db.execute(
            "UPDATE dialog_states SET user_id = ?, state = ?, payload = ?, updated_at = ? "
            "WHERE room_id = ?",
            (user_id, state, serialized, now_str(), room_id),
        )
        if not updated:
            await self._db.execute(
                "INSERT INTO dialog_states (room_id, user_id, state, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (room_id, user_id, state, serialized, now_str()),
            )

    async def get(self, room_id: str) -> Row | None:
        row = await self._db.fetchone("SELECT * FROM dialog_states WHERE room_id = ?", (room_id,))
        if row:
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                row["payload"] = {}
        return row

    async def clear(self, room_id: str) -> None:
        await self._db.execute("DELETE FROM dialog_states WHERE room_id = ?", (room_id,))


class EventRepository:
    """Таблица ``processed_events`` — защита от повторной отправки уведомлений."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def is_processed(self, event_key: str) -> bool:
        row = await self._db.fetchone(
            "SELECT event_key FROM processed_events WHERE event_key = ?", (event_key,)
        )
        return row is not None

    async def mark(self, event_key: str, issue_key: str, status: str) -> None:
        await self._db.execute(
            "INSERT INTO processed_events (event_key, issue_key, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            (event_key, issue_key, status, now_str()),
        )

    async def cleanup(self, days: int = 30) -> None:
        since = to_ts(dt.datetime.now() - dt.timedelta(days=days))
        await self._db.execute("DELETE FROM processed_events WHERE created_at < ?", (since,))


class FeedbackRepository:
    """Таблица ``feedback`` — обращения обратной связи."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, feedback_id: str, room_id: str, user_id: str, message: str) -> None:
        await self._db.execute(
            "INSERT INTO feedback (feedback_id, room_id, user_id, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (feedback_id, room_id, user_id, message[:4000], now_str()),
        )

    async def get(self, feedback_id: str) -> Row | None:
        return await self._db.fetchone(
            "SELECT * FROM feedback WHERE feedback_id = ?", (feedback_id,)
        )

    async def mark_answered(self, feedback_id: str) -> None:
        await self._db.execute(
            "UPDATE feedback SET answered_at = ? WHERE feedback_id = ?", (now_str(), feedback_id)
        )


class Repositories:
    """Контейнер со всеми репозиториями — удобно передавать одним объектом."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.audit = AuditRepository(db)
        self.attachments = AttachmentRepository(db)
        self.menus = MenuRepository(db)
        self.states = StateRepository(db)
        self.events = EventRepository(db)
        self.feedback = FeedbackRepository(db)
