# -*- coding: utf-8 -*-
"""Сервис вывода интерфейса: отправка меню, подсказок и сообщений.

Отправка меню = сообщение с пунктами + заранее проставленные ботом реакции +
запись соответствия ``event_id -> меню`` в БД, чтобы обработчик ``m.reaction``
знал, какой пункт выбрал пользователь.
"""
from __future__ import annotations

import logging
from typing import Any

from src.db.repository import Repositories
from src.matrix import menu as menus
from src.matrix.client import MatrixClient
from src.texts import t
from src.utils.text import split_message

logger = logging.getLogger(__name__)


class UiService:
    """Отправка сообщений и меню в комнаты Matrix."""

    def __init__(self, config: Any, matrix: MatrixClient, repos: Repositories) -> None:
        self._config = config
        self._matrix = matrix
        self._repos = repos
        self._as_list = bool(config.get("menu.menu_items_as_list", True))
        self._title_bold = bool(config.get("menu.title_bold", True))

    async def send(self, room_id: str, text: str, notice: bool = False) -> str | None:
        """Отправить текст (длинные сообщения разбиваются на части)."""
        event_id: str | None = None
        for chunk in split_message(text):
            event_id = await self._matrix.send_text(room_id, chunk, notice=notice)
        return event_id

    async def send_menu(
        self,
        room_id: str,
        menu: menus.Menu,
        user_id: str | None = None,
        prefix_text: str = "",
        title_bold: bool | None = None
    ) -> str | None:
        """Отправить меню и проставить все его реакции."""
        if prefix_text:
            await self.send(room_id, prefix_text)

        bold = self._title_bold if title_bold is None else title_bold
        text = menu.render(as_list=self._as_list, title_bold=bold)
        event_id = await self._matrix.send_text(room_id, text)
        if not event_id:
            logger.error("Не удалось отправить меню '%s' в комнату %s", menu.menu_id, room_id)
            return None

        await self._repos.menus.save(
            event_id=event_id,
            room_id=room_id,
            user_id=user_id,
            menu_id=menu.menu_id,
            payload={"actions": menu.action_map(), "context": menu.payload},
        )
        await self._matrix.react_many(room_id, event_id, menu.emojis)
        return event_id

    async def ask_input(
        self, room_id: str, prompt: str, user_id: str | None = None
    ) -> str | None:
        """Запросить у пользователя текстовый ввод и показать меню отмены."""
        await self.send(room_id, prompt)
        return await self.send_menu(room_id, menus.cancel_menu(), user_id=user_id)

    async def main_menu(self, room_id: str, user_id: str | None = None) -> str | None:
        return await self.send_menu(room_id, menus.main_menu(), user_id=user_id)

    async def guest_menu(self, room_id: str, user_id: str | None = None) -> str | None:
        return await self.send_menu(room_id, menus.guest_menu(), user_id=user_id)

    async def error(self, room_id: str, key: str = "critical_error", **kwargs: Any) -> None:
        """Сообщить пользователю об ошибке (подставляя контакты поддержки)."""
        kwargs.setdefault("support_email", self._config.get("administration.support_email", ""))
        await self.send(room_id, t(key, **kwargs))

    def help_text(self) -> str:
        """Собрать текст справки (`!help`) с учётом ``menu.help_commands_as_list``."""
        as_list = bool(self._config.get("menu.help_commands_as_list", True))
        bullet = "- " if as_list else ""
        example = f"{self._config.get('jira.project', 'IT')}-1234"
        commands = [
            t("help_command_start"),
            t("help_command_create"),
            t("help_command_status", example=example),
            t("help_command_list"),
            t("help_command_menu"),
            t("help_command_help"),
        ]
        lines = [f"**{t('menu_title_help')}**", "", t("help_intro")]
        lines.extend(f"{bullet}{command}" for command in commands)
        lines.append("")
        lines.append(t("help_navigation"))
        return "\n".join(lines)
