# -*- coding: utf-8 -*-
"""Обработка обычных сообщений: ввод темы, описания, комментария, номера заявки.

Вложение и текст, как и в эталонном боте, ожидаются одним сообщением: в Matrix
файл приходит отдельным событием с ``msgtype`` ``m.file``/``m.image`` и полем
``body`` (имя файла), поэтому текст берётся из подписи, а при её отсутствии
подставляется служебная фраза.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src import constants as const
from src.db.repository import Row
from src.handlers.actions import ActionHandler
from src.matrix import menu as menus
from src.services.context import BotContext
from src.texts import t
from src.utils.validators import normalize_issue_key

logger = logging.getLogger(__name__)

FILE_MSGTYPES = ("m.file", "m.image", "m.audio", "m.video")


@dataclass
class IncomingMessage:
    """Нормализованное сообщение из комнаты Matrix."""

    room_id: str
    user_id: str
    event_id: str
    body: str
    msgtype: str = "m.text"
    url: str | None = None
    filename: str | None = None

    @property
    def has_file(self) -> bool:
        return bool(self.url) and self.msgtype in FILE_MSGTYPES

    @property
    def text(self) -> str:
        return (self.body or "").strip()


class MessageHandler:
    """Маршрутизация сообщений по текущему состоянию диалога."""

    def __init__(self, ctx: BotContext, actions: ActionHandler) -> None:
        self.ctx = ctx
        self.actions = actions

    async def handle(self, message: IncomingMessage, profile: Row | None) -> None:
        """Обработать сообщение пользователя."""
        ctx = self.ctx
        room_id, user_id = message.room_id, message.user_id
        state_row = await ctx.repos.states.get(room_id)
        state = (state_row or {}).get("state") or const.STATE_IDLE
        payload: dict[str, Any] = (state_row or {}).get("payload") or {}

        if state == const.STATE_WAIT_FEEDBACK:
            await self.actions.finish_feedback(room_id, user_id, profile, message.text)
            return

        if not profile:
            # Незарегистрированный пользователь: подсказываем, что делать
            await ctx.ui.send(room_id, t("not_registered"))
            await ctx.ui.guest_menu(room_id, user_id)
            return

        if state == const.STATE_WAIT_SUBJECT:
            await self._subject(message, payload)
            return
        if state == const.STATE_WAIT_DESCRIPTION:
            await self._description(message, profile, payload)
            return
        if state == const.STATE_WAIT_ISSUE_KEY:
            await self._issue_key(message, profile)
            return
        if state in (
            const.STATE_WAIT_COMMENT,
            const.STATE_WAIT_GIVE_INFO,
            const.STATE_WAIT_REOPEN,
        ):
            await self._issue_action_input(message, profile, state, payload)
            return

        # Свободный текст вне сценария — показываем справку (п. 3 ТЗ)
        await ctx.repos.audit.add(
            room_id, user_id, const.AUDIT_ACTIONS["unknown_command"], message.text[:200]
        )
        await self.actions.show_help(room_id, user_id)

    # ------------------------------------------------------------------
    # Создание заявки
    # ------------------------------------------------------------------
    async def _subject(self, message: IncomingMessage, payload: dict[str, Any]) -> None:
        ctx = self.ctx
        subject = message.text
        max_length = ctx.issues.subject_max_length

        if message.has_file or not subject:
            await ctx.ui.send(room_id=message.room_id, text=t("create_subject_empty"))
            return
        if len(subject) > max_length:
            await ctx.ui.send(
                message.room_id, t("create_subject_too_long", max_length=max_length)
            )
            return

        payload["subject"] = subject
        await ctx.repos.states.set(
            message.room_id, message.user_id, const.STATE_WAIT_DESCRIPTION, payload
        )
        await ctx.ui.ask_input(
            message.room_id,
            t("create_enter_description", max_size=ctx.attachments.max_size_mb),
            message.user_id,
        )

    async def _description(
        self, message: IncomingMessage, profile: Row, payload: dict[str, Any]
    ) -> None:
        ctx = self.ctx
        attach_path, attach_name = await self._save_attachment(message)
        description = message.text if not message.has_file else message.text
        if message.has_file and (not description or description == attach_name):
            description = t("create_description_empty")

        payload.update(
            {
                "description": description or t("create_description_empty"),
                "attach_path": attach_path,
                "attach_name": attach_name,
            }
        )
        await ctx.repos.states.set(
            message.room_id, message.user_id, const.STATE_WAIT_CONFIRM_CREATE, payload
        )
        summary = t(
            "create_confirm",
            subject=payload.get("subject", ""),
            description=payload.get("description", ""),
            attachment=attach_name or t("create_attachment_none"),
        )
        await ctx.ui.send_menu(
            message.room_id,
            menus.confirm_menu("create_issue"),
            message.user_id,
            prefix_text=summary,
        )

    # ------------------------------------------------------------------
    # Поиск заявки и действия по ней
    # ------------------------------------------------------------------
    async def _issue_key(self, message: IncomingMessage, profile: Row) -> None:
        ctx = self.ctx
        project = ctx.issues.project
        normalized = normalize_issue_key(message.text, project)
        if not normalized:
            # Не выходим из сценария: просим ввести номер ещё раз (п. 5.1 ТЗ)
            await ctx.ui.send(message.room_id, t("find_invalid_key", example=f"{project}-1234"))
            return
        await self.actions.open_issue(message.room_id, message.user_id, profile, normalized)

    async def _issue_action_input(
        self, message: IncomingMessage, profile: Row, state: str, payload: dict[str, Any]
    ) -> None:
        action = {
            const.STATE_WAIT_COMMENT: const.ACTION_COMMENT,
            const.STATE_WAIT_GIVE_INFO: const.ACTION_GIVE_INFO,
            const.STATE_WAIT_REOPEN: const.ACTION_REOPEN,
        }[state]
        issue_key = str(payload.get("issue_key", ""))
        attach_path, attach_name = await self._save_attachment(message)
        text = message.text if not message.has_file else message.text
        if message.has_file and text == attach_name:
            text = ""
        await self.actions.finish_issue_action(
            message.room_id,
            message.user_id,
            profile,
            action,
            issue_key,
            text or None,
            attach_path,
            attach_name,
        )

    # ------------------------------------------------------------------
    async def _save_attachment(self, message: IncomingMessage) -> tuple[str | None, str | None]:
        """Скачать вложение пользователя из Synapse в папку бота."""
        if not message.has_file:
            return None, None
        filename = message.filename or message.body or "attachment"
        path = await self.ctx.attachments.save_user_attachment(
            message.room_id, str(message.url), filename
        )
        if not path:
            await self.ctx.ui.send(
                message.room_id,
                t(
                    "attachments_too_big",
                    filename=filename,
                    max_size=self.ctx.attachments.max_size_mb,
                ),
            )
            return None, None
        return path, filename
