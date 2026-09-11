# -*- coding: utf-8 -*-
"""Тесты рендера разметки сообщений (markdown / html / plain)."""
from __future__ import annotations

import conftest  # noqa: F401

from src.matrix.formatting import MARKUP_HTML, MARKUP_PLAIN, markdown_to_html, render


def test_bold_and_code():
    body, formatted = render("**Главное меню** и `!start`")
    assert body == "Главное меню и !start"
    assert "<strong>Главное меню</strong>" in formatted
    assert "<code>!start</code>" in formatted


def test_list_rendering():
    formatted = markdown_to_html("**Меню**\n- 📝 Создать\n- ❓ Помощь")
    assert formatted.count("<li>") == 2
    assert "<ul>" in formatted and "</ul>" in formatted


def test_links():
    _body, formatted = render("[портал](https://jira/IT-1)")
    assert '<a href="https://jira/IT-1">портал</a>' in formatted


def test_html_escape():
    _body, formatted = render("Тема <script>alert(1)</script>")
    assert "&lt;script&gt;" in formatted


def test_plain_markup_has_no_html():
    body, formatted = render("**Жирный**", MARKUP_PLAIN)
    assert formatted is None
    assert body == "Жирный"


def test_html_markup_passthrough():
    body, formatted = render("<b>Меню</b>", MARKUP_HTML)
    assert formatted == "<b>Меню</b>"
    assert body == "Меню"
