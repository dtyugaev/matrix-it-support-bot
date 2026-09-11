# -*- coding: utf-8 -*-
"""Работа с Synapse Admin API (удаление медиа из хранилища сервера).

Приоритет по п. 4.4 ТЗ: сначала пробуем токен самого бота (если у аккаунта есть
права администратора сервера), при ошибке — fallback на отдельный
``matrix.admin_access_token`` из конфига.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_mxc(mxc_uri: str) -> tuple[str, str] | None:
    """Разобрать ``mxc://server/media_id`` на пару ``(server, media_id)``.

    >>> parse_mxc("mxc://otr.ru/AbCdEf123")
    ('otr.ru', 'AbCdEf123')
    >>> parse_mxc("https://otr.ru/AbCdEf123") is None
    True
    """
    if not mxc_uri or not mxc_uri.startswith("mxc://"):
        return None
    rest = mxc_uri[len("mxc://") :]
    if "/" not in rest:
        return None
    server, media_id = rest.split("/", 1)
    if not server or not media_id:
        return None
    return server, media_id


class SynapseAdminApi:
    """Минимальный клиент Synapse Admin API на aiohttp."""

    def __init__(self, config: Any) -> None:
        self._homeserver = str(config.get("matrix.homeserver", "")).rstrip("/")
        self._bot_token = str(config.get("matrix.access_token", ""))
        self._admin_token = str(config.get("matrix.admin_access_token", ""))
        self._timeout = int(config.get("matrix.request_timeout", 30))
        self._session: Any = None
        self.bot_is_admin: bool | None = None

    async def start(self) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout)
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _call(self, method: str, path: str, token: str) -> tuple[int, str]:
        if self._session is None:
            await self.start()
        url = f"{self._homeserver}{path}"
        async with self._session.request(
            method, url, headers={"Authorization": f"Bearer {token}"}
        ) as response:
            return response.status, await response.text()

    async def delete_media(self, mxc_uri: str) -> bool:
        """Удалить файл из медиа-хранилища Synapse.

        Возвращает ``True``, если удаление подтверждено сервером.
        """
        parsed = parse_mxc(mxc_uri)
        if not parsed:
            logger.warning("Некорректный mxc-адрес, удаление пропущено: %s", mxc_uri)
            return False
        server, media_id = parsed
        path = f"/_synapse/admin/v1/media/{server}/{media_id}"

        tokens: list[tuple[str, str]] = []
        if self._bot_token and self.bot_is_admin is not False:
            tokens.append(("токен бота", self._bot_token))
        if self._admin_token:
            tokens.append(("admin_access_token", self._admin_token))

        for label, token in tokens:
            try:
                status, body = await self._call("DELETE", path, token)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка обращения к Synapse Admin API (%s): %s", label, exc)
                continue
            if status < 300:
                if label == "токен бота":
                    self.bot_is_admin = True
                logger.info("Медиа %s удалено из Synapse (%s)", mxc_uri, label)
                return True
            if status in (401, 403):
                if label == "токен бота":
                    self.bot_is_admin = False
                logger.warning(
                    "Нет прав администратора сервера для удаления медиа (%s), пробуем fallback",
                    label,
                )
                continue
            logger.error(
                "Synapse Admin API вернул %s при удалении %s: %s", status, mxc_uri, body[:200]
            )
        logger.error("Не удалось удалить медиа %s из хранилища Synapse", mxc_uri)
        return False
