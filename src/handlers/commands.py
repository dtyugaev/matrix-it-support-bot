# -*- coding: utf-8 -*-
"""Текстовые команды бота (п. 3 ТЗ): !start, !create, !status, !list, !menu, !help."""
from __future__ import annotations

import logging

from src.db.repository import Row
from src.handlers.actions import ActionHandler
from src.services.context import BotContext
from src.texts import t

logger = logging.getLogger(__name__)

COMMAND_PREFIX = "!"
KNOWN_COMMANDS = ("start", "menu", "help", "create", "list", "status")


class CommandHandler:
    """Маршрутизатор текстовых команд."""

    def __init__(self, ctx: BotContext, actions: ActionHandler) -> None:
        self.ctx = ctx
        self.actions = actions

    @staticmethod
    def parse(body: str) -> tuple[str, list[str]] | None:
        """Разобрать строку в ``(команда, аргументы)`` или вернуть ``None``."""
        text = (body or "").strip()
        if not text.startswith(COMMAND_PREFIX):
            return None
        parts = text[len(COMMAND_PREFIX) :].split()
        if not parts:
            return None
        return parts[0].lower(), parts[1:]

    async def handle(self, room_id: str, user_id: str, profile: Row | None, body: str) -> bool:
        """Обработать команду. Возвращает ``True``, если сообщение было командой."""
        parsed = self.parse(body)
        if parsed is None:
            return False
        command, args = parsed

        if command not in KNOWN_COMMANDS:
            await self.ctx.repos.audit.add(
                room_id, user_id, t("unknown_command", command=command)[:100]
            )
            await self.ctx.ui.send(room_id, t("unknown_command", command=command))
            await self.actions.show_help(room_id, user_id)
            return True

        if command == "start":
            await self.actions.start(room_id, user_id)
            return True
        if command == "help":
            await self.actions.show_help(room_id, user_id)
            return True
        if command == "menu":
            await self.actions.show_menu(room_id, user_id, profile)
            return True

        # Остальные команды доступны только зарегистрированному пользователю
        if not profile:
            await self.ctx.ui.send(room_id, t("not_registered"))
            await self.ctx.ui.guest_menu(room_id, user_id)
            return True

        if command == "create":
            await self.actions.begin_create(room_id, user_id)
            return True
        if command == "list":
            await self.actions.show_list(room_id, user_id, profile, page=1)
            return True
        if command == "status":
            if not args:
                await self.actions.ask_issue_key(room_id, user_id)
            else:
                await self.actions.open_issue(room_id, user_id, profile, args[0])
            return True
        return True
