# -*- coding: utf-8 -*-
"""Точка сборки бота: инициализация сервисов, регистрация обработчиков nio,
основной цикл синхронизации и фоновые задачи.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

from src import constants as const
from src.db.base import Database
from src.db.repository import Repositories
from src.handlers.actions import ActionHandler
from src.handlers.admin import AdminHandler
from src.handlers.commands import CommandHandler
from src.handlers.membership import MembershipHandler
from src.handlers.messages import IncomingMessage, MessageHandler
from src.handlers.reactions import ReactionHandler
from src.jira.client import JiraClient
from src.jira.status_listener import StatusListenerClient
from src.matrix.admin_api import SynapseAdminApi
from src.matrix.client import MatrixClient
from src.services.admin_room import AdminRoomService
from src.services.attachments import AttachmentService
from src.services.context import BotContext
from src.services.issues import IssueService
from src.services.notifications import NotificationService
from src.services.ui import UiService
from src.services.users import UserService
from src.texts import t

logger = logging.getLogger(__name__)


class Bot:
    """Бот службы технической поддержки для Matrix Synapse."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.started_at = dt.datetime.now()
        self._start_ms = int(time.time() * 1000)
        self._tasks: list[asyncio.Task[Any]] = []

        self.db = Database(config)
        self.repos = Repositories(self.db)
        self.matrix = MatrixClient(config)
        self.jira = JiraClient(config)
        self.admin_api = SynapseAdminApi(config)
        self.listener = StatusListenerClient(config, self.jira)
        self.admin_room = AdminRoomService(config, self.matrix)
        self.ui = UiService(config, self.matrix, self.repos)
        self.attachments = AttachmentService(
            config, self.repos, self.matrix, self.jira, self.admin_api, self.admin_room
        )
        self.issues = IssueService(config, self.repos, self.jira, self.attachments, self.admin_room)
        self.users = UserService(config, self.repos, self.matrix, self.jira, self.admin_room)

        self.ctx = BotContext(
            config=config,
            repos=self.repos,
            matrix=self.matrix,
            jira=self.jira,
            listener=self.listener,
            admin_api=self.admin_api,
            ui=self.ui,
            users=self.users,
            issues=self.issues,
            attachments=self.attachments,
            admin_room=self.admin_room,
            started_at=self.started_at,
        )
        self.actions = ActionHandler(self.ctx)
        self.commands = CommandHandler(self.ctx, self.actions)
        self.reactions = ReactionHandler(self.ctx, self.actions)
        self.messages = MessageHandler(self.ctx, self.actions)
        self.membership = MembershipHandler(self.ctx, self.actions)
        self.admin = AdminHandler(self.ctx)
        self.notifications = NotificationService(
            config, self.repos, self.listener, self.ui, self.admin_room
        )

    # ------------------------------------------------------------------
    # Инициализация
    # ------------------------------------------------------------------
    async def setup(self) -> None:
        """Подключить БД, Synapse, Jira и зарегистрировать обработчики событий."""
        await self.db.connect()
        await self.db.init_schema()
        await self.repos.menus.cleanup_older_than(days=30)

        await self.admin_api.start()
        await self.jira.start()
        await self.matrix.start()
        self._register_callbacks()

        if self.admin_room.room_id:
            await self.matrix.join(self.admin_room.room_id)
        await self.admin_room.notify(
            const.ADMIN_EVENT_BOT_STARTED,
            t("admin_bot_started", app_name=const.APP_NAME, version=const.VERSION),
        )

    def _register_callbacks(self) -> None:
        """Подписаться на события Matrix."""
        from nio import (
            InviteMemberEvent,
            RoomMemberEvent,
            RoomMessageAudio,
            RoomMessageFile,
            RoomMessageImage,
            RoomMessageText,
            RoomMessageVideo,
            UnknownEvent,
        )

        client = self.matrix.client
        client.add_event_callback(self._on_text, RoomMessageText)
        for media_type in (RoomMessageFile, RoomMessageImage, RoomMessageAudio, RoomMessageVideo):
            client.add_event_callback(self._on_media, media_type)
        client.add_event_callback(self._on_member_event, RoomMemberEvent)
        client.add_event_callback(self._on_unknown, UnknownEvent)
        client.add_event_callback(self._on_invite, InviteMemberEvent)

        try:  # ReactionEvent появился в новых версиях nio
            from nio import ReactionEvent  # type: ignore[attr-defined]

            client.add_event_callback(self._on_reaction, ReactionEvent)
        except ImportError:  # pragma: no cover - обрабатываем реакции через UnknownEvent
            logger.info("nio без ReactionEvent — реакции обрабатываются через UnknownEvent")

    # ------------------------------------------------------------------
    # Служебные проверки
    # ------------------------------------------------------------------
    def _is_own(self, sender: str) -> bool:
        return sender == self.matrix.user_id

    def _is_old(self, event: Any) -> bool:
        """Отбросить события, пришедшие до запуска бота (backlog первого sync)."""
        timestamp = getattr(event, "server_timestamp", None)
        return bool(timestamp) and timestamp < self._start_ms

    async def _room_guard(self, room_id: str, user_id: str) -> tuple[bool, Any]:
        """Проверить, что сообщение пришло от владельца комнаты (п. 1.5 ТЗ).

        :returns: ``(можно_обрабатывать, профиль_владельца_или_None)``
        """
        owner = await self.repos.users.get_by_room(room_id)
        if owner is None:
            return True, None
        if owner["user_id"] == user_id:
            await self.repos.users.touch(room_id)
            return True, owner
        logger.info(
            "Сообщение от %s в комнате %s, закреплённой за %s — игнорируем",
            user_id,
            room_id,
            owner["user_id"],
        )
        await self.ui.send(
            room_id,
            t("room_owned_by_other", owner=owner.get("displayname") or owner["user_id"]),
        )
        await self.admin_room.notify(
            const.ADMIN_EVENT_ROOM_HIJACK_ATTEMPT,
            t(
                "admin_room_hijack_attempt",
                user_id=user_id,
                room_id=room_id,
                owner=owner["user_id"],
            ),
        )
        return False, owner

    # ------------------------------------------------------------------
    # Обработчики событий
    # ------------------------------------------------------------------
    async def _on_text(self, room: Any, event: Any) -> None:
        if self._is_own(event.sender) or self._is_old(event):
            return
        try:
            await self._dispatch_message(
                IncomingMessage(
                    room_id=room.room_id,
                    user_id=event.sender,
                    event_id=event.event_id,
                    body=event.body or "",
                    msgtype="m.text",
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки сообщения в комнате %s", room.room_id)
            await self.ui.error(room.room_id)

    async def _on_media(self, room: Any, event: Any) -> None:
        if self._is_own(event.sender) or self._is_old(event):
            return
        try:
            await self._dispatch_message(
                IncomingMessage(
                    room_id=room.room_id,
                    user_id=event.sender,
                    event_id=event.event_id,
                    body=event.body or "",
                    msgtype=str(getattr(event, "source", {}).get("content", {}).get("msgtype", "m.file")),
                    url=getattr(event, "url", None),
                    filename=getattr(event, "body", None),
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки вложения в комнате %s", room.room_id)
            await self.ui.error(room.room_id)

    async def _dispatch_message(self, message: IncomingMessage) -> None:
        """Общий разбор входящего сообщения."""
        room_id, user_id = message.room_id, message.user_id
        logger.info("Сообщение от %s в %s: %s", user_id, room_id, message.text[:200])

        # Комната администраторов обрабатывается отдельно
        if self.admin.is_admin_room(room_id):
            if await self.admin.handle(room_id, user_id, message.text):
                return
            return

        allowed, owner = await self._room_guard(room_id, user_id)
        if not allowed:
            return

        profile = await self.repos.users.get(room_id, user_id)
        if await self.commands.handle(room_id, user_id, profile, message.text):
            return
        await self.messages.handle(message, profile)

    async def _on_reaction(self, room: Any, event: Any) -> None:
        if self._is_own(event.sender) or self._is_old(event):
            return
        relates = getattr(event, "reacts_to", None)
        key = getattr(event, "key", None)
        if not (relates and key):
            return
        await self._handle_reaction(room.room_id, event.sender, relates, key)

    async def _on_unknown(self, room: Any, event: Any) -> None:
        """Реакции приходят как UnknownEvent в версиях nio без ReactionEvent."""
        if getattr(event, "type", "") != "m.reaction":
            return
        if self._is_own(event.sender) or self._is_old(event):
            return
        relation = (event.source.get("content", {}) or {}).get("m.relates_to", {})
        await self._handle_reaction(
            room.room_id, event.sender, relation.get("event_id", ""), relation.get("key", "")
        )

    async def _handle_reaction(
        self, room_id: str, user_id: str, target_event_id: str, emoji: str
    ) -> None:
        if not (target_event_id and emoji):
            return
        try:
            if self.admin.is_admin_room(room_id):
                return
            allowed, _owner = await self._room_guard(room_id, user_id)
            if not allowed:
                return
            await self.reactions.handle(room_id, user_id, target_event_id, emoji)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки реакции %s в комнате %s", emoji, room_id)
            await self.ui.error(room_id)

    async def _on_invite(self, room: Any, event: Any) -> None:
        if getattr(event, "membership", "") != "invite":
            return
        if getattr(event, "state_key", "") != self.matrix.user_id:
            return
        try:
            await self.membership.on_invite(room.room_id, event.sender)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки приглашения в комнату %s", room.room_id)

    async def _on_member_event(self, room: Any, event: Any) -> None:
        if self._is_old(event):
            return
        try:
            await self.membership.on_member_change(
                room.room_id,
                getattr(event, "state_key", ""),
                getattr(event, "membership", ""),
                event.sender,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обработки состава комнаты %s", room.room_id)

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Запустить бота: фоновые задачи и бесконечный sync с Synapse."""
        await self.setup()

        self._tasks = [
            asyncio.create_task(self.notifications.loop(), name="jira-monitoring"),
            asyncio.create_task(self.attachments.cleanup_loop(), name="attachments-cleanup"),
        ]

        sync_timeout = int(self.config.get("matrix.sync_timeout", 30000))
        logger.info("Бот %s %s запущен", const.APP_NAME, const.VERSION)
        try:
            while True:
                try:
                    await self.matrix.client.sync_forever(
                        timeout=sync_timeout, full_state=False, loop_sleep_time=1000
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("Обрыв синхронизации с Synapse — повтор через 10 секунд")
                    await asyncio.sleep(10)
                    await self.matrix.whoami()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Аккуратно остановить фоновые задачи и закрыть соединения."""
        logger.warning("Бот останавливается…")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.admin_room.notify(
            const.ADMIN_EVENT_BOT_STOPPED, t("admin_bot_stopped", app_name=const.APP_NAME)
        )
        await self.jira.close()
        await self.admin_api.close()
        await self.matrix.close()
        await self.db.close()
        logger.warning("Бот остановлен")
