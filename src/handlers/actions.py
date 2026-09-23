# -*- coding: utf-8 -*-
"""Сценарии бота: то, что происходит при выборе пункта меню или вводе команды.

Один и тот же сценарий вызывается и из текстовых команд (``!create``), и из
реакций на меню, поэтому вся логика собрана здесь, а ``commands.py`` и
``reactions.py`` остаются тонкими маршрутизаторами.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from src import constants as const
from src.db.repository import Row
from src.matrix import menu as menus
from src.services.context import BotContext
from src.texts import t
from src.utils.text import human_duration
from src.utils.validators import normalize_issue_key

logger = logging.getLogger(__name__)


class ActionHandler:
    """Реализация всех пользовательских сценариев."""

    def __init__(self, ctx: BotContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    # Регистрация и меню
    # ------------------------------------------------------------------
    async def start(self, room_id: str, user_id: str) -> None:
        """Сценарий `!start` / пункт «Старт»."""
        ctx = self.ctx
        existing = await ctx.repos.users.get(room_id, user_id)
        if existing:
            await ctx.ui.send(room_id, t("registered_already"))
            return

        owner = await ctx.users.owner_of_room(room_id)
        if owner and owner["user_id"] != user_id:
            await ctx.ui.send(
                room_id, t("room_owned_by_other", owner=owner.get("displayname") or owner["user_id"])
            )
            return

        profile, created = await ctx.users.register(room_id, user_id)
        if not profile or not created:
            await ctx.ui.error(room_id)
            return

        await ctx.ui.send(
            room_id,
            t(
                "registered_ok",
                room_id=profile["room_id"],
                user_id=profile["user_id"],
            ),
        )
        await ctx.ui.main_menu(room_id, user_id)

    async def show_menu(self, room_id: str, user_id: str, profile: Row | None) -> None:
        """Сценарий `!menu`: главное меню или гостевое."""
        await self.ctx.repos.audit.add(room_id, user_id, const.AUDIT_ACTIONS["menu"])
        if profile:
            await self.ctx.ui.main_menu(room_id, user_id)
        else:
            await self.ctx.ui.guest_menu(room_id, user_id)

    async def show_help(self, room_id: str, user_id: str) -> None:
        """Справка по командам и навигации."""
        text = self.ctx.ui.help_text()
        await self.ctx.ui.send(room_id, text)

    async def show_profile(self, room_id: str, user_id: str, profile: Row) -> None:
        """Меню «Мой профиль»."""
        text = await self.ctx.users.profile_text(profile)
        await self.ctx.ui.send_menu(room_id, menus.profile_menu(), user_id, prefix_text=text)

    async def ask_delete_profile(self, room_id: str, user_id: str) -> None:
        await self.ctx.ui.send_menu(
            room_id,
            menus.confirm_menu("delete_profile"),
            user_id,
            prefix_text=t("profile_delete_confirm"),
        )

    async def delete_profile(self, room_id: str, user_id: str) -> None:
        await self.ctx.users.delete_binding(room_id, user_id, reason="Удаление профиля пользователем")
        await self.ctx.ui.send(room_id, t("profile_deleted"))
        await self.ctx.ui.guest_menu(room_id, user_id)

    async def cancel(self, room_id: str, user_id: str, profile: Row | None) -> None:
        """Отмена текущего ввода."""
        await self.ctx.repos.states.clear(room_id)
        await self.ctx.ui.send(room_id, t("operation_cancelled"))
        if profile:
            await self.ctx.ui.main_menu(room_id, user_id)

    # ------------------------------------------------------------------
    # Создание заявки
    # ------------------------------------------------------------------
    async def begin_create(self, room_id: str, user_id: str) -> None:
        await self.ctx.repos.states.set(room_id, user_id, const.STATE_WAIT_SUBJECT, {})
        await self.ctx.ui.ask_input(
            room_id,
            t("create_enter_subject", max_length=self.ctx.issues.subject_max_length),
            user_id,
        )

    async def confirm_create(self, room_id: str, user_id: str, profile: Row, payload: dict[str, Any]) -> None:
        """Пользователь подтвердил создание заявки."""
        await self.ctx.repos.states.clear(room_id)
        await self.ctx.ui.send(room_id, t("create_in_progress"))
        issue_key = await self.ctx.issues.create(
            profile,
            payload.get("subject", ""),
            payload.get("description", ""),
            payload.get("attach_path"),
            payload.get("attach_name"),
        )
        if not issue_key:
            await self.ctx.ui.send(
                room_id, t("create_failed", support_email=self.ctx.support_email)
            )
        else:
            portal = str(self.ctx.config.get("jira.portal_url", "")).rstrip("/")
            await self.ctx.ui.send(
                room_id,
                t(
                    "create_success",
                    issue_key=issue_key,
                    portal_url=f"{portal}/{issue_key}" if portal else "-",
                ),
            )
        await self.ctx.ui.main_menu(room_id, user_id)

    # ------------------------------------------------------------------
    # Статус заявки: список и поиск
    # ------------------------------------------------------------------
    async def show_status_menu(self, room_id: str, user_id: str) -> None:
        await self.ctx.ui.send_menu(room_id, menus.status_menu(), user_id)

    async def show_list(self, room_id: str, user_id: str, profile: Row, page: int = 1) -> None:
        """Постраничный список заявок пользователя."""
        ctx = self.ctx
        state = await ctx.repos.states.get(room_id)
        pages = (state or {}).get("payload", {}).get("pages") if state else None

        if not pages or page == 1:
            await ctx.ui.send(room_id, t("list_preparing"))
            try:
                pages = await ctx.issues.list_pages(profile)
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось получить список заявок для %s: %s", user_id, exc)
                await ctx.ui.error(room_id, "jira_unavailable")
                return
            await ctx.repos.states.set(room_id, user_id, const.STATE_IDLE, {"pages": pages})

        if not pages or not pages[0]:
            await ctx.ui.send(room_id, t("list_empty"))
            await ctx.ui.main_menu(room_id, user_id)
            return

        hint = ctx.issues.page_prefix(pages, page)
        if hint:
            await ctx.ui.send(room_id, hint)
            page = min(max(page, 1), len(pages))

        await ctx.ui.send_menu(
            room_id, menus.list_menu(pages[page - 1], page, len(pages)), user_id
        )

    async def ask_issue_key(self, room_id: str, user_id: str) -> None:
        example = f"{self.ctx.issues.project}-1234"
        await self.ctx.repos.states.set(room_id, user_id, const.STATE_WAIT_ISSUE_KEY, {})
        await self.ctx.ui.ask_input(room_id, t("find_enter_key", example=example), user_id)

    async def open_issue(self, room_id: str, user_id: str, profile: Row, issue_key: str) -> None:
        """Показать карточку заявки и динамическое подменю действий."""
        ctx = self.ctx
        normalized = normalize_issue_key(issue_key, ctx.issues.project)
        if not normalized:
            await ctx.ui.send(
                room_id, t("find_invalid_key", example=f"{ctx.issues.project}-1234")
            )
            return

        await ctx.ui.send(room_id, t("collecting_issue_info"))
        try:
            issue = await ctx.issues.open_issue(profile, normalized)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка получения заявки %s: %s", normalized, exc)
            await ctx.ui.error(room_id, "jira_unavailable")
            return

        if not issue:
            await ctx.ui.send(
                room_id, t("find_no_access", support_email=ctx.support_email)
            )
            return

        await ctx.repos.states.clear(room_id)
        await ctx.ui.send_menu(
            room_id,
            ctx.issues.actions_menu(issue),
            user_id,
            prefix_text=ctx.issues.issue_card_text(issue),
        )

    # ------------------------------------------------------------------
    # Действия по заявке
    # ------------------------------------------------------------------
    _ACTION_STATE = {
        const.ACTION_COMMENT: const.STATE_WAIT_COMMENT,
        const.ACTION_GIVE_INFO: const.STATE_WAIT_GIVE_INFO,
        const.ACTION_REOPEN: const.STATE_WAIT_REOPEN,
    }

    async def begin_issue_action(
        self, room_id: str, user_id: str, profile: Row, action: str, issue_key: str
    ) -> None:
        """Начать действие по заявке: запросить текст или выполнить сразу."""
        ctx = self.ctx

        if action == const.ACTION_ATTACHMENTS:
            await self.send_attachments(room_id, user_id, profile, issue_key)
            return

        if action == const.ACTION_CONFIRM:
            await ctx.ui.send(room_id, t("processing"))
            ok, key = await ctx.issues.perform_action(profile, issue_key, action)
            await self._report_action_result(room_id, user_id, profile, issue_key, ok, key)
            return

        state = self._ACTION_STATE.get(action)
        if not state:
            await ctx.ui.send(room_id, t("action_not_available", status="-"))
            return

        await ctx.repos.states.set(room_id, user_id, state, {"issue_key": issue_key})
        await ctx.ui.ask_input(
            room_id,
            t("enter_comment", max_size=ctx.attachments.max_size_mb),
            user_id,
        )

    async def finish_issue_action(
        self,
        room_id: str,
        user_id: str,
        profile: Row,
        action: str,
        issue_key: str,
        text: str | None,
        attach_path: str | None,
        attach_name: str | None,
    ) -> None:
        """Завершить действие по заявке после ввода текста/вложения."""
        ctx = self.ctx
        await ctx.repos.states.clear(room_id)
        await ctx.ui.send(room_id, t("processing"))
        ok, key = await ctx.issues.perform_action(
            profile, issue_key, action, text=text, attach_path=attach_path, attach_name=attach_name
        )
        await self._report_action_result(room_id, user_id, profile, issue_key, ok, key)

    async def _report_action_result(
        self, room_id: str, user_id: str, profile: Row, issue_key: str, ok: bool, key: str
    ) -> None:
        ctx = self.ctx
        if key == "action_not_available":
            issue = await ctx.jira.get_issue(issue_key)
            await ctx.ui.send(
                room_id, t("action_not_available", status=(issue or {}).get("status", "-"))
            )
        elif key in ("find_no_access", "jira_unavailable", "create_failed"):
            await ctx.ui.send(room_id, t(key, support_email=ctx.support_email))
        else:
            await ctx.ui.send(room_id, t(key, issue_key=issue_key))

        if ok:
            await self.open_issue(room_id, user_id, profile, issue_key)
        else:
            await ctx.ui.main_menu(room_id, user_id)

    async def send_attachments(
        self, room_id: str, user_id: str, profile: Row, issue_key: str
    ) -> None:
        """Отправить архив вложений заявки."""
        ctx = self.ctx
        await ctx.ui.send(room_id, t("attachments_preparing"))
        try:
            ok, key, skipped = await ctx.issues.send_attachments(profile, issue_key)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка отправки вложений по %s: %s", issue_key, exc)
            await ctx.ui.send(room_id, t("attachments_failed"))
            return

        for filename in skipped:
            await ctx.ui.send(
                room_id,
                t("attachments_too_big", filename=filename, max_size=ctx.attachments.max_size_mb),
            )
        if ok:
            await ctx.ui.send(
                room_id,
                t(
                    "attachments_sent",
                    issue_key=issue_key,
                    ttl_human=human_duration(ctx.attachments.ttl),
                ),
            )
        else:
            await ctx.ui.send(room_id, t(key, support_email=ctx.support_email) if key == "find_no_access" else t(key))

    # ------------------------------------------------------------------
    # Обратная связь
    # ------------------------------------------------------------------
    async def begin_feedback(self, room_id: str, user_id: str) -> None:
        await self.ctx.repos.states.set(room_id, user_id, const.STATE_WAIT_FEEDBACK, {})
        await self.ctx.ui.ask_input(
            room_id, t("feedback_enter", support_email=self.ctx.support_email), user_id
        )

    async def finish_feedback(
        self, room_id: str, user_id: str, profile: Row | None, message: str
    ) -> None:
        """Сохранить обращение и переслать его в комнату администраторов."""
        ctx = self.ctx
        feedback_id = f"{random.randint(10000, 99999)}"
        await ctx.repos.states.clear(room_id)
        await ctx.repos.feedback.add(feedback_id, room_id, user_id, message)
        await ctx.repos.audit.add(room_id, user_id, const.AUDIT_ACTIONS["feedback"], feedback_id)
        await ctx.admin_room.notify(
            const.ADMIN_EVENT_FEEDBACK,
            t(
                "admin_feedback",
                feedback_id=feedback_id,
                displayname=(profile or {}).get("displayname") or user_id,
                user_id=user_id,
                room_id=room_id,
                message=message,
            ),
        )
        await ctx.ui.send(room_id, t("feedback_accepted", feedback_id=feedback_id))
        if profile:
            await ctx.ui.main_menu(room_id, user_id)
        else:
            await ctx.ui.guest_menu(room_id, user_id)
