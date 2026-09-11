# -*- coding: utf-8 -*-
"""Текстовые утилиты: человекочитаемый TTL, обрезка и нарезка сообщений."""
from __future__ import annotations

MAX_MESSAGE_LENGTH = 30000  # практический лимит одного события m.room.message


def human_duration(seconds: int) -> str:
    """Перевести секунды в строку вида ``7 дн. 2 ч.``.

    >>> human_duration(604800)
    '7 дн.'
    >>> human_duration(3660)
    '1 ч. 1 мин.'
    """
    seconds = max(int(seconds), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes:
        parts.append(f"{minutes} мин.")
    if not parts:
        parts.append(f"{secs} сек.")
    return " ".join(parts)


def truncate(value: str, limit: int, suffix: str = "…") -> str:
    """Обрезать строку до ``limit`` символов."""
    if value is None:
        return ""
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(limit - len(suffix), 0)] + suffix


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Разбить длинный текст на части по границам строк.

    >>> split_message("abc", 10)
    ['abc']
    >>> split_message("aaa\\nbbb\\nccc", 8)
    ['aaa\\nbbb', 'ccc']
    """
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def clean_jira_markup(text: str) -> str:
    """Убрать из текста Jira служебные теги вида ``{color:#ff0000}``."""
    import re

    return re.sub(r"\{color(:\s*[^}]+)?\}", "", text or "")
