# -*- coding: utf-8 -*-
"""Тесты построения текстовых меню и разбора реакций."""
from __future__ import annotations

import conftest  # noqa: F401

from src.constants import ACTION_COMMENT, ACTION_CONFIRM, ACTION_REOPEN
from src.matrix import menu as menus


def test_main_menu_items_and_emojis():
    menu = menus.main_menu()
    text = menu.render(as_list=True)
    assert text.startswith("**Главное меню**")
    assert "- 📝 Создать заявку" in text
    assert len(menu.emojis) == len(menu.items)


def test_menu_items_as_list_switch():
    menu = menus.status_menu()
    assert "- 📋" in menu.render(as_list=True)
    assert "\n📋" in menu.render(as_list=False)


def test_title_is_bold_by_default():
    menu = menus.profile_menu()
    assert menu.render()[:2] == "**"
    assert not menu.render(title_bold=False).startswith("**")


def test_list_menu_pagination_and_payload():
    issues = [
        {"key": "IT-1", "summary": "Тема 1", "status": "В работе"},
        {"key": "IT-2", "summary": "Тема 2", "status": "Закрыто"},
    ]
    menu = menus.list_menu(issues, page=2, total_pages=10)
    text = menu.render()
    assert "страница 2 из 10" in text
    assert "1️⃣ IT-1 — Тема 1 (В работе)" in text
    actions = menu.action_map()
    assert actions["1️⃣"]["payload"]["issue_key"] == "IT-1"
    assert actions["◀️"]["payload"]["page"] == 1
    assert actions["▶️"]["payload"]["page"] == 3


def test_list_menu_without_pagination_has_no_arrows():
    menu = menus.list_menu([{"key": "IT-1", "summary": "s", "status": "В работе"}], 1, 1)
    assert "◀️" not in menu.emojis
    assert "▶️" not in menu.emojis


def test_issue_actions_menu_is_dynamic():
    menu = menus.issue_actions_menu("IT-5", [ACTION_REOPEN, ACTION_CONFIRM, ACTION_COMMENT])
    mapping = menu.action_map()
    assert mapping["🔁"]["action"] == ACTION_REOPEN
    assert mapping["✅"]["action"] == ACTION_CONFIRM
    assert mapping["🗨️"]["action"] == ACTION_COMMENT
    assert mapping["🔁"]["payload"]["issue_key"] == "IT-5"
    assert "📎" not in mapping


def test_confirm_menu_carries_action():
    menu = menus.confirm_menu("create_issue", {"subject": "тест"})
    mapping = menu.action_map()
    assert mapping["✅"]["payload"]["confirm_action"] == "create_issue"
    assert mapping["❌"]["action"] == "confirm_no"


def test_paginate():
    assert menus.paginate([1, 2, 3, 4, 5], 2, 10) == [[1, 2], [3, 4], [5]]
    assert menus.paginate([1, 2, 3, 4, 5], 2, 2) == [[1, 2], [3, 4]]
    assert menus.paginate([], 5, 10) == [[]]
