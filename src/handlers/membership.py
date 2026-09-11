# -*- coding: utf-8 -*-
"""Жизненный цикл комнаты: auto-join, приветствие, выход и кик (п. 1 ТЗ)."""
from __future__ import annotations

import logging

from src.constants import ADMIN_EVENT_MATRIX_ERROR
from src.handlers.actions import ActionHandler
from src.services.context import BotContext
from src.texts import t

logger = logging.getLogger(__name__)


class MembershipHandler:
    """Обработка приглашений и изменений состава комнаты."""

    def __init__(self, ctx: BotContext, actions: ActionHandler) -> None:
        self.ctx = ctx
        self.actions = actions

    @property
    def _bot_id(self) -> str:
        return self.ctx.matrix.user_id

    async def on_invite(self, room_id: str, inviter: str) -> None:
        """Автоматически принять приглашение и показать нужное меню."""
        ctx = self.ctx
        if not bool(ctx.config.get("matrix.auto_join", True)):
            logger.info("auto_join отключён — приглашение в %s проигнорировано", room_id)
            return

        logger.info("Приглашение в комнату %s от %s — присоединяемся", room_id, inviter)
        if not await ctx.matrix.join(room_id):
            logger.error("Не удалось присоединиться к комнате %s", room_id)
            await ctx.admin_room.notify(
                ADMIN_EVENT_MATRIX_ERROR,
                t("admin_matrix_error", error=f"join {room_id} не выполнен"),
            )
            return

        # Комната администраторов: auto-join без приветствия и меню (п. 8.2 ТЗ)
        if ctx.admin_room.room_id and room_id == ctx.admin_room.room_id:
            logger.info("Присоединились к комнате администраторов %s", room_id)
            await ctx.admin_room.send(t("admin_help"))
            return

        profile = await ctx.repos.users.get_by_room(room_id)
        if profile:
            await ctx.ui.send(
                room_id, t("greeting_registered", displayname=profile.get("displayname") or "")
            )
            await ctx.ui.main_menu(room_id, profile["user_id"])
            return

        await ctx.ui.send(room_id, t("greeting_guest"))
        await ctx.ui.guest_menu(room_id, inviter)

    async def on_member_change(
        self, room_id: str, state_key: str, membership: str, sender: str
    ) -> None:
        """Реакция на выход/кик участника или самого бота."""
        ctx = self.ctx
        if membership not in ("leave", "ban"):
            return

        if state_key == self._bot_id:
            # Бота удалили или кикнули из комнаты (п. 1.4 ТЗ)
            reason = "Бот удалён из комнаты" if sender != self._bot_id else "Бот покинул комнату"
            logger.info("%s %s — забываем комнату", reason, room_id)
            await ctx.users.forget_room(room_id, reason=reason)
            return

        owner = await ctx.repos.users.get_by_room(room_id)
        if owner and owner["user_id"] == state_key:
            # Владелец комнаты вышел (п. 1.7 ТЗ)
            logger.info(
                "Пользователь %s вышел из закреплённой за ним комнаты %s — удаляем связь",
                state_key,
                room_id,
            )
            await ctx.users.delete_binding(
                room_id, state_key, reason="Пользователь вышел из комнаты"
            )
