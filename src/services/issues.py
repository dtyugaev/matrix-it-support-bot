# -*- coding: utf-8 -*-
"""Бизнес-логика работы с заявками Jira: создание, список, поиск, действия.

Логика перенесена из эталонного Telegram-бота (модули ``create_issue``,
``list_issues``, ``find_issue``, ``processing_issue``) и адаптирована под
текстовые меню Matrix.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from src.constants import (
    ACTION_COMMENT,
    ACTION_CONFIRM,
    ACTION_GIVE_INFO,
    ACTION_REOPEN,
    ADMIN_EVENT_ISSUE_CREATED,
    ADMIN_EVENT_JIRA_ERROR,
    AUDIT_ACTIONS,
)
from src.db.repository import Repositories, Row
from src.jira import transitions as tr
from src.jira.client import SWITCH_OK, SWITCH_WRONG_STATUS, JiraClient, JiraError
from src.matrix import menu as menus
from src.services.admin_room import AdminRoomService
from src.services.attachments import AttachmentService
from src.texts import t
from src.utils.text import truncate

logger = logging.getLogger(__name__)


class IssueService:
    """Операции с заявками от имени зарегистрированного пользователя."""

    def __init__(
        self,
        config: Any,
        repos: Repositories,
        jira: JiraClient,
        attachments: AttachmentService,
        admin_room: AdminRoomService,
    ) -> None:
        self._config = config
        self._repos = repos
        self._jira = jira
        self._attachments = attachments
        self._admin_room = admin_room
        self.project = str(config.get("jira.project", "IT"))
        self.per_page = int(config.get("jira.issues_per_page", 5))
        self.max_pages = int(config.get("jira.issues_max_pages", 10))
        self.always_allow_comment = bool(config.get("jira.always_allow_comment", True))
        self.subject_max_length = int(config.get("jira.subject_max_length", 255))

    # ------------------------------------------------------------------
    # Доступ к заявке
    # ------------------------------------------------------------------
    def has_access(self, issue: dict[str, Any], profile: Row) -> bool:
        """Доступ есть у автора заявки и у администраторов бота.

        Уникальный идентификатор пользователя в Jira — ``jira_login`` (п. 4.2),
        поэтому сравнение идёт по логину, а не по почте (почта не уникальна).
        """
        if self._admin_room.is_admin(profile.get("user_id", "")):
            return True
        reporter = (issue.get("reporter_login") or "").strip().lower()
        return bool(reporter) and reporter == (profile.get("jira_login") or "").strip().lower()

    async def open_issue(self, profile: Row, issue_key: str) -> dict[str, Any] | None:
        """Получить заявку и проверить доступ пользователя к ней."""
        issue = await self._jira.get_issue(issue_key)
        if not issue:
            return None
        if not self.has_access(issue, profile):
            logger.warning(
                "Пользователь %s (jira_login=%s) запросил заявку %s автора %s — доступ запрещён",
                profile.get("user_id"),
                profile.get("jira_login"),
                issue_key,
                issue.get("reporter_login"),
            )
            return None
        await self._repos.audit.add(
            profile.get("room_id"), profile.get("user_id"), AUDIT_ACTIONS["open_issue"], issue_key
        )
        return issue

    # ------------------------------------------------------------------
    # Отображение
    # ------------------------------------------------------------------
    def issue_card_text(self, issue: dict[str, Any]) -> str:
        """Карточка заявки: поля, описание, последние комментарии, ссылка на портал."""
        parts = [
            f"**{t('menu_title_issue', issue_key=issue['key'])}**",
            "",
            t(
                "issue_card",
                issue_key=issue["key"],
                summary=issue.get("summary") or "",
                created=issue.get("created") or "",
                updated=issue.get("updated") or "",
                status=issue.get("status") or "",
                attachments_count=issue.get("attachments_count", 0),
            ),
            "",
            t(
                "issue_description_block",
                description=truncate(issue.get("description") or t("create_description_empty"), 8000),
            ),
        ]
        comments = issue.get("comments") or []
        parts.extend(
            [
                "",
                t(
                    "issue_comments_block",
                    comments="\n\n".join(comments) if comments else t("issue_comments_empty"),
                ),
            ]
        )
        if bool(self._config.get("menu.show_portal_link", True)):
            portal = str(self._config.get("jira.portal_url", "")).rstrip("/")
            if portal:
                parts.extend(["", t("issue_portal_link", url=f"{portal}/{issue['key']}")])
        return "\n".join(parts)

    def actions_for(self, issue: dict[str, Any]) -> list[str]:
        """Доступные действия по заявке (динамическое подменю, п. 5.2)."""
        return tr.available_actions(
            issue.get("status") or "",
            int(issue.get("attachments_count") or 0),
            self.always_allow_comment,
        )

    def actions_menu(self, issue: dict[str, Any]) -> menus.Menu:
        return menus.issue_actions_menu(issue["key"], self.actions_for(issue))

    # ------------------------------------------------------------------
    # Список заявок
    # ------------------------------------------------------------------
    async def list_pages(self, profile: Row) -> list[list[dict[str, Any]]]:
        """Список заявок пользователя, разбитый на страницы."""
        status_ids = [str(item) for item in (self._config.get("jira.issue_types_for_list") or [])]
        issues = await self._jira.get_user_issues(profile.get("jira_login", ""), status_ids)
        await self._repos.audit.add(
            profile.get("room_id"),
            profile.get("user_id"),
            AUDIT_ACTIONS["list_issues"],
            f"найдено={len(issues)}",
        )
        return menus.paginate(issues, self.per_page, self.max_pages)

    # ------------------------------------------------------------------
    # Создание заявки
    # ------------------------------------------------------------------
    async def create(
        self,
        profile: Row,
        subject: str,
        description: str,
        attach_path: str | None = None,
        attach_name: str | None = None,
    ) -> str | None:
        """Создать заявку в Jira и приложить файл, если он есть."""
        subject = " ".join(subject.split("\n")).strip()
        try:
            issue_key = await self._jira.create_issue(
                summary=truncate(subject, self.subject_max_length, suffix=""),
                description=description or t("create_description_empty"),
                reporter_login=profile.get("jira_login", ""),
            )
        except JiraError as exc:
            logger.error("Не удалось создать заявку для %s: %s", profile.get("user_id"), exc)
            await self._admin_room.notify(ADMIN_EVENT_JIRA_ERROR, t("admin_jira_error", error=exc))
            return None

        if attach_path:
            try:
                await self._jira.add_attachment(issue_key, attach_path, attach_name or "attachment")
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось приложить файл к %s: %s", issue_key, exc)
            finally:
                self._attachments.remove_local(attach_path)

        await self._repos.audit.add(
            profile.get("room_id"), profile.get("user_id"), AUDIT_ACTIONS["create_issue"], issue_key
        )
        await self._admin_room.notify(
            ADMIN_EVENT_ISSUE_CREATED,
            t("admin_issue_created", user_id=profile.get("user_id"), issue_key=issue_key),
        )
        return issue_key

    # ------------------------------------------------------------------
    # Действия по заявке
    # ------------------------------------------------------------------
    def comment_body(self, profile: Row, text: str | None, has_attachment: bool) -> str:
        """Сформировать текст комментария для Jira от имени заявителя."""
        displayname = profile.get("displayname") or profile.get("user_id") or ""
        if text:
            return t("jira_comment_prefix", displayname=displayname, text=text)
        if has_attachment:
            return t("jira_comment_attach_only", displayname=displayname)
        return t("jira_comment_prefix", displayname=displayname, text="")

    async def perform_action(
        self,
        profile: Row,
        issue_key: str,
        action: str,
        text: str | None = None,
        attach_path: str | None = None,
        attach_name: str | None = None,
    ) -> tuple[bool, str]:
        """Выполнить действие по заявке с проверкой доступности по статусу.

        :returns: ``(успех, ключ_фразы_для_пользователя)``.
        """
        issue = await self._jira.get_issue(issue_key)
        if not issue:
            return False, "find_no_access"
        if not self.has_access(issue, profile):
            return False, "find_no_access"

        status = issue.get("status") or ""
        if not tr.is_action_available(
            action, status, int(issue.get("attachments_count") or 0), self.always_allow_comment
        ):
            logger.info(
                "Действие '%s' недоступно для %s в статусе '%s'", action, issue_key, status
            )
            return False, "action_not_available"

        comment = self.comment_body(profile, text, bool(attach_path))

        try:
            if action == ACTION_COMMENT:
                await self._jira.add_comment(issue_key, comment)
                if attach_path:
                    await self._jira.add_attachment(
                        issue_key, attach_path, attach_name or "attachment"
                    )
                await self._audit_action(profile, issue_key, AUDIT_ACTIONS["add_comment"])
                return True, "comment_added"

            if action in (ACTION_GIVE_INFO, ACTION_REOPEN, ACTION_CONFIRM):
                transition = tr.transition_for(action)
                if action == ACTION_CONFIRM and not text:
                    comment = t("jira_comment_confirm")
                result = await self._jira.switch_status(
                    transition, issue_key, comment, attach_path, attach_name
                )
                await self._audit_action(
                    profile, issue_key, AUDIT_ACTIONS["switch_status"], transition
                )
                if result == SWITCH_OK:
                    return True, {
                        ACTION_GIVE_INFO: "issue_info_provided",
                        ACTION_REOPEN: "issue_reopened",
                        ACTION_CONFIRM: "issue_closed",
                    }[action]
                if result == SWITCH_WRONG_STATUS:
                    return False, "issue_info_already_provided"
                return False, "status_switch_failed"
        except JiraError as exc:
            logger.error("Ошибка Jira при действии '%s' по %s: %s", action, issue_key, exc)
            await self._admin_room.notify(ADMIN_EVENT_JIRA_ERROR, t("admin_jira_error", error=exc))
            return False, "jira_unavailable"
        finally:
            if attach_path:
                self._attachments.remove_local(attach_path)

        return False, "action_not_available"

    async def send_attachments(self, profile: Row, issue_key: str) -> tuple[bool, str, list[str]]:
        """Собрать и отправить архив вложений заявки.

        :returns: ``(успех, ключ_фразы, пропущенные_файлы)``.
        """
        issue = await self._jira.get_issue(issue_key)
        if not issue or not self.has_access(issue, profile):
            return False, "find_no_access", []
        if not issue.get("attachments"):
            return False, "attachments_none", []

        archive_path, skipped = await self._attachments.build_issue_archive(
            issue_key, issue["attachments"]
        )
        if not archive_path:
            return False, "attachments_failed", skipped

        sent = await self._attachments.send_archive(
            profile["room_id"], profile["user_id"], issue_key, archive_path
        )
        await self._audit_action(profile, issue_key, AUDIT_ACTIONS["get_attachments"])
        if not sent:
            return False, "attachments_failed", skipped
        return True, "attachments_sent", skipped

    async def _audit_action(
        self, profile: Row, issue_key: str, action: str, details: str = ""
    ) -> None:
        await self._repos.audit.add(
            profile.get("room_id"),
            profile.get("user_id"),
            action,
            f"{issue_key} {details}".strip(),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def page_prefix(pages: Sequence[Sequence[dict[str, Any]]], page: int) -> str:
        """Служебная подсказка при попытке уйти за границы пагинации."""
        if page < 1:
            return t("list_first_page")
        if page > len(pages):
            return t("list_last_page")
        return ""
