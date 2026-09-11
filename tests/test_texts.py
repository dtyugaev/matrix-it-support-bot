# -*- coding: utf-8 -*-
"""Тесты централизованного хранилища фраз (п. 9 ТЗ)."""
from __future__ import annotations

import conftest  # noqa: F401

from src.texts import TEXTS, t

CRITICAL_KEYS = (
    "greeting_guest",
    "room_owned_by_other",
    "menu_title_main",
    "issue_card",
    "find_invalid_key",
    "attachments_sent",
    "admin_help",
    "help_navigation",
    "unknown_command",
)


def test_critical_keys_exist():
    for key in CRITICAL_KEYS:
        assert key in TEXTS, key


def test_no_empty_phrases():
    for key, value in TEXTS.items():
        assert value.strip(), key


def test_format_substitution():
    assert t("unknown_command", command="!foo") == "Неизвестная команда `!foo`."


def test_unknown_key_raises():
    try:
        t("нет_такого_ключа")
    except KeyError as exc:
        assert "нет_такого_ключа" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Ожидалась KeyError")
