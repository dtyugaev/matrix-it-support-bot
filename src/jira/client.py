# -*- coding: utf-8 -*-
"""Асинхронный клиент Jira REST API v2 на aiohttp.

Эталонный бот использовал синхронную библиотеку ``jira`` и выносил вызовы в
ThreadPoolExecutor. Здесь вся работа с Jira асинхронная: один ``aiohttp``
сессия, таймауты, повторные попытки с экспоненциальным backoff и никаких
секретов в логах.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from src.jira import transitions as tr
from src.utils.dates import format_jira_datetime
from src.utils.text import clean_jira_markup

logger = logging.getLogger(__name__)

#: Результаты смены статуса (совместимо с кодами эталонного бота).
SWITCH_OK = 0
SWITCH_WRONG_STATUS = 255
SWITCH_ATTACH_ERROR = -1


class JiraError(Exception):
    """Ошибка обращения к Jira (сетевая или логическая)."""


class JiraAuthError(JiraError):
    """Не удалось авторизоваться в Jira."""


class JiraClient:
    """Клиент Jira: заявки, комментарии, вложения, переходы статусов."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self.base_url = str(config.get("jira.url", "")).rstrip("/")
        self.project = str(config.get("jira.project", "IT"))
        self.issue_type_id = str(config.get("jira.issue_type_id", "10000"))
        self._login = str(config.get("jira.login", ""))
        self._password = str(config.get("jira.password", ""))
        self._verify_ssl = bool(config.get("jira.verify_ssl", False))
        self._timeout = int(config.get("jira.request_timeout", 30))
        self._max_retries = int(config.get("jira.max_retries", 4))
        self._backoff = float(config.get("jira.retry_backoff", 2.0))
        self._comments_per_issue = int(config.get("jira.comments_per_issue", 3))
        self._session: Any = None
        self.available = False

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Создать HTTP-сессию и проверить авторизацию."""
        import aiohttp

        connector = aiohttp.TCPConnector(ssl=self._verify_ssl if self._verify_ssl else False)
        self._session = aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(self._login, self._password),
            timeout=aiohttp.ClientTimeout(total=self._timeout),
            connector=connector,
            headers={"Accept": "application/json", "X-Atlassian-Token": "no-check"},
        )
        await self.check_auth()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def check_auth(self) -> bool:
        """Проверить логин/пароль Jira. При ошибке пишет ERROR в лог."""
        try:
            data = await self._request("GET", "/rest/api/2/myself")
            self.available = True
            logger.info(
                "Jira %s: авторизация выполнена под пользователем %s",
                self.base_url,
                (data or {}).get("name"),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.available = False
            logger.error("Авторизация в Jira (%s) не выполнена: %s", self.base_url, exc)
            return False

    # ------------------------------------------------------------------
    # Низкоуровневый запрос
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
        raw: bool = False,
        absolute_url: str | None = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Выполнить запрос с повторными попытками и экспоненциальным backoff."""
        if self._session is None:
            raise JiraError("HTTP-сессия Jira не инициализирована")

        url = absolute_url or f"{self.base_url}{path}"
        delay = 1.0
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session.request(
                    method, url, json=json_body, params=params, data=data, headers=headers
                ) as response:
                    if response.status in (401, 403):
                        self.available = False
                        text = await response.text()
                        raise JiraAuthError(
                            f"Jira отклонила запрос ({response.status}): {text[:200]}"
                        )
                    if response.status >= 500:
                        raise JiraError(f"Jira вернула {response.status} для {method} {path}")
                    if response.status == 404:
                        raise JiraError(f"Объект не найден в Jira: {method} {path}")
                    if response.status >= 400:
                        text = await response.text()
                        raise JiraError(
                            f"Ошибка Jira {response.status} для {method} {path}: {text[:300]}"
                        )
                    self.available = True
                    if raw:
                        return await response.read()
                    if not expect_json or response.status == 204:
                        return None
                    return await response.json(content_type=None)
            except JiraAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - сетевые ошибки повторяем
                last_error = exc
                logger.warning(
                    "Запрос к Jira %s %s не удался (попытка %s/%s): %s",
                    method,
                    path,
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(delay)
                    delay *= self._backoff
        self.available = False
        raise JiraError(f"Jira недоступна: {last_error}")

    # ------------------------------------------------------------------
    # Пользователи
    # ------------------------------------------------------------------
    async def get_user(self, jira_login: str) -> dict[str, Any] | None:
        """Информация о пользователе Jira по логину (jira_login = localpart MXID)."""
        try:
            return await self._request(
                "GET", "/rest/api/2/user", params={"username": jira_login}
            )
        except JiraError as exc:
            logger.warning("Не удалось получить пользователя Jira '%s': %s", jira_login, exc)
            return None

    async def get_user_email(self, jira_login: str) -> str | None:
        """Почта пользователя из Jira. При неудаче — WARNING и ``None`` (п. 3 ТЗ)."""
        user = await self.get_user(jira_login)
        email = (user or {}).get("emailAddress") or None
        if not email:
            logger.warning(
                "Не удалось получить email из Jira для логина '%s' — работаем с пустой почтой",
                jira_login,
            )
        return email

    # ------------------------------------------------------------------
    # Заявки
    # ------------------------------------------------------------------
    async def create_issue(
        self, summary: str, description: str, reporter_login: str
    ) -> str:
        """Создать заявку от имени пользователя (reporter = его логин Jira)."""
        payload = {
            "fields": {
                "project": {"key": self.project},
                "issuetype": {"id": self.issue_type_id},
                "summary": summary,
                "description": description,
                "reporter": {"name": reporter_login},
            }
        }
        data = await self._request("POST", "/rest/api/2/issue", json_body=payload)
        key = (data or {}).get("key")
        if not key:
            raise JiraError("Jira не вернула номер созданной заявки")
        logger.info("Создана заявка %s (reporter=%s)", key, reporter_login)
        return str(key)

    async def add_attachment(self, issue_key: str, file_path: str, filename: str) -> bool:
        """Приложить файл к заявке (multipart/form-data)."""
        import aiofiles
        import aiohttp

        async with aiofiles.open(file_path, "rb") as handle:
            content = await handle.read()
        form = aiohttp.FormData()
        form.add_field("file", content, filename=filename, content_type="application/octet-stream")
        await self._request(
            "POST",
            f"/rest/api/2/issue/{issue_key}/attachments",
            data=form,
            headers={"X-Atlassian-Token": "no-check"},
        )
        logger.info("К заявке %s приложен файл %s", issue_key, filename)
        return True

    async def search(
        self,
        jql: str,
        fields: Sequence[str] = ("summary", "status", "created", "updated"),
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Поиск заявок по JQL."""
        logger.debug("JQL: %s", jql)
        data = await self._request(
            "GET",
            "/rest/api/2/search",
            params={
                "jql": jql,
                "fields": ",".join(fields),
                "maxResults": max_results,
                "startAt": 0,
            },
        )
        return list((data or {}).get("issues", []))

    async def get_user_issues(
        self, jira_login: str, status_ids: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Список заявок пользователя, отсортированный как в эталонном боте.

        Порядок: «Запрос информации», затем «Подтверждение решения»/«Предложено
        решение», «Открыта», рабочие статусы, прочие; внутри группы — новые сверху.
        """
        jql = f'project = "{self.project}" AND reporter = "{jira_login}"'
        if status_ids:
            ids = ", ".join(f'"{status_id}"' for status_id in status_ids)
            jql += f" AND status in ({ids})"
        jql += " ORDER BY createdDate DESC"

        issues = await self.search(jql, max_results=200)
        normalized = [
            {
                "key": issue.get("key", ""),
                "summary": (issue.get("fields") or {}).get("summary", ""),
                "status": ((issue.get("fields") or {}).get("status") or {}).get("name", ""),
                "created": (issue.get("fields") or {}).get("created", ""),
                "updated": (issue.get("fields") or {}).get("updated", ""),
            }
            for issue in issues
        ]
        return sort_issues(normalized)

    async def get_issue(self, issue_key: str) -> dict[str, Any] | None:
        """Карточка заявки: поля, вложения и последние комментарии."""
        issue_key = issue_key.upper()
        try:
            data = await self._request(
                "GET",
                f"/rest/api/2/issue/{issue_key}",
                params={"expand": "renderedFields"},
            )
        except JiraAuthError:
            raise
        except JiraError as exc:
            logger.info("Не удалось получить заявку %s: %s", issue_key, exc)
            return None
        if not data:
            return None

        fields = data.get("fields") or {}
        reporter = fields.get("reporter") or {}
        attachments = [
            {
                "id": item.get("id"),
                "filename": item.get("filename", "file"),
                "size": int(item.get("size") or 0),
                "content": item.get("content", ""),
            }
            for item in (fields.get("attachment") or [])
        ]
        comments = self._public_comments(((fields.get("comment") or {}).get("comments") or []))

        return {
            "key": data.get("key", issue_key),
            "summary": fields.get("summary") or "",
            "description": clean_jira_markup(fields.get("description") or ""),
            "status": ((fields.get("status") or {}).get("name") or ""),
            "created": format_jira_datetime(fields.get("created") or ""),
            "updated": format_jira_datetime(fields.get("updated") or ""),
            "reporter_login": reporter.get("name") or "",
            "reporter_email": reporter.get("emailAddress") or "",
            "attachments": attachments,
            "attachments_count": len(attachments),
            "comments": comments,
        }

    def _public_comments(self, raw_comments: list[dict[str, Any]]) -> list[str]:
        """Оставить только публичные комментарии и вернуть последние N.

        Комментарии с ограничением видимости (``visibility``) пользователю не
        показываются — логика перенесена из эталонного бота.
        """
        public: list[str] = []
        for comment in raw_comments:
            if comment.get("visibility"):
                continue
            created = format_jira_datetime(comment.get("created") or "", short=True)
            body = clean_jira_markup(comment.get("body") or "")
            public.append(f"{created}\n{body}")
        if self._comments_per_issue <= 0:
            return []
        return public[-self._comments_per_issue :]

    async def add_comment(self, issue_key: str, body: str) -> bool:
        """Добавить комментарий к заявке."""
        data = await self._request(
            "POST", f"/rest/api/2/issue/{issue_key}/comment", json_body={"body": body}
        )
        if not data:
            raise JiraError(f"Пустой ответ Jira при добавлении комментария к {issue_key}")
        logger.info("В заявку %s добавлен комментарий", issue_key)
        return True

    async def get_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/rest/api/2/issue/{issue_key}/transitions")
        return list((data or {}).get("transitions", []))

    async def switch_status(
        self, action: str, issue_key: str, comment: str, attach_path: str | None = None,
        attach_name: str | None = None,
    ) -> int:
        """Выполнить переход заявки в новый статус.

        :param action: имя перехода Jira (см. :data:`src.jira.transitions.TRANSITIONS`).
        :returns: ``0`` — успех, ``255`` — текущий статус не допускает перехода,
            ``-1`` — переход выполнен, но не удалось приложить файл.
        """
        if action not in tr.TRANSITIONS:
            raise JiraError(f"Неизвестный переход Jira: {action}")

        issue = await self.get_issue(issue_key)
        if not issue:
            raise JiraError(f"Заявка {issue_key} недоступна")

        current_status = issue["status"]
        if current_status not in tr.allowed_from_statuses(action):
            logger.warning(
                "%s: текущий статус '%s', для перехода '%s' требуется один из %s",
                issue_key,
                current_status,
                action,
                tr.allowed_from_statuses(action),
            )
            return SWITCH_WRONG_STATUS

        available = await self.get_transitions(issue_key)
        transition_id = next(
            (item.get("id") for item in available if item.get("name") == action), None
        )
        if not transition_id:
            raise JiraError(
                f"Для заявки {issue_key} (статус '{current_status}') недоступен переход '{action}'"
            )

        payload: dict[str, Any] = {"transition": {"id": str(transition_id)}}
        if comment:
            payload["update"] = {"comment": [{"add": {"body": comment}}]}
        await self._request(
            "POST",
            f"/rest/api/2/issue/{issue_key}/transitions",
            json_body=payload,
            expect_json=False,
        )

        expected = tr.target_status(action)
        updated = await self.get_issue(issue_key)
        if updated and updated["status"] != expected:
            logger.warning(
                "%s: после перехода '%s' статус '%s', ожидался '%s'",
                issue_key,
                action,
                updated["status"],
                expected,
            )
        logger.info("Заявка %s переведена переходом '%s'", issue_key, action)

        if attach_path:
            try:
                await self.add_attachment(issue_key, attach_path, attach_name or "attachment")
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось приложить файл к %s: %s", issue_key, exc)
                return SWITCH_ATTACH_ERROR
        return SWITCH_OK

    async def download_attachment(self, url: str) -> bytes:
        """Скачать вложение заявки по ссылке ``content``."""
        return await self._request("GET", "", absolute_url=url, raw=True)

    async def request_absolute(self, method: str, url: str, **kwargs: Any) -> Any:
        """Публичный доступ к HTTP-слою по абсолютному URL.

        Используется плагином Jira Status Listener, который живёт на том же
        хосте Jira и требует той же basic-авторизации.
        """
        return await self._request(method, "", absolute_url=url, **kwargs)


#: Приоритет статусов при выводе списка заявок (меньше — выше в списке).
STATUS_ORDER: dict[str, int] = {
    tr.STATUS_INFORMATION_REQUEST: 0,
    tr.STATUS_SOLUTION_CONFIRMATION: 1,
    tr.STATUS_SOLUTION_PROPOSED: 1,
    tr.STATUS_OPENED: 2,
    tr.STATUS_REGISTERED: 2,
    tr.STATUS_IN_WORK: 3,
    tr.STATUS_ANSWER_PROVIDED: 4,
    tr.STATUS_REOPENED: 4,
    tr.STATUS_WAIT_SOLUTION: 5,
    tr.STATUS_POSTPONED: 6,
    tr.STATUS_CLOSED: 9,
}


def sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отсортировать заявки по группе статуса, затем по дате создания (новые сверху).

    Логика перенесена из ``core/core_jira/api.py::get_all_user_issues``
    эталонного проекта.
    """

    def sort_key(issue: dict[str, Any]) -> tuple[int, str]:
        status = issue.get("status", "")
        order = STATUS_ORDER.get(status, 7)
        if order == 7 and tr.STATUS_IN_WORK in status:
            order = 3
        # createdDate в ISO-формате сортируется лексикографически
        return (order, _invert(issue.get("created", "")))

    return sorted(issues, key=sort_key)


def _invert(value: str) -> str:
    """Инвертировать строку для сортировки по убыванию внутри группы."""
    return "".join(chr(0x10FFFF - ord(char)) if ord(char) < 0x10FFFF else char for char in value)
