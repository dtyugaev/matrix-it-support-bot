# -*- coding: utf-8 -*-
"""Константы проекта: версия, пути по умолчанию, ключи состояний и действий.

Модуль не импортирует сторонние библиотеки и может использоваться в тестах.
"""
from __future__ import annotations

import os
from typing import Final

VERSION: Final[str] = "1.0.0"
APP_NAME: Final[str] = "matrix-it-support-bot"

# Корень проекта (папка, в которой лежит main.py)
BASE_DIR: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CONFIG_FILE: Final[str] = os.path.join(BASE_DIR, "config.yaml")
DEFAULT_STORAGE_DIR: Final[str] = os.path.join(BASE_DIR, "storage")

# ---------------------------------------------------------------------------
# Состояния диалога (аналог FSM-состояний aiogram в эталонном проекте)
# ---------------------------------------------------------------------------
STATE_IDLE: Final[str] = "idle"
STATE_WAIT_SUBJECT: Final[str] = "wait_subject"
STATE_WAIT_DESCRIPTION: Final[str] = "wait_description"
STATE_WAIT_CONFIRM_CREATE: Final[str] = "wait_confirm_create"
STATE_WAIT_ISSUE_KEY: Final[str] = "wait_issue_key"
STATE_WAIT_COMMENT: Final[str] = "wait_comment"
STATE_WAIT_GIVE_INFO: Final[str] = "wait_give_info"
STATE_WAIT_REOPEN: Final[str] = "wait_reopen"
STATE_WAIT_FEEDBACK: Final[str] = "wait_feedback"
STATE_WAIT_ADMIN_ANSWER: Final[str] = "wait_admin_answer"
STATE_WAIT_BROADCAST_CONFIRM: Final[str] = "wait_broadcast_confirm"

# ---------------------------------------------------------------------------
# Действия по заявке (динамическое подменю, п. 5.2 ТЗ)
# ---------------------------------------------------------------------------
ACTION_GIVE_INFO: Final[str] = "give_info"
ACTION_REOPEN: Final[str] = "reopen"
ACTION_CONFIRM: Final[str] = "confirm"
ACTION_COMMENT: Final[str] = "comment"
ACTION_ATTACHMENTS: Final[str] = "attachments"

# ---------------------------------------------------------------------------
# Ключи событий, которые можно включать/выключать для комнаты администраторов
# ---------------------------------------------------------------------------
ADMIN_EVENT_BOT_STARTED: Final[str] = "bot_started"
ADMIN_EVENT_BOT_STOPPED: Final[str] = "bot_stopped"
ADMIN_EVENT_USER_REGISTERED: Final[str] = "user_registered"
ADMIN_EVENT_USER_DELETED: Final[str] = "user_deleted"
ADMIN_EVENT_PROFILE_NOT_FOUND: Final[str] = "profile_not_found"
ADMIN_EVENT_ISSUE_CREATED: Final[str] = "issue_created"
ADMIN_EVENT_FEEDBACK: Final[str] = "feedback"
ADMIN_EVENT_JIRA_ERROR: Final[str] = "jira_error"
ADMIN_EVENT_MATRIX_ERROR: Final[str] = "matrix_error"
ADMIN_EVENT_ATTACHMENT_CLEANUP_ERROR: Final[str] = "attachment_cleanup_error"
ADMIN_EVENT_ROOM_HIJACK_ATTEMPT: Final[str] = "room_hijack_attempt"

# Действия для журнала действий пользователей (таблица audit_log)
AUDIT_ACTIONS: Final[dict[str, str]] = {
    "start": "Регистрация в комнате",
    "menu": "Открыл меню",
    "create_issue": "Создание заявки",
    "open_issue": "Открыл заявку",
    "list_issues": "Список заявок",
    "switch_status": "Изменение статуса заявки",
    "add_comment": "Добавление комментария",
    "get_attachments": "Получение вложений",
    "feedback": "Обратная связь",
    "delete_profile": "Удаление профиля",
    "unknown_command": "Неизвестная команда",
}
