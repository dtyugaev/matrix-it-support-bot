# -*- coding: utf-8 -*-
"""Приём уведомлений об изменении статуса заявок из плагина Jira Status Listener.

Основной механизм (как в эталонном проекте): бот с периодичностью
``jira.poll_interval_seconds`` опрашивает REST-эндпоинт плагина
(``jira.status_listener_url``), получает список событий, рассылает уведомления
и удаляет обработанные события в плагине (DELETE ``<url>/<id>``).

Если плагин недоступен, включается резервный механизм (``jira.fallback_poll_enabled``):
JQL-опрос заявок, обновившихся за последние ``jira.poll_lookback_minutes`` минут.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from src.jira import transitions as tr
from src.jira.client import JiraClient, JiraError
from src.utils.dates import jql_since

logger = logging.getLogger(__name__)

#: Статусы, о которых бот уведомляет пользователя (как в эталонном боте).
NOTIFIABLE_STATUSES: dict[str, str] = {
    tr.STATUS_INFORMATION_REQUEST: "notification_information_request",
    tr.STATUS_SOLUTION_CONFIRMATION: "notification_solution_proposed",
    tr.STATUS_SOLUTION_PROPOSED: "notification_solution_proposed",
}


class StatusListenerClient:
    """Обёртка над REST-эндпоинтом плагина Jira Status Listener."""

    def __init__(self, config: Any, jira: JiraClient) -> None:
        self._config = config
        self._jira = jira
        self.url = str(config.get("jira.status_listener_url", "")).rstrip("/")
        self.delete_processed = bool(config.get("jira.delete_processed_events", True))
        self.fallback_enabled = bool(config.get("jira.fallback_poll_enabled", True))
        self.notify_all = bool(config.get("jira.notify_all_status_changes", False))
        self.available = bool(self.url)

    # ------------------------------------------------------------------
    # Плагин Status Listener
    # ------------------------------------------------------------------
    async def fetch_events(self) -> list[dict[str, Any]]:
        """Получить накопленные события плагина."""
        if not self.url:
            return []
        data = await self._jira.request_absolute("GET", self.url)
        if not data:
            return []
        if isinstance(data, dict):  # некоторые версии плагина отдают объект
            data = data.get("events") or data.get("items") or []
        logger.debug("Плагин Status Listener вернул %s событий", len(data))
        return list(data)

    async def delete_event(self, event_id: str) -> None:
        """Удалить обработанное событие в плагине."""
        if not (self.url and self.delete_processed and event_id):
            return
        await self._jira.request_absolute(
            "DELETE", f"{self.url}/{event_id}", expect_json=False
        )
        logger.debug("Удалено событие плагина %s", event_id)

    # ------------------------------------------------------------------
    # Нормализация событий
    # ------------------------------------------------------------------
    async def normalize_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Привести событие плагина к внутреннему виду.

        Возвращает словарь ``{event_id, event_key, issue_key, status, text_key,
        jira_login}`` либо ``None``, если о таком статусе уведомлять не нужно.
        """
        issue_key = str(event.get("issueKey") or event.get("issue_key") or "").upper()
        if not issue_key:
            logger.warning("Событие плагина без номера заявки: %s", event)
            return None

        status = str(event.get("statusName") or "")
        if not status:
            status_id = str(event.get("statusId") or event.get("status_id") or "")
            status = tr.STATUS_BY_ID.get(status_id, "")
        if not status:
            logger.warning("Не удалось определить статус события для %s: %s", issue_key, event)
            return None

        text_key = NOTIFIABLE_STATUSES.get(status)
        if text_key is None:
            if not self.notify_all:
                return None
            text_key = "notification_status_changed"

        jira_login = _extract_login(event)
        if not jira_login:
            # Плагин не всегда отдаёт логин автора — берём его из самой заявки,
            # т.к. уникальный идентификатор пользователя в Jira — jira_login (п. 4.2).
            issue = await self._jira.get_issue(issue_key)
            jira_login = (issue or {}).get("reporter_login", "")

        return {
            "event_id": str(event.get("id") or ""),
            "event_key": f"{issue_key}:{status}:{event.get('id') or event.get('updated') or ''}",
            "issue_key": issue_key,
            "status": status,
            "text_key": text_key,
            "jira_login": jira_login,
        }

    # ------------------------------------------------------------------
    # Резервный опрос через JQL
    # ------------------------------------------------------------------
    async def fallback_poll(self, logins: Iterable[str]) -> list[dict[str, Any]]:
        """Резервный сбор изменений статусов через JQL (если плагин недоступен)."""
        logins = [login for login in logins if login]
        if not (self.fallback_enabled and logins):
            return []

        lookback = jql_since(int(self._config.get("jira.poll_lookback_minutes", 15)))
        status_ids = [
            tr.ID_BY_STATUS[status] for status in NOTIFIABLE_STATUSES if status in tr.ID_BY_STATUS
        ]
        reporters = ", ".join(f'"{login}"' for login in logins)
        statuses = ", ".join(f'"{status_id}"' for status_id in status_ids)
        jql = (
            f'project = "{self._jira.project}" AND reporter in ({reporters}) '
            f"AND status in ({statuses}) AND updated >= {lookback} ORDER BY updated DESC"
        )
        try:
            issues = await self._jira.search(
                jql, fields=("summary", "status", "updated", "reporter"), max_results=100
            )
        except JiraError as exc:
            logger.error("Резервный JQL-опрос Jira не выполнен: %s", exc)
            return []

        events: list[dict[str, Any]] = []
        for issue in issues:
            fields = issue.get("fields") or {}
            status = ((fields.get("status") or {}).get("name") or "")
            text_key = NOTIFIABLE_STATUSES.get(status)
            if not text_key:
                continue
            events.append(
                {
                    "event_id": "",
                    "event_key": f"{issue.get('key')}:{status}:{fields.get('updated')}",
                    "issue_key": str(issue.get("key", "")).upper(),
                    "status": status,
                    "text_key": text_key,
                    "jira_login": ((fields.get("reporter") or {}).get("name") or ""),
                }
            )
        logger.info("Резервный опрос Jira: найдено %s изменений статусов", len(events))
        return events


def _extract_login(event: dict[str, Any]) -> str:
    """Достать логин автора заявки из события плагина (разные версии — разные поля)."""
    # Поле userName в событии плагина означает автора изменения (инженера),
    # поэтому оно намеренно не используется для адресации уведомления.
    for key in ("reporterName", "reporterLogin", "reporter"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and value.get("name"):
            return str(value["name"])
    return ""
