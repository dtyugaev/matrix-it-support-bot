# -*- coding: utf-8 -*-
"""Тесты DDL схемы БД для sqlite и postgresql."""
from __future__ import annotations

import conftest  # noqa: F401

from src.db.schema import POSTGRESQL, SQLITE, ddl

EXPECTED_TABLES = (
    "users",
    "audit_log",
    "attachments",
    "menu_messages",
    "dialog_states",
    "processed_events",
    "feedback",
)


def test_all_tables_present_in_both_dialects():
    for dialect in (SQLITE, POSTGRESQL):
        statements = " ".join(ddl(dialect))
        for table in EXPECTED_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table} " in statements, (dialect, table)


def test_dialect_specific_types():
    assert "AUTOINCREMENT" in " ".join(ddl(SQLITE))
    assert "BIGSERIAL" in " ".join(ddl(POSTGRESQL))


def test_room_is_unique_per_user():
    assert any("users_room_uindex" in item for item in ddl(SQLITE))


def test_unknown_dialect_raises():
    try:
        ddl("oracle")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Ожидалась ValueError")
