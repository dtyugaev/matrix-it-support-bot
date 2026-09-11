# -*- coding: utf-8 -*-
"""Работа с вложениями: сборка zip из вложений Jira, отправка в комнату и
автоматическая очистка по TTL (п. 4.4 и 5.2 ТЗ).

Очистка выполняется фоновой задачей каждые ``cleanup_interval`` секунд и для
каждого просроченного вложения делает три шага (каждый включается отдельно):

1. ``delete_from_room`` — redact события с файлом в комнате Matrix;
2. ``delete_from_bot_dir`` — удаление локального файла из папки бота;
3. ``delete_from_synapse`` — удаление файла из медиа-хранилища через Admin API.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import zipfile
from typing import Any, Sequence

from src.constants import ADMIN_EVENT_ATTACHMENT_CLEANUP_ERROR
from src.db.repository import Repositories
from src.matrix.admin_api import SynapseAdminApi
from src.matrix.client import MatrixClient
from src.texts import t
from src.utils.text import human_duration

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r"[\\/:*?\"<>|]")


def render_zip_name(template: str, issue_key: str, moment: dt.datetime | None = None) -> str:
    """Собрать имя zip-архива по шаблону из конфига.

    Поддерживаются подстановки ``%(issue_key)s`` / ``%(issue_key)`` и любые
    директивы ``strftime``. Небезопасные для файловой системы символы
    заменяются на дефис.

    >>> render_zip_name("%(issue_key)s_%Y.zip", "IT-1", dt.datetime(2026, 9, 11))
    'IT-1_2026.zip'
    """
    moment = moment or dt.datetime.now()
    template = template or "%(issue_key)s_%Y-%m-%d_%H-%M-%S.zip"
    with_key = template.replace("%(issue_key)s", issue_key).replace("%(issue_key)", issue_key)
    rendered = moment.strftime(with_key)
    safe = _UNSAFE_CHARS.sub("-", rendered)
    if not safe.lower().endswith(".zip"):
        safe += ".zip"
    return safe


class AttachmentService:
    """Сборка, отправка и удаление вложений."""

    def __init__(
        self,
        config: Any,
        repos: Repositories,
        matrix: MatrixClient,
        jira: Any,
        admin_api: SynapseAdminApi,
        admin_room: Any = None,
    ) -> None:
        self._config = config
        self._repos = repos
        self._matrix = matrix
        self._jira = jira
        self._admin_api = admin_api
        self._admin_room = admin_room
        self.dir = config.path_of("jira.attachments.dir", "storage/attachments")
        self.max_size_mb = int(config.get("jira.attachments.max_size", 30))
        self.ttl = int(config.get("jira.attachments.attachment_ttl", 604800))
        self.cleanup_interval = int(config.get("jira.attachments.cleanup_interval", 300))
        self._template = str(config.get("jira.attachments.zip_filename_format", ""))
        os.makedirs(self.dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Загрузка вложений из Jira и сборка архива
    # ------------------------------------------------------------------
    async def build_issue_archive(
        self, issue_key: str, attachments: Sequence[dict[str, Any]]
    ) -> tuple[str | None, list[str]]:
        """Скачать все вложения заявки и упаковать их в один zip-архив.

        :returns: ``(путь_к_архиву, список_пропущенных_файлов)``.
        """
        if not attachments:
            return None, []

        limit_bytes = self.max_size_mb * 1024 * 1024
        skipped: list[str] = []
        downloaded: list[tuple[str, bytes]] = []

        for attachment in attachments:
            filename = attachment.get("filename") or "file"
            size = int(attachment.get("size") or 0)
            if limit_bytes and size > limit_bytes:
                logger.warning(
                    "Вложение %s заявки %s пропущено: размер %s байт больше лимита %s МБ",
                    filename,
                    issue_key,
                    size,
                    self.max_size_mb,
                )
                skipped.append(filename)
                continue
            try:
                content = await self._jira.download_attachment(attachment.get("content", ""))
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось скачать вложение %s (%s): %s", filename, issue_key, exc)
                skipped.append(filename)
                continue
            if content:
                downloaded.append((filename, content))

        if not downloaded:
            return None, skipped

        archive_name = render_zip_name(self._template, issue_key)
        archive_path = os.path.join(self.dir, archive_name)
        await asyncio.to_thread(self._write_zip, archive_path, downloaded)
        logger.info("Создан архив вложений %s (%s файлов)", archive_path, len(downloaded))
        return archive_path, skipped

    @staticmethod
    def _write_zip(archive_path: str, files: Sequence[tuple[str, bytes]]) -> None:
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            used: set[str] = set()
            for filename, content in files:
                name = filename
                counter = 1
                while name in used:  # исключаем коллизии имён внутри архива
                    stem, ext = os.path.splitext(filename)
                    name = f"{stem}_{counter}{ext}"
                    counter += 1
                used.add(name)
                archive.writestr(name, content)

    # ------------------------------------------------------------------
    # Отправка архива пользователю
    # ------------------------------------------------------------------
    async def send_archive(
        self, room_id: str, user_id: str, issue_key: str, archive_path: str
    ) -> bool:
        """Отправить архив в комнату и зарегистрировать его для автоочистки."""
        filename = os.path.basename(archive_path)
        mxc_uri, size = await self._matrix.upload_file(archive_path, filename)
        if not mxc_uri:
            logger.error("Не удалось загрузить архив %s в Synapse", filename)
            return False

        event_id = await self._matrix.send_file(
            room_id, mxc_uri, filename, size, body=f"{issue_key}: {filename}"
        )
        await self._repos.attachments.register(
            issue_key=issue_key,
            room_id=room_id,
            user_id=user_id,
            event_id=event_id,
            mxc_uri=mxc_uri,
            local_path=archive_path,
            filename=filename,
            ttl_seconds=self.ttl,
        )
        logger.info(
            "Архив %s отправлен в %s, будет удалён через %s", filename, room_id, human_duration(self.ttl)
        )
        return True

    # ------------------------------------------------------------------
    # Сохранение вложений пользователя (для Jira)
    # ------------------------------------------------------------------
    async def save_user_attachment(
        self, room_id: str, mxc_uri: str, filename: str
    ) -> str | None:
        """Скачать вложение пользователя из Synapse в папку бота."""
        import aiofiles

        content, _ = await self._matrix.download_media(mxc_uri)
        if not content:
            logger.error("Не удалось скачать вложение пользователя %s", mxc_uri)
            return None

        limit_bytes = self.max_size_mb * 1024 * 1024
        if limit_bytes and len(content) > limit_bytes:
            logger.warning("Вложение %s превышает лимит %s МБ", filename, self.max_size_mb)
            return None

        room_dir = os.path.join(self.dir, re.sub(r"[^\w.-]", "_", room_id))
        os.makedirs(room_dir, exist_ok=True)
        safe_name = _UNSAFE_CHARS.sub("-", filename or "attachment")
        path = os.path.join(room_dir, f"{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_name}")
        async with aiofiles.open(path, "wb") as handle:
            await handle.write(content)
        logger.info("Вложение пользователя сохранено: %s", path)
        return path

    @staticmethod
    def remove_local(path: str | None) -> None:
        """Удалить локальный файл, игнорируя отсутствие файла."""
        if not path:
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
                logger.debug("Удалён локальный файл %s", path)
        except OSError as exc:
            logger.error("Не удалось удалить файл %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Фоновая очистка по TTL
    # ------------------------------------------------------------------
    async def cleanup_once(self) -> int:
        """Обработать все просроченные вложения. Возвращает число очищенных."""
        expired = await self._repos.attachments.expired()
        if not expired:
            return 0

        delete_room = bool(self._config.get("jira.attachments.delete_from_room", True))
        delete_local = bool(self._config.get("jira.attachments.delete_from_bot_dir", True))
        delete_synapse = bool(self._config.get("jira.attachments.delete_from_synapse", True))

        processed = 0
        for row in expired:
            attachment_id = row.get("id")
            try:
                if delete_room and row.get("event_id"):
                    await self._matrix.redact(
                        row["room_id"], row["event_id"], reason="Срок хранения вложения истёк"
                    )
                if delete_local:
                    self.remove_local(row.get("local_path"))
                if delete_synapse and row.get("mxc_uri"):
                    await self._admin_api.delete_media(row["mxc_uri"])
                await self._repos.attachments.mark_deleted(int(attachment_id))
                processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка очистки вложения %s: %s", attachment_id, exc)
                if self._admin_room is not None:
                    await self._admin_room.notify(
                        ADMIN_EVENT_ATTACHMENT_CLEANUP_ERROR,
                        t(
                            "admin_attachment_cleanup_error",
                            attachment_id=attachment_id,
                            error=exc,
                        ),
                    )
        if processed:
            logger.info("Очистка вложений: обработано %s записей", processed)
        return processed

    async def cleanup_loop(self) -> None:
        """Бесконечный цикл сборщика мусора вложений."""
        logger.info(
            "Запущен сборщик вложений: интервал %s c, TTL %s",
            self.cleanup_interval,
            human_duration(self.ttl),
        )
        while True:
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Сбой в сборщике вложений — продолжаем работу")
            await asyncio.sleep(max(self.cleanup_interval, 10))
