# -*- coding: utf-8 -*-
"""Обработка событий ``m.reaction`` — выбор пункта текстового меню (п. 2.1 ТЗ)."""
from __future__ import annotations

import logging
from typing import Any

from src import constants as const
from src.db.repository import Row
from src.handlers.actions import ActionHandler
from src.services.context import BotContext
from src.texts import t

logger = logging.getLogger(__name__)

ISSUE_ACTIONS = (
    const.ACTION_GIVE_INFO,
    const.ACTION_REOPEN,
    const.ACTION_CONFIRM,
    const.ACTION_COMMENT,
    const.ACTION_ATTACHMENTS,
)


class ReactionHandler:
    """Определяет выбранный пункт меню по эмодзи и запускает сценарий."""

    def __init__(self, ctx: BotContext, actions: ActionHandler) -> None:
        self.ctx = ctx
        self.actions = actions

    async def handle(
        self, room_id: str, user_id: str, target_event_id: str, emoji: str
    ) -> bool:
        """Обработать реакцию пользователя на сообщение с меню."""
        menu_row = await self.ctx.repos.menus.get(target_event_id)
        if not menu_row:
            logger.debug("Реакция %s на сообщение %s без меню — игнорируем", emoji, target_event_id)
            return False

        actions_map: dict[str, Any] = (menu_row.get("payload") or {}).get("actions") or {}
        entry = actions_map.get(emoji)
        if not entry:
            logger.debug("Реакция %s не соответствует пунктам меню %s", emoji, menu_row["menu_id"])
            return False

        action = entry.get("action", "")
        payload: dict[str, Any] = entry.get("payload") or {}
        profile = await self.ctx.repos.users.get(room_id, user_id)

        logger.info(
            "Пользователь %s выбрал пункт '%s' в меню '%s' (комната %s)",
            user_id,
            action,
            menu_row["menu_id"],
            room_id,
        )
        await self.ctx.repos.audit.add(room_id, user_id, f"menu:{action}", menu_row["menu_id"])
        await self.dispatch(room_id, user_id, profile, action, payload)
        return True

    async def dispatch(
        self,
        room_id: str,
        user_id: str,
        profile: Row | None,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        """Выполнить сценарий, соответствующий выбранному пункту."""
        actions = self.actions

        if action == "start":
            await actions.start(room_id, user_id)
            return
        if action == "help":
            await actions.show_help(room_id, user_id)
            return
        if action == "feedback":
            await actions.begin_feedback(room_id, user_id)
            return
        if action == "cancel":
            await actions.cancel(room_id, user_id, profile)
            return

        if action == "confirm_no":
            await actions.cancel(room_id, user_id, profile)
            return

        if not profile:
            #await self.ctx.ui.send(room_id, t("not_registered"))
            await self.ctx.ui.guest_menu(room_id, user_id)
            return

        if action == "main_menu":
            await self.ctx.ui.main_menu(room_id, user_id)
            return
        if action == "create_issue":
            await actions.begin_create(room_id, user_id)
            return
        if action == "issue_status":
            await actions.show_status_menu(room_id, user_id)
            return
        if action == "profile":
            await actions.show_profile(room_id, user_id, profile)
            return
        if action == "delete_profile":
            await actions.ask_delete_profile(room_id, user_id)
            return
        if action == "list_issues":
            await actions.show_list(room_id, user_id, profile, page=1)
            return
        if action == "list_page":
            await actions.show_list(room_id, user_id, profile, page=int(payload.get("page", 1)))
            return
        if action == "find_issue":
            await actions.ask_issue_key(room_id, user_id)
            return
        if action == "open_issue":
            await actions.open_issue(room_id, user_id, profile, str(payload.get("issue_key", "")))
            return
        if action in ISSUE_ACTIONS:
            await actions.begin_issue_action(
                room_id, user_id, profile, action, str(payload.get("issue_key", ""))
            )
            return
        if action == "confirm_yes":
            confirm_action = str(payload.get("confirm_action", ""))
            if confirm_action == "delete_profile":
                await actions.delete_profile(room_id, user_id)
                return
            if confirm_action == "create_issue":
                state = await self.ctx.repos.states.get(room_id)
                await actions.confirm_create(
                    room_id, user_id, profile, (state or {}).get("payload", {})
                )
                return

        logger.warning("Неизвестное действие меню: %s", action)
        await self.ctx.ui.main_menu(room_id, user_id)
