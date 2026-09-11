# -*- coding: utf-8 -*-
"""Валидация пользовательского ввода (без внешних зависимостей)."""
from __future__ import annotations

import re

#: Ключ заявки: префикс проекта (латиница/цифры) + дефис + цифры.
ISSUE_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$")


def normalize_issue_key(raw: str, project: str | None = None) -> str | None:
    """Проверить и нормализовать номер заявки.

    Возвращает ключ в верхнем регистре (``it-123`` -> ``IT-123``) либо ``None``,
    если формат некорректен или проект не совпадает с ожидаемым.

    >>> normalize_issue_key(" it-148764 ", "IT")
    'IT-148764'
    >>> normalize_issue_key("IT148764", "IT") is None
    True
    """
    if not raw:
        return None
    candidate = raw.strip().replace("\u00a0", " ").split()[0] if raw.strip() else ""
    match = ISSUE_KEY_RE.match(candidate)
    if not match:
        return None
    prefix, number = match.group(1).upper(), match.group(2)
    if project and prefix != project.upper():
        return None
    return f"{prefix}-{number}"


def is_valid_mxid(value: str) -> bool:
    """Проверить формат Matrix ID (``@localpart:server``)."""
    return bool(re.match(r"^@[^:\s]+:[^:\s]+$", value or ""))


def localpart(mxid: str) -> str:
    """Localpart Matrix ID — он же логин пользователя в Jira (п. 4.2 ТЗ).

    >>> localpart("@tyugaev.dmitrii:otr.ru")
    'tyugaev.dmitrii'
    """
    if not mxid:
        return ""
    return mxid.lstrip("@").split(":", 1)[0]
