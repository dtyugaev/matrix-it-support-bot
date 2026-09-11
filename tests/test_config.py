# -*- coding: utf-8 -*-
"""Тесты конфигурации: значения по умолчанию, валидация, маскирование секретов."""
from __future__ import annotations

import conftest  # noqa: F401

from src.config import Config, ConfigError, deep_merge

VALID = {
    "matrix": {
        "homeserver": "https://connect.otr.ru",
        "user_id": "@it_support_bot:otr.ru",
        "access_token": "secret-token",
    },
    "jira": {"url": "https://jira-kis.otr.ru", "login": "bot", "password": "secret"},
}


def test_defaults_are_applied():
    config = Config(VALID)
    assert config.get("jira.issues_per_page") == 5
    assert config.get("jira.attachments.attachment_ttl") == 604800
    assert config.get("menu.menu_items_as_list") is True
    assert config.get("administration.notify_events.jira_error") is True


def test_user_values_override_defaults():
    config = Config(deep_merge(VALID, {"jira": {"issues_per_page": 7}}))
    assert config.get("jira.issues_per_page") == 7
    assert config.get("jira.issues_max_pages") == 10


def test_validate_requires_mandatory_keys():
    try:
        Config({"matrix": {"homeserver": "https://x"}}).validate()
    except ConfigError as exc:
        assert "matrix.user_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Ожидалась ConfigError")


def test_validate_checks_database_type():
    config = Config(deep_merge(VALID, {"database": {"type": "mysql"}}))
    try:
        config.validate()
    except ConfigError as exc:
        assert "database.type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Ожидалась ConfigError")


def test_validate_checks_user_id_format():
    config = Config(deep_merge(VALID, {"matrix": {"user_id": "it_support_bot"}}))
    try:
        config.validate()
    except ConfigError as exc:
        assert "matrix.user_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Ожидалась ConfigError")


def test_secrets_are_masked():
    dumped = Config(VALID).safe_dump()
    assert dumped["matrix"]["access_token"] == "***"
    assert dumped["jira"]["password"] == "***"
    assert dumped["jira"]["url"] == "https://jira-kis.otr.ru"


def test_path_of_returns_absolute_path():
    path = Config(VALID).path_of("database.sqlite.path")
    assert path.endswith("storage/bot.db")
    assert path.startswith("/")
