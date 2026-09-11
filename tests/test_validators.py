# -*- coding: utf-8 -*-
"""Тесты валидации ввода: номер заявки, Matrix ID, localpart."""
from __future__ import annotations

import conftest  # noqa: F401

from src.utils.validators import is_valid_mxid, localpart, normalize_issue_key


def test_normalize_issue_key_ok():
    assert normalize_issue_key("IT-148764", "IT") == "IT-148764"
    assert normalize_issue_key("  it-148764  ", "IT") == "IT-148764"
    assert normalize_issue_key("it-1", "it") == "IT-1"


def test_normalize_issue_key_rejects_bad_format():
    for value in ("IT148764", "IT-", "-123", "IT-12a", "", "   ", "IT_148"):
        assert normalize_issue_key(value, "IT") is None, value


def test_normalize_issue_key_checks_project():
    assert normalize_issue_key("SD-100", "IT") is None
    assert normalize_issue_key("SD-100") == "SD-100"


def test_localpart_is_jira_login():
    assert localpart("@tyugaev.dmitrii:otr.ru") == "tyugaev.dmitrii"
    assert localpart("@it_support_bot:otr.ru") == "it_support_bot"
    assert localpart("") == ""


def test_is_valid_mxid():
    assert is_valid_mxid("@user:otr.ru")
    assert not is_valid_mxid("user:otr.ru")
    assert not is_valid_mxid("@user")
