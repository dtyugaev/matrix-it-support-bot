# -*- coding: utf-8 -*-
"""Команды администратора бота в комнате администраторов (п. 8 ТЗ).

Перенесённые из эталонного проекта возможности: список и выгрузка
пользователей, аудит, статистика, логи, адресное сообщение пользователю,
рассылка и ответ на обращение обратной связи.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import os
from typing import Any

from src.constants import VERSION, APP_NAME
from src.services.context import BotContext
from src.texts import t
from src.utils.text import human_duration
from src.utils.validators import is_valid_mxid

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "!admin"

PERIODS: dict[str, int | None] = {"day": 1, "week": 7, "month": 30, "all": None}


class AdminHandler:
    """Обработчик административных команд."""

    def __init__(self, ctx: BotContext) -> None:
        self.ctx = ctx

    def is_admin_room(self, room_id: str) -> bool:
        return bool(self.ctx.admin_room.room_id) and room_id == self.ctx.admin_room.room_id

    async def handle(self, room_id: str, user_id: str, body: str) -> bool:
        """Обработать команду администратора. ``True`` — команда распознана."""
        text = (body or "").strip()
        if not text.lower().startswith(ADMIN_PREFIX):
            return False

        if not self.ctx.admin_room.is_admin(user_id):
            await self.ctx.ui.send(room_id, t("admin_denied"))
            return True

        parts = text.split()
        command = parts[1].lower() if len(parts) > 1 else "help"
        args = parts[2:]
        logger.info("Администратор %s выполняет команду '%s'", user_id, command)

        handlers = {
            "help": self._help,
            "status": self._status,
            "users": self._users,
            "export_users": self._export_users,
            "audit": self._audit,
            "stats": self._stats,
            "logs": self._logs,
            "send": self._send,
            "broadcast": self._broadcast,
            "answer": self._answer,
        }
        handler = handlers.get(command)
        if handler is None:
            await self.ctx.ui.send(room_id, t("admin_bad_args"))
            await self.ctx.ui.send(room_id, t("admin_help"))
            return True
        try:
            await handler(room_id, args)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка выполнения команды администратора '%s'", command)
            await self.ctx.ui.error(room_id)
        return True

    # ------------------------------------------------------------------
    async def _help(self, room_id: str, _args: list[str]) -> None:
        await self.ctx.ui.send(room_id, t("admin_help"))

    async def _status(self, room_id: str, _args: list[str]) -> None:
        ctx = self.ctx
        counters = await ctx.repos.users.counters()
        uptime = dt.datetime.now() - ctx.started_at
        await ctx.ui.send(
            room_id,
            t(
                "admin_status_card",
                version=f"{APP_NAME} {VERSION}",
                started_at=ctx.started_at.strftime("%d.%m.%Y %H:%M:%S"),
                uptime=human_duration(int(uptime.total_seconds())),
                rooms=counters["rooms"],
                users=counters["users"],
                jira_state=t("admin_state_ok") if ctx.jira.available else t("admin_state_fail"),
                matrix_state=t("admin_state_ok") if ctx.matrix.available else t("admin_state_fail"),
                pending_attachments=await ctx.repos.attachments.pending_count(),
            ),
        )

    async def _users(self, room_id: str, args: list[str]) -> None:
        pattern = " ".join(args)
        rows = (
            await self.ctx.repos.users.search(pattern)
            if pattern
            else await self.ctx.repos.users.all_users()
        )
        if not rows:
            await self.ctx.ui.send(room_id, t("admin_users_empty"))
            return
        chunks: list[str] = []
        for row in rows[:50]:
            rooms = await self.ctx.repos.users.rooms_of_user(row["user_id"])
            chunks.append(
                t(
                    "admin_user_line",
                    user_id=row["user_id"],
                    displayname=row.get("displayname") or "-",
                    jira_login=row.get("jira_login") or "-",
                    email=row.get("email") or t("registered_email_empty"),
                    rooms_count=len(rooms),
                )
            )
        await self.ctx.ui.send(room_id, "\n\n".join(chunks))

    async def _export_users(self, room_id: str, _args: list[str]) -> None:
        rows = await self.ctx.repos.users.all_users()
        storage = self.ctx.config.path_of("jira.attachments.dir", "storage/attachments")
        os.makedirs(storage, exist_ok=True)
        path = os.path.join(
            storage, f"users_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        columns = ["room_id", "user_id", "displayname", "email", "jira_login", "created_at"]
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", delimiter=";")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in columns})
        await self.ctx.admin_room.send_file(path, caption=f"Пользователи бота ({len(rows)})")
        self.ctx.attachments.remove_local(path)

    async def _audit(self, room_id: str, args: list[str]) -> None:
        target = args[0] if args and is_valid_mxid(args[0]) else None
        days_arg = args[1] if target and len(args) > 1 else (args[0] if args and not target else "7")
        try:
            days = int(days_arg)
        except (TypeError, ValueError):
            days = 7
        rows = await self.ctx.repos.audit.list(target, days=days, limit=100)
        header = t("admin_audit_header", target=target or "всех пользователей", days=days)
        if not rows:
            await self.ctx.ui.send(room_id, f"{header}\n\n{t('admin_users_empty')}")
            return
        lines = [
            t(
                "admin_audit_line",
                date=row.get("created_at"),
                user_id=row.get("user_id") or "-",
                action=row.get("action") or "-",
                details=row.get("details") or "-",
            )
            for row in rows
        ]
        await self.ctx.ui.send(room_id, "\n".join([header, "", *lines]))

    async def _stats(self, room_id: str, args: list[str]) -> None:
        period = (args[0].lower() if args else "week")
        if period not in PERIODS:
            await self.ctx.ui.send(room_id, t("admin_bad_args"))
            return
        rows = await self.ctx.repos.audit.stats(PERIODS[period])
        lines = [t("admin_stats_header", period=period)]
        if not rows:
            lines.append(t("admin_users_empty"))
        else:
            lines.extend(
                t("admin_stats_line", action=row.get("action"), count=row.get("total"))
                for row in rows
            )
        await self.ctx.ui.send(room_id, "\n".join(lines))

    async def _logs(self, room_id: str, _args: list[str]) -> None:
        log_path = self.ctx.config.path_of("logging.file_path")
        if not (log_path and os.path.isfile(log_path)):
            await self.ctx.ui.send(room_id, t("admin_users_empty"))
            return
        await self.ctx.admin_room.send_file(log_path, caption=os.path.basename(log_path))

    async def _send(self, room_id: str, args: list[str]) -> None:
        if len(args) < 2 or not is_valid_mxid(args[0]):
            await self.ctx.ui.send(room_id, t("admin_bad_args"))
            return
        target, text = args[0], " ".join(args[1:])
        rooms = await self.ctx.repos.users.rooms_of_user(target)
        if not rooms:
            await self.ctx.ui.send(room_id, t("admin_user_not_found", user_id=target))
            return
        sent = 0
        for row in rooms:
            if await self.ctx.ui.send(row["room_id"], text):
                sent += 1
        await self.ctx.ui.send(room_id, t("admin_send_result", user_id=target, sent=sent))

    async def _broadcast(self, room_id: str, args: list[str]) -> None:
        if not args:
            await self.ctx.ui.send(room_id, t("admin_bad_args"))
            return
        text = " ".join(args)
        rows = await self.ctx.repos.users.all_users()
        sent, errors = 0, 0
        for row in rows:
            if await self.ctx.ui.send(row["room_id"], text):
                sent += 1
            else:
                errors += 1
                logger.error("Рассылка не доставлена в комнату %s", row["room_id"])
        await self.ctx.ui.send(room_id, t("admin_broadcast_result", sent=sent, errors=errors))

    async def _answer(self, room_id: str, args: list[str]) -> None:
        if len(args) < 2:
            await self.ctx.ui.send(room_id, t("admin_bad_args"))
            return
        feedback_id, text = args[0], " ".join(args[1:])
        feedback = await self.ctx.repos.feedback.get(feedback_id)
        if not feedback:
            await self.ctx.ui.send(room_id, t("admin_users_empty"))
            return
        await self.ctx.ui.send(
            feedback["room_id"],
            "\n\n".join([t("feedback_answer_header", feedback_id=feedback_id), text]),
        )
        await self.ctx.repos.feedback.mark_answered(feedback_id)
        await self.ctx.ui.send(room_id, t("admin_answer_sent", feedback_id=feedback_id))
