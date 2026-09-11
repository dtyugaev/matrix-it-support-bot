# -*- coding: utf-8 -*-
"""Асинхронная обёртка над синхронными драйверами sqlite3 / psycopg2.

Драйверы синхронные, поэтому все запросы выполняются в отдельном потоке через
``asyncio.to_thread`` под общей блокировкой — это исключает блокировки БД,
из-за которых в эталонном проекте приходилось открывать соединение на каждый
запрос. Подключение выполняется с повторными попытками и backoff, чтобы
недоступность БД не роняла основной цикл бота.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import Any, Iterable, Sequence

from src.db import schema

logger = logging.getLogger(__name__)

Row = dict[str, Any]


class Database:
    """Единая точка доступа к БД (sqlite или postgresql)."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self.dialect: str = str(config.get("database.type", "sqlite")).lower()
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._max_retries = int(config.get("database.max_retries", 5))
        self._backoff = float(config.get("database.retry_backoff", 2.0))

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------
    def _connect_sqlite(self) -> Any:
        path = self._config.path_of("database.sqlite.path", "storage/bot.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Установлено соединение с SQLite: %s", path)
        return conn

    def _connect_postgres(self) -> Any:
        import psycopg2  # локальный импорт: нужен только для postgresql
        import psycopg2.extras

        section = self._config.section("database")["postgresql"]
        conn = psycopg2.connect(
            host=section.get("host", "localhost"),
            port=int(section.get("port", 5432)),
            dbname=section.get("dbname"),
            user=section.get("user"),
            password=section.get("password"),
            connect_timeout=int(section.get("connect_timeout", 10)),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        conn.autocommit = True
        logger.info(
            "Установлено соединение с PostgreSQL: %s:%s/%s",
            section.get("host"),
            section.get("port"),
            section.get("dbname"),
        )
        return conn

    def _connect(self) -> Any:
        if self.dialect == schema.SQLITE:
            return self._connect_sqlite()
        if self.dialect == schema.POSTGRESQL:
            return self._connect_postgres()
        raise ValueError(f"Неподдерживаемый тип БД: {self.dialect}")

    async def connect(self) -> None:
        """Подключиться к БД с повторными попытками и backoff."""
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                self._conn = await asyncio.to_thread(self._connect)
                return
            except Exception as exc:  # noqa: BLE001 - логируем и повторяем
                last_error = exc
                logger.error(
                    "Не удалось подключиться к БД (попытка %s/%s): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                await asyncio.sleep(delay)
                delay *= self._backoff
        raise RuntimeError(f"Не удалось подключиться к БД: {last_error}")

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def init_schema(self) -> None:
        """Создать таблицы и индексы, если их ещё нет."""
        for statement in schema.ddl(self.dialect):
            await self.execute(statement)
        logger.info("Схема БД проверена/создана (%s)", self.dialect)

    # ------------------------------------------------------------------
    # Выполнение запросов
    # ------------------------------------------------------------------
    def _adapt(self, sql: str) -> str:
        """Преобразовать placeholders ``?`` в ``%s`` для psycopg2."""
        if self.dialect == schema.POSTGRESQL:
            return sql.replace("?", "%s")
        return sql

    def _run(self, sql: str, params: Sequence[Any], mode: str) -> Any:
        cursor = self._conn.cursor()
        try:
            cursor.execute(self._adapt(sql), tuple(params))
            if mode == "one":
                row = cursor.fetchone()
                return dict(row) if row is not None else None
            if mode == "all":
                return [dict(row) for row in cursor.fetchall()]
            if self.dialect == schema.SQLITE:
                self._conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()

    async def _guarded(self, sql: str, params: Sequence[Any], mode: str) -> Any:
        if self._conn is None:
            await self.connect()
        async with self._lock:
            try:
                return await asyncio.to_thread(self._run, sql, params, mode)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка выполнения запроса к БД: %s | SQL: %s", exc, sql.strip()[:200])
                await self._reconnect()
                return await asyncio.to_thread(self._run, sql, params, mode)

    async def _reconnect(self) -> None:
        logger.warning("Переподключаемся к БД")
        try:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
        except Exception:  # noqa: BLE001
            logger.debug("Соединение уже закрыто", exc_info=True)
        self._conn = None
        await self.connect()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Выполнить INSERT/UPDATE/DELETE/DDL, вернуть число затронутых строк."""
        return await self._guarded(sql, list(params), "exec")

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Row | None:
        """Вернуть одну строку в виде словаря или ``None``."""
        return await self._guarded(sql, list(params), "one")

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Row]:
        """Вернуть все строки в виде списка словарей."""
        return await self._guarded(sql, list(params), "all")
