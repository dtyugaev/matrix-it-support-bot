# -*- coding: utf-8 -*-
"""DDL схемы БД для SQLite и PostgreSQL.

Схема одна и та же, различаются только типы автоинкремента и метки времени,
поэтому DDL собирается функцией :func:`ddl` по имени диалекта.
"""
from __future__ import annotations

from typing import Final

SQLITE: Final[str] = "sqlite"
POSTGRESQL: Final[str] = "postgresql"

_TYPES: Final[dict[str, dict[str, str]]] = {
    SQLITE: {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "ts": "TEXT", "bool": "INTEGER", "text": "TEXT"},
    POSTGRESQL: {"pk": "BIGSERIAL PRIMARY KEY", "ts": "TIMESTAMP", "bool": "BOOLEAN", "text": "TEXT"},
}

_TABLES: Final[tuple[str, ...]] = (
    # Пользователи: одна строка = одна пара (room_id, user_id), п. 7.1 ТЗ
    """
    CREATE TABLE IF NOT EXISTS users (
        id {pk},
        room_id {text} NOT NULL,
        user_id {text} NOT NULL,
        displayname {text},
        email {text},
        jira_login {text} NOT NULL,
        created_at {ts} NOT NULL,
        updated_at {ts},
        last_seen_at {ts}
    )
    """,
    # Журнал действий пользователей, п. 7.2 ТЗ
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id {pk},
        room_id {text},
        user_id {text},
        action {text} NOT NULL,
        details {text},
        created_at {ts} NOT NULL
    )
    """,
    # Реестр отправленных вложений для автоочистки по TTL, п. 4.4 ТЗ
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id {pk},
        issue_key {text} NOT NULL,
        room_id {text} NOT NULL,
        user_id {text},
        event_id {text},
        mxc_uri {text},
        local_path {text},
        filename {text},
        created_at {ts} NOT NULL,
        expires_at {ts} NOT NULL,
        deleted_at {ts}
    )
    """,
    # Сообщения-меню: связь event_id -> меню, чтобы разбирать реакции m.reaction
    """
    CREATE TABLE IF NOT EXISTS menu_messages (
        id {pk},
        event_id {text} NOT NULL UNIQUE,
        room_id {text} NOT NULL,
        user_id {text},
        menu_id {text} NOT NULL,
        payload {text},
        created_at {ts} NOT NULL
    )
    """,
    # Состояние диалога в комнате (замена FSM-хранилища aiogram)
    """
    CREATE TABLE IF NOT EXISTS dialog_states (
        room_id {text} PRIMARY KEY,
        user_id {text},
        state {text} NOT NULL,
        payload {text},
        updated_at {ts} NOT NULL
    )
    """,
    # Обработанные события плагина Jira Status Listener (защита от дублей)
    """
    CREATE TABLE IF NOT EXISTS processed_events (
        event_key {text} PRIMARY KEY,
        issue_key {text},
        status {text},
        created_at {ts} NOT NULL
    )
    """,
    # Обращения обратной связи (пересылаются в комнату администраторов)
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id {pk},
        feedback_id {text} NOT NULL UNIQUE,
        room_id {text} NOT NULL,
        user_id {text} NOT NULL,
        message {text},
        created_at {ts} NOT NULL,
        answered_at {ts}
    )
    """,
)

_INDEXES: Final[tuple[str, ...]] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS users_room_uindex ON users (room_id)",
    "CREATE INDEX IF NOT EXISTS users_user_idx ON users (user_id)",
    "CREATE INDEX IF NOT EXISTS users_jira_login_idx ON users (jira_login)",
    "CREATE INDEX IF NOT EXISTS audit_user_idx ON audit_log (user_id)",
    "CREATE INDEX IF NOT EXISTS audit_created_idx ON audit_log (created_at)",
    "CREATE INDEX IF NOT EXISTS attachments_expires_idx ON attachments (expires_at)",
    "CREATE INDEX IF NOT EXISTS menu_room_idx ON menu_messages (room_id)",
)


def ddl(dialect: str) -> list[str]:
    """Вернуть список DDL-запросов для указанного диалекта БД."""
    dialect = dialect.lower()
    if dialect not in _TYPES:
        raise ValueError(f"Неподдерживаемый тип БД: {dialect}")
    types = _TYPES[dialect]
    statements = [" ".join(table.format(**types).split()) for table in _TABLES]
    statements.extend(_INDEXES)
    return statements
