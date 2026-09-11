# -*- coding: utf-8 -*-
"""Комната администраторов бота (п. 8 ТЗ).

Все служебные события бота (регистрации, обращения обратной связи, ошибки Jira
и Synapse) пересылаются в комнату ``administration.admin_room_id``. Каждый тип
события можно выключить параметром ``administration.notify_events.<событие>``.
Если написать в комнату не удалось — пишем ERROR в лог и продолжаем работу.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from src.matrix.client import MatrixClient

logger = logging.getLogger(__name__)


class AdminRoomService:
    """Отправка служебных уведомлений администраторам бота."""

    def __init__(self, config: Any, matrix: MatrixClient) -> None:
        self._config = config
        self._matrix = matrix
        self.room_id = str(config.get("administration.admin_room_id", "") or "")
        self._admins = {str(item) for item in (config.get("administration.admins") or [])}
        self._events: dict[str, Any] = dict(config.get("administration.notify_events") or {})

    # ------------------------------------------------------------------
    def is_admin(self, user_id: str) -> bool:
        """Проверить, что пользователь — администратор бота."""
        return user_id in self._admins

    @property
    def admins(self) -> Iterable[str]:
        return tuple(self._admins)

    def is_event_enabled(self, event_key: str) -> bool:
        """Включено ли логирование события в комнату администраторов."""
        return bool(self._events.get(event_key, True))

    async def notify(self, event_key: str, text: str) -> bool:
        """Отправить событие в комнату администраторов, если оно включено."""
        if not self.is_event_enabled(event_key):
            logger.debug("Событие '%s' отключено для комнаты администраторов", event_key)
            return False
        return await self.send(text)

    async def send(self, text: str) -> bool:
        """Отправить произвольный текст в комнату администраторов."""
        if not self.room_id:
            logger.warning(
                "administration.admin_room_id не задан — сообщение администраторам не отправлено"
            )
            return False
        try:
            event_id = await self._matrix.send_text(self.room_id, text, notice=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось написать в комнату администраторов %s: %s", self.room_id, exc)
            return False
        if not event_id:
            logger.error(
                "Не удалось написать в комнату администраторов %s — продолжаем работу",
                self.room_id,
            )
            return False
        return True

    async def send_file(self, path: str, caption: str = "") -> bool:
        """Отправить файл (логи, выгрузка пользователей) в комнату администраторов."""
        import os

        if not self.room_id:
            logger.warning("administration.admin_room_id не задан — файл не отправлен")
            return False
        mxc_uri, size = await self._matrix.upload_file(path)
        if not mxc_uri:
            logger.error("Не удалось загрузить файл %s в Synapse", path)
            return False
        event_id = await self._matrix.send_file(
            self.room_id, mxc_uri, os.path.basename(path), size, body=caption or None
        )
        return bool(event_id)
