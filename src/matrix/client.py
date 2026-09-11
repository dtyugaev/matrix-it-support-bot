# -*- coding: utf-8 -*-
"""Обёртка над ``matrix-nio``: подключение по access_token, отправка сообщений,
реакции, удаление событий, загрузка и скачивание вложений.

Логин/пароль на сервере отключены (п. 1.1 ТЗ), поэтому клиент создаётся с уже
готовыми ``access_token``/``device_id``, а корректность токена проверяется
запросом ``whoami``.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from typing import Any

from src.matrix.formatting import render

logger = logging.getLogger(__name__)


class MatrixClientError(Exception):
    """Ошибка обращения к Synapse."""


class MatrixClient:
    """Тонкий фасад над ``nio.AsyncClient`` с повторными попытками."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self.homeserver = str(config.get("matrix.homeserver", "")).rstrip("/")
        self.user_id = str(config.get("matrix.user_id", ""))
        self.device_id = str(config.get("matrix.device_id", "")) or "BOT"
        self._access_token = str(config.get("matrix.access_token", ""))
        self.markup = str(config.get("menu.markup", "markdown"))
        self._max_retries = int(config.get("matrix.max_retries", 5))
        self._backoff = float(config.get("matrix.retry_backoff", 2.0))
        self._timeout = int(config.get("matrix.request_timeout", 30))
        self.client: Any = None
        self.available = False

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------
    async def start(self) -> Any:
        """Создать nio-клиент и проверить access_token."""
        from nio import AsyncClient, AsyncClientConfig

        store_path = self._config.path_of("matrix.nio_store_path", "storage/nio_store")
        os.makedirs(store_path, exist_ok=True)

        client_config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=bool(self._config.get("matrix.encryption", False)),
            request_timeout=self._timeout,
            max_limit_exceeded=0,
            max_timeouts=0,
        )
        self.client = AsyncClient(
            homeserver=self.homeserver,
            user=self.user_id,
            device_id=self.device_id,
            store_path=store_path,
            config=client_config,
        )
        self.client.access_token = self._access_token
        self.client.user_id = self.user_id
        self.client.device_id = self.device_id

        if bool(self._config.get("matrix.encryption", False)):
            try:
                self.client.load_store()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось загрузить nio store: %s", exc)

        await self.whoami()
        return self.client

    async def whoami(self) -> bool:
        """Проверить авторизацию на Synapse. При ошибке — ERROR в лог."""
        from nio import WhoamiError

        try:
            response = await self.client.whoami()
        except Exception as exc:  # noqa: BLE001
            self.available = False
            logger.error("Не удалось обратиться к Synapse %s: %s", self.homeserver, exc)
            return False
        if isinstance(response, WhoamiError):
            self.available = False
            logger.error(
                "Авторизация в Synapse не выполнена: %s. Проверьте matrix.access_token",
                response.message,
            )
            return False
        self.available = True
        logger.info("Synapse %s: авторизация выполнена (%s)", self.homeserver, response.user_id)
        return True

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()

    # ------------------------------------------------------------------
    # Служебное: повторные попытки
    # ------------------------------------------------------------------
    async def _retry(self, description: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Вызвать метод nio с повторными попытками и backoff."""
        from nio import ErrorResponse

        delay = 1.0
        last_error: Any = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await func(*args, **kwargs)
                if isinstance(response, ErrorResponse):
                    last_error = response.message
                    if getattr(response, "status_code", "") in ("M_FORBIDDEN", "M_UNKNOWN_TOKEN"):
                        logger.error("Synapse отказал в операции '%s': %s", description, last_error)
                        return response
                    logger.warning(
                        "Операция '%s' не выполнена (попытка %s/%s): %s",
                        description,
                        attempt,
                        self._max_retries,
                        last_error,
                    )
                else:
                    self.available = True
                    return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Ошибка операции '%s' (попытка %s/%s): %s",
                    description,
                    attempt,
                    self._max_retries,
                    exc,
                )
            if attempt < self._max_retries:
                await asyncio.sleep(delay)
                delay *= self._backoff
        self.available = False
        logger.error("Операция '%s' не выполнена: %s", description, last_error)
        return None

    # ------------------------------------------------------------------
    # Сообщения и реакции
    # ------------------------------------------------------------------
    async def send_text(self, room_id: str, text: str, notice: bool = False) -> str | None:
        """Отправить сообщение с Markdown/HTML-разметкой, вернуть ``event_id``."""
        body, formatted = render(text, self.markup)
        content: dict[str, Any] = {
            "msgtype": "m.notice" if notice else "m.text",
            "body": body,
        }
        if formatted:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = formatted

        response = await self._retry(
            f"send_text -> {room_id}",
            self.client.room_send,
            room_id=room_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )
        return getattr(response, "event_id", None)

    async def react(self, room_id: str, event_id: str, emoji: str) -> str | None:
        """Поставить реакцию ``m.reaction`` на сообщение."""
        content = {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": event_id,
                "key": emoji,
            }
        }
        response = await self._retry(
            f"react {emoji} -> {event_id}",
            self.client.room_send,
            room_id=room_id,
            message_type="m.reaction",
            content=content,
            ignore_unverified_devices=True,
        )
        return getattr(response, "event_id", None)

    async def react_many(self, room_id: str, event_id: str, emojis: list[str]) -> None:
        """Проставить заранее все реакции меню (п. 2.1 ТЗ)."""
        for emoji in emojis:
            await self.react(room_id, event_id, emoji)

    async def redact(self, room_id: str, event_id: str, reason: str = "") -> bool:
        """Удалить (redact) событие — используется для очистки вложений."""
        response = await self._retry(
            f"redact {event_id}", self.client.room_redact, room_id, event_id, reason=reason
        )
        return response is not None

    # ------------------------------------------------------------------
    # Вложения
    # ------------------------------------------------------------------
    async def upload_file(self, file_path: str, filename: str | None = None) -> tuple[str | None, int]:
        """Загрузить файл в медиа-хранилище Synapse, вернуть ``(mxc_uri, размер)``."""
        import aiofiles

        filename = filename or os.path.basename(file_path)
        size = os.path.getsize(file_path)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        async with aiofiles.open(file_path, "rb") as handle:
            response = await self._retry(
                f"upload {filename}",
                self.client.upload,
                lambda *args: handle,
                content_type=mime,
                filename=filename,
                filesize=size,
            )
        if response is None:
            return None, size
        upload = response[0] if isinstance(response, tuple) else response
        return getattr(upload, "content_uri", None), size

    async def send_file(
        self, room_id: str, mxc_uri: str, filename: str, size: int, body: str | None = None
    ) -> str | None:
        """Отправить в комнату файл (msgtype ``m.file``)."""
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        content = {
            "msgtype": "m.file",
            "body": body or filename,
            "filename": filename,
            "url": mxc_uri,
            "info": {"size": size, "mimetype": mime},
        }
        response = await self._retry(
            f"send_file {filename} -> {room_id}",
            self.client.room_send,
            room_id=room_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )
        return getattr(response, "event_id", None)

    async def download_media(self, mxc_uri: str) -> tuple[bytes | None, str | None]:
        """Скачать вложение пользователя из Synapse по ``mxc://``."""
        from src.matrix.admin_api import parse_mxc

        parsed = parse_mxc(mxc_uri)
        if not parsed:
            return None, None
        server, media_id = parsed
        response = await self._retry(
            f"download {mxc_uri}", self.client.download, server_name=server, media_id=media_id
        )
        if response is None:
            return None, None
        return getattr(response, "body", None), getattr(response, "filename", None)

    # ------------------------------------------------------------------
    # Комнаты и пользователи
    # ------------------------------------------------------------------
    async def join(self, room_id: str) -> bool:
        response = await self._retry(f"join {room_id}", self.client.join, room_id)
        return response is not None

    async def leave(self, room_id: str) -> bool:
        response = await self._retry(f"leave {room_id}", self.client.room_leave, room_id)
        return response is not None

    async def forget(self, room_id: str) -> bool:
        """Забыть комнату (п. 1.4 ТЗ)."""
        response = await self._retry(f"forget {room_id}", self.client.room_forget, room_id)
        return response is not None

    async def displayname(self, user_id: str) -> str:
        """Отображаемое имя пользователя из профиля Matrix."""
        response = await self._retry(f"displayname {user_id}", self.client.get_displayname, user_id)
        name = getattr(response, "displayname", None)
        return name or user_id
