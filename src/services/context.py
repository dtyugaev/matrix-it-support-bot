# -*- coding: utf-8 -*-
"""Контекст бота: все сервисы в одном объекте (передаётся в обработчики)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from src.db.repository import Repositories
from src.jira.client import JiraClient
from src.jira.status_listener import StatusListenerClient
from src.matrix.admin_api import SynapseAdminApi
from src.matrix.client import MatrixClient
from src.services.admin_room import AdminRoomService
from src.services.attachments import AttachmentService
from src.services.issues import IssueService
from src.services.ui import UiService
from src.services.users import UserService


@dataclass
class BotContext:
    """Ссылки на конфигурацию и сервисы бота."""

    config: Any
    repos: Repositories
    matrix: MatrixClient
    jira: JiraClient
    listener: StatusListenerClient
    admin_api: SynapseAdminApi
    ui: UiService
    users: UserService
    issues: IssueService
    attachments: AttachmentService
    admin_room: AdminRoomService
    started_at: dt.datetime = field(default_factory=dt.datetime.now)

    @property
    def support_email(self) -> str:
        return str(self.config.get("administration.support_email", ""))
