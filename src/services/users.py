# -*- coding: utf-8 -*-
"""Регистрация пользователей и жизненный цикл привязки «комната ↔ пользователь».

Отличия от эталонного проекта: подтверждение по email не требуется, данные
берутся из профиля Matrix (displayname, MXID), ``jira_login`` = localpart MXID,
почта запрашивается в Jira API и может остаться пустой (п. 3 ТЗ).
"""
from __future__ import annotations

import logging
from typing import Any

from src.constants import (
    ADMIN_EVENT_USER_DELETED,
    ADMIN_EVENT_USER_REGISTERED,
    AUDIT_ACTIONS,
)
from src.db.repository import Repositories, Row
from src.matrix.client import MatrixClient
from src.services.admin_room import AdminRoomService
from src.texts import t
from src.utils.validators import localpart

logger = logging.getLogger(__name__)


class UserService:
    """Создание, чтение и удаление привязок (room_id, user_id)."""

    def __init__(
        self,
        config: Any,
        repos: Repositories,
        matrix: MatrixClient,
        jira: Any,
        admin_room: AdminRoomService,
    ) -> None:
        self._config = config
        self._repos = repos
        self._matrix = matrix
        self._jira = jira
        self._admin_room = admin_room

    async def owner_of_room(self, room_id: str) -> Row | None:
        """Пользователь, за которым закреплена комната."""
        return await self._repos.users.get_by_room(room_id)

    async def register(self, room_id: str, user_id: str) -> tuple[Row | None, bool]:
        """Зарегистрировать пользователя в комнате.

        :returns: ``(профиль, создан_ли_новый)``. Если комната уже закреплена за
            этим пользователем — возвращает существующий профиль и ``False``.
        """
        existing = await self._repos.users.get(room_id, user_id)
        if existing:
            return existing, False

        owner = await self._repos.users.get_by_room(room_id)
        if owner and owner["user_id"] != user_id:
            logger.warning(
                "Комната %s уже закреплена за %s, отказ для %s", room_id, owner["user_id"], user_id
            )
            return owner, False

        displayname = await self._matrix.displayname(user_id)
        jira_login = localpart(user_id)
        email = None
        try:
            email = await self._jira.get_user_email(jira_login)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось получить email из Jira для '%s': %s — продолжаем без почты",
                jira_login,
                exc,
            )

        profile = await self._repos.users.add(
            room_id=room_id,
            user_id=user_id,
            displayname=displayname,
            jira_login=jira_login,
            email=email,
        )
        await self._repos.audit.add(room_id, user_id, AUDIT_ACTIONS["start"], f"jira_login={jira_login}")
        await self._admin_room.notify(
            ADMIN_EVENT_USER_REGISTERED,
            t(
                "admin_user_registered",
                user_id=user_id,
                displayname=displayname,
                room_id=room_id,
            ),
        )
        return profile, True

    async def delete_binding(self, room_id: str, user_id: str, reason: str = "") -> bool:
        """Удалить привязку (room_id, user_id) — выход, кик или удаление профиля."""
        deleted = await self._repos.users.delete_binding(room_id, user_id)
        await self._repos.states.clear(room_id)
        await self._repos.menus.cleanup_room(room_id)
        if deleted:
            await self._repos.audit.add(
                room_id, user_id, AUDIT_ACTIONS["delete_profile"], reason
            )
            await self._admin_room.notify(
                ADMIN_EVENT_USER_DELETED,
                t("admin_user_deleted", user_id=user_id, room_id=room_id),
            )
        return bool(deleted)

    async def forget_room(self, room_id: str, reason: str = "") -> None:
        """Полностью забыть комнату: удалить записи БД и покинуть/забыть комнату."""
        owner = await self._repos.users.get_by_room(room_id)
        await self._repos.users.delete_room(room_id)
        await self._repos.states.clear(room_id)
        await self._repos.menus.cleanup_room(room_id)
        if owner:
            await self._repos.audit.add(
                room_id, owner["user_id"], AUDIT_ACTIONS["delete_profile"], reason
            )
            await self._admin_room.notify(
                ADMIN_EVENT_USER_DELETED,
                t("admin_user_deleted", user_id=owner["user_id"], room_id=room_id),
            )
        await self._matrix.forget(room_id)
        logger.info("Комната %s забыта (%s)", room_id, reason)

    async def profile_text(self, profile: Row) -> str:
        """Собрать карточку профиля для меню «Мой профиль»."""
        rooms = await self._repos.users.rooms_of_user(profile["user_id"])
        return t(
            "profile_card",
            user_id=profile["user_id"],
            displayname=profile.get("displayname") or profile["user_id"],
            jira_login=profile.get("jira_login") or "",
            email=profile.get("email") or t("registered_email_empty"),
            room_id=profile["room_id"],
            created_at=profile.get("created_at") or "",
            rooms_count=len(rooms),
        )
