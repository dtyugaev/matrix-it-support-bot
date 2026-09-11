# -*- coding: utf-8 -*-
"""Рендер разметки сообщений бота.

В Matrix сообщение отправляется двумя полями: ``body`` (plain text) и
``formatted_body`` (HTML). Бот описывает все тексты в Markdown, а этот модуль
конвертирует их в HTML. Поведение управляется параметром ``menu.markup``:

* ``markdown`` — Markdown конвертируется в HTML (по умолчанию);
* ``html`` — тексты уже содержат HTML и передаются как есть;
* ``plain`` — HTML не отправляется, сообщение уходит только как plain text.

Реализован намеренно минимальный набор конструкций (жирный, курсив, код,
ссылки, списки, переводы строк) — этого достаточно для меню и карточек заявок
и не тянет внешнюю зависимость.
"""
from __future__ import annotations

import html
import re

MARKUP_MARKDOWN = "markdown"
MARKUP_HTML = "html"
MARKUP_PLAIN = "plain"

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_AUTOLINK_RE = re.compile(r"(?<![\"'>=])(https?://[^\s<>\)]+)")


def markdown_to_html(text: str) -> str:
    """Конвертировать поддерживаемый Markdown в HTML."""
    if not text:
        return ""

    lines = text.split("\n")
    html_lines: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")) and len(stripped) > 2:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        html_lines.append(_inline(line))

    if in_list:
        html_lines.append("</ul>")

    result: list[str] = []
    for index, line in enumerate(html_lines):
        result.append(line)
        next_line = html_lines[index + 1] if index + 1 < len(html_lines) else ""
        if line.startswith(("<ul>", "<li>", "</ul>")) or next_line.startswith("<ul>"):
            continue
        if index < len(html_lines) - 1:
            result.append("<br/>")
    return "".join(result)


def _inline(text: str) -> str:
    """Обработать инлайн-конструкции одной строки."""
    escaped = html.escape(text, quote=False)
    escaped = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    escaped = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = _AUTOLINK_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    return escaped


def render(text: str, markup: str = MARKUP_MARKDOWN) -> tuple[str, str | None]:
    """Вернуть пару ``(body, formatted_body)`` для события ``m.room.message``."""
    markup = (markup or MARKUP_MARKDOWN).lower()
    if markup == MARKUP_PLAIN:
        return strip_markdown(text), None
    if markup == MARKUP_HTML:
        return strip_html(text), text
    return strip_markdown(text), markdown_to_html(text)


def strip_markdown(text: str) -> str:
    """Убрать Markdown-разметку для поля ``body``."""
    if not text:
        return ""
    plain = _BOLD_RE.sub(r"\1", text)
    plain = _ITALIC_RE.sub(r"\1", plain)
    plain = _CODE_RE.sub(r"\1", plain)
    plain = _LINK_RE.sub(r"\1 (\2)", plain)
    return plain


def strip_html(text: str) -> str:
    """Грубо убрать HTML-теги для поля ``body``."""
    if not text:
        return ""
    without_tags = re.sub(r"<br\s*/?>", "\n", text)
    without_tags = re.sub(r"</(p|li|ul|div)>", "\n", without_tags)
    without_tags = re.sub(r"<[^>]+>", "", without_tags)
    return html.unescape(without_tags).strip()
