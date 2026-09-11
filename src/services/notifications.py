# -*- coding: utf-8 -*-
"""Рассылка уведомлений об изменении статуса заявок (п. 4.2, 4.3 ТЗ).

Фоновая задача с периодом ``jira.poll_interval_seconds``:

1. получает события плагина Jira Status Listener;
2. определяет автора заявки (``jira_login``) и все его комнаты;
3. отправляет уведомление во все комнаты пользователя (п. 1.6);
4. помечает событие обработанным и удаляет его в плагине.

Сбой на любом шаге не роняет цикл: пишем ERROR в лог, при необходимости
сообщаем в комнату администраторов и продолжаем со следующей итерации.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.constants import ADMIN_EVENT_JIRA_ERROR, ADMIN_EVENT_PROFILE_NOT_FOUND
from src.db.repository import Repositories
from src.jira.status_listener import StatusListenerClient
from src.services.admin_room import AdminRoomService
from src.services.ui import UiService
from src.texts import t

logger = logging.getLogger(__name__)


class NotificationService:
    """Опрос Jira и доставка уведомлений пользователям."""

    def __init__(
        self,
        config: Any,
        repos: Repositories,
        listener: StatusListenerClient,
        ui: UiService,
        admin_room: AdminRoomService,
    ) -> None:
        self._config = config
        self._repos = repos
        self._listener = listener
        self._ui = ui
        self._admin_room = admin_room
        self.interval = int(config.get("jira.poll_interval_seconds", 60))

    # ------------------------------------------------------------------
    async def process_once(self) -> int:
        """Один цикл опроса. Возвращает количество отправленных уведомлений."""
        events: list[dict[str, Any]] = []
        listener_failed = False
        try:
            events = await self._listener.fetch_events()
        except Exception as exc:  # noqa: BLE001
            listener_failed = True
            logger.error("Не удалось получить события плагина Jira Status Listener: %s", exc)
            await self._admin_room.notify(ADMIN_EVENT_JIRA_ERROR, t("admin_jira_error", error=exc))

        normalized: list[dict[str, Any]] = []
        for event in events:
            try:
                item = await self._listener.normalize_event(event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка разбора события плагина %s: %s", event, exc)
                continue
            if item:
                normalized.append(item)
            elif event.get("id"):
                # Событие нас не интересует — просто снимаем его из очереди плагина
                await self._safe_delete(str(event.get("id")))

        if listener_failed:
            normalized.extend(await self._fallback())

        sent = 0
        for item in normalized:
            try:
                sent += await self._deliver(item)
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка отправки уведомления по заявке %s", item.get("issue_key"))
        return sent

    async def _fallback(self) -> list[dict[str, Any]]:
        """Резервный JQL-опрос по логинам зарегистрированных пользователей."""
        users = await self._repos.users.all_users()
        logins = sorted({str(user.get("jira_login") or "") for user in users})
        return await self._listener.fallback_poll(logins)

    async def _deliver(self, item: dict[str, Any]) -> int:
        """Доставить одно уведомление во все комнаты пользователя."""
        event_key = item["event_key"]
        if await self._repos.events.is_processed(event_key):
            logger.debug("Событие %s уже обработано", event_key)
            await self._safe_delete(item.get("event_id", ""))
            return 0

        jira_login = item.get("jira_login") or ""
        rooms = await self._repos.users.rooms_of_jira_login(jira_login) if jira_login else []
        if not rooms:
            logger.warning(
                "Не найден профиль в БД для Jira-логина '%s' (заявка %s)",
                jira_login,
                item.get("issue_key"),
            )
            await self._admin_room.notify(
                ADMIN_EVENT_PROFILE_NOT_FOUND,
                t(
                    "admin_profile_not_found",
                    jira_login=jira_login or "-",
                    issue_key=item.get("issue_key", "-"),
                ),
            )
            await self._repos.events.mark(event_key, item.get("issue_key", ""), item.get("status", ""))
            await self._safe_delete(item.get("event_id", ""))
            return 0

        text = "\n\n".join(
            [
                t(item["text_key"], issue_key=item["issue_key"], status=item.get("status", "")),
                t("notification_footer"),
            ]
        )
        delivered = 0
        for room in rooms:
            if await self._ui.send(room["room_id"], text):
                delivered += 1
            else:
                logger.error(
                    "Не удалось доставить уведомление по %s в комнату %s",
                    item["issue_key"],
                    room["room_id"],
                )

        await self._repos.events.mark(event_key, item.get("issue_key", ""), item.get("status", ""))
        await self._safe_delete(item.get("event_id", ""))
        logger.info(
            "Уведомление по заявке %s отправлено в %s комнат(ы)", item["issue_key"], delivered
        )
        return delivered

    async def _safe_delete(self, event_id: str) -> None:
        if not event_id:
            return
        try:
            await self._listener.delete_event(event_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось удалить событие %s в плагине Jira: %s", event_id, exc)

    # ------------------------------------------------------------------
    async def loop(self) -> None:
        """Бесконечный цикл опроса Jira."""
        logger.info("Запущен мониторинг заявок Jira: интервал %s c", self.interval)
        while True:
            try:
                await self.process_once()
                await self._repos.events.cleanup(days=30)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Сбой в мониторинге заявок — продолжаем работу")
            await asyncio.sleep(max(self.interval, 5))
