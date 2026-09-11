# -*- coding: utf-8 -*-
"""Разбор и форматирование дат Jira (только стандартная библиотека)."""
from __future__ import annotations

import datetime as dt

JIRA_INPUT_FORMAT = "%Y-%m-%dT%H:%M:%S"
HUMAN_FORMAT = "%d.%m.%Y %H:%M:%S"
HUMAN_SHORT_FORMAT = "%d.%m.%Y %H:%M"


def parse_jira_datetime(value: str) -> dt.datetime | None:
    """Разобрать дату Jira вида ``2026-07-10T15:11:51.000+0300``.

    Дробная часть и смещение таймзоны отбрасываются: Jira отдаёт время в
    таймзоне сервера, а бот показывает его пользователю как есть — так же,
    как эталонный Telegram-бот.

    >>> parse_jira_datetime("2026-07-10T15:11:51.000+0300").hour
    15
    >>> parse_jira_datetime("") is None
    True
    """
    if not value:
        return None
    trimmed = value.split(".")[0].split("+")[0].strip()
    try:
        return dt.datetime.strptime(trimmed, JIRA_INPUT_FORMAT)
    except ValueError:
        return None


def format_jira_datetime(value: str, short: bool = False) -> str:
    """Отформатировать дату Jira для вывода пользователю.

    >>> format_jira_datetime("2026-07-10T15:11:51.000+0300")
    '10.07.2026 15:11:51'
    >>> format_jira_datetime("bad-date")
    'bad-date'
    """
    parsed = parse_jira_datetime(value)
    if parsed is None:
        return value or ""
    return parsed.strftime(HUMAN_SHORT_FORMAT if short else HUMAN_FORMAT)


def jql_since(minutes: int) -> str:
    """Строка периода для JQL: ``-15m``."""
    return f"-{max(int(minutes), 1)}m"
