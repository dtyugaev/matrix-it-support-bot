# -*- coding: utf-8 -*-
"""Загрузка и валидация конфигурации бота из YAML-файла.

PyYAML импортируется лениво (внутри :func:`load_config`), чтобы класс
:class:`Config` можно было использовать в юнит-тестах без внешних зависимостей.
Секреты (токены, пароли) никогда не логируются: см. :meth:`Config.safe_dump`.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Iterable

from src import constants

#: Значения по умолчанию. Пользовательский config.yaml накладывается сверху.
DEFAULTS: dict[str, Any] = {
    "matrix": {
        "homeserver": "",
        "user_id": "",
        "access_token": "",
        "device_id": "IT_Support_bot_device",
        "admin_access_token": "",
        "auto_join": True,
        "sync_timeout": 30000,
        "encryption": False,
        "nio_store_path": "storage/nio_store",
        "request_timeout": 30,
        "max_retries": 5,
        "retry_backoff": 2.0,
    },
    "database": {
        "type": "postgresql",
        "sqlite": {"path": "storage/bot.db"},
        "postgresql": {
            "host": "localhost",
            "port": 5432,
            "dbname": "matrix_it_support_bot",
            "user": "bot",
            "password": "",
            "connect_timeout": 10,
        },
    },
    "logging": {
        "level": "INFO",
        "file_path": "storage/matrix-it-support-bot.log",
        "console": False,
        "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        "date_format": "%Y-%m-%d %H:%M:%S",
        "quiet_loggers": ["nio", "urllib3", "asyncio", "aiohttp"],
    },
    "jira": {
        "url": "",
        "login": "",
        "password": "",
        "verify_ssl": False,
        "project": "IT",
        "issue_type_id": "10000",
        "portal_url": "",
        "status_listener_url": "",
        "poll_interval_seconds": 60,
        "poll_lookback_minutes": 15,
        "issue_types_for_list": ["10017", "10005", "10002", "10901"],
        "issues_per_page": 5,
        "issues_max_pages": 10,
        "comments_per_issue": 3,
        "comments_sort_order": "desc",
        "always_allow_comment": True,
        "request_timeout": 30,
        "max_retries": 4,
        "retry_backoff": 2.0,
        "subject_max_length": 255,
        "delete_processed_events": True,
        "fallback_poll_enabled": True,
        "notify_all_status_changes": False,
        "attachments": {
            "dir": "storage/attachments",
            "zip_filename_format": "%(issue_key)s_%Y-%m-%d_%H-%M-%S.zip",
            "max_size": 30,
            "attachment_ttl": 604800,
            "cleanup_interval": 300,
            "delete_from_room": True,
            "delete_from_bot_dir": True,
            "delete_from_synapse": True,
        },
    },
    "menu": {
        "menu_items_as_list": True,
        "help_commands_as_list": True,
        "markup": "markdown",
        "title_bold": True,
        "show_portal_link": True,
    },
    "administration": {
        "admin_room_id": "",
        "admins": [],
        "support_email": "it@otr.ru",
        "notify_events": {
            constants.ADMIN_EVENT_BOT_STARTED: True,
            constants.ADMIN_EVENT_BOT_STOPPED: True,
            constants.ADMIN_EVENT_USER_REGISTERED: True,
            constants.ADMIN_EVENT_USER_DELETED: True,
            constants.ADMIN_EVENT_PROFILE_NOT_FOUND: True,
            constants.ADMIN_EVENT_ISSUE_CREATED: False,
            constants.ADMIN_EVENT_FEEDBACK: True,
            constants.ADMIN_EVENT_JIRA_ERROR: True,
            constants.ADMIN_EVENT_MATRIX_ERROR: True,
            constants.ADMIN_EVENT_ATTACHMENT_CLEANUP_ERROR: True,
            constants.ADMIN_EVENT_ROOM_HIJACK_ATTEMPT: False,
        },
    },
}

#: Обязательные параметры: без них бот не запускается.
REQUIRED_KEYS: tuple[str, ...] = (
    "matrix.homeserver",
    "matrix.user_id",
    "matrix.access_token",
    "jira.url",
    "jira.login",
    "jira.password",
    "jira.project",
)

SECRET_KEYS: frozenset[str] = frozenset(
    {"access_token", "admin_access_token", "password", "token"}
)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно наложить ``override`` на копию ``base``."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigError(Exception):
    """Ошибка конфигурации (отсутствует файл или обязательный параметр)."""


class Config:
    """Обёртка над словарём конфигурации с доступом по точечному пути."""

    def __init__(self, data: dict[str, Any] | None = None, path: str | None = None) -> None:
        self._data = deep_merge(DEFAULTS, data or {})
        self.path = path
        self.base_dir = constants.BASE_DIR

    # -- доступ -----------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        """Получить значение по пути вида ``jira.attachments.max_size``."""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, dotted: str) -> Any:
        value = self.get(dotted, _MISSING)
        if value is _MISSING:
            raise KeyError(dotted)
        return value

    def section(self, name: str) -> dict[str, Any]:
        """Вернуть копию секции конфигурации."""
        return copy.deepcopy(self.get(name, {}))

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def path_of(self, dotted: str, default: str = "") -> str:
        """Абсолютный путь для файловых параметров (относительно корня проекта)."""
        raw = str(self.get(dotted, default) or default)
        if not raw:
            return raw
        return raw if os.path.isabs(raw) else os.path.join(self.base_dir, raw)

    # -- валидация --------------------------------------------------------
    def validate(self, required: Iterable[str] = REQUIRED_KEYS) -> None:
        """Проверить наличие обязательных параметров и корректность значений."""
        missing = [key for key in required if not self.get(key)]
        if missing:
            raise ConfigError(
                "В конфигурации не заданы обязательные параметры: " + ", ".join(missing)
            )

        db_type = str(self.get("database.type", "")).lower()
        if db_type not in ("sqlite", "postgresql"):
            raise ConfigError(
                f"database.type должен быть 'sqlite' или 'postgresql', получено: {db_type!r}"
            )
        if db_type == "postgresql" and not self.get("database.postgresql.dbname"):
            raise ConfigError("Для database.type=postgresql требуется database.postgresql.dbname")

        markup = str(self.get("menu.markup", "markdown")).lower()
        if markup not in ("markdown", "html", "plain"):
            raise ConfigError("menu.markup должен быть markdown, html или plain")

        if int(self.get("jira.issues_per_page", 5)) < 1:
            raise ConfigError("jira.issues_per_page должен быть больше нуля")
        if int(self.get("jira.poll_interval_seconds", 60)) < 5:
            raise ConfigError("jira.poll_interval_seconds не может быть меньше 5 секунд")
        if str(self.get("jira.comments_sort_order", "desc")).lower() not in ("asc", "desc"):
            raise ConfigError("jira.comments_sort_order должен быть asc или desc")

        user_id = str(self.get("matrix.user_id", ""))
        if not user_id.startswith("@") or ":" not in user_id:
            raise ConfigError("matrix.user_id должен иметь вид @localpart:server")

    # -- безопасный вывод -------------------------------------------------
    def safe_dump(self) -> dict[str, Any]:
        """Копия конфигурации, в которой секреты заменены на ``***``."""

        def _mask(node: Any) -> Any:
            if isinstance(node, dict):
                return {
                    key: ("***" if key in SECRET_KEYS and value else _mask(value))
                    for key, value in node.items()
                }
            if isinstance(node, list):
                return [_mask(item) for item in node]
            return node

        return _mask(self.as_dict())


class _Missing:
    pass


_MISSING = _Missing()


def load_config(path: str | None = None) -> Config:
    """Прочитать YAML-файл конфигурации и вернуть валидный :class:`Config`."""
    import yaml  # локальный импорт: нужен только при реальном запуске бота

    config_path = path or constants.DEFAULT_CONFIG_FILE
    if not os.path.isfile(config_path):
        raise ConfigError(
            f"Не найден файл конфигурации: {config_path}. "
            "Скопируйте config.yaml.example в config.yaml и заполните параметры."
        )
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Некорректная структура файла конфигурации: {config_path}")

    config = Config(raw, path=config_path)
    config.validate()
    return config
