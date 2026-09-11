# -*- coding: utf-8 -*-
"""Запуск бота matrix-it-support-bot.

Пример: ``python3 main.py --config config.yaml``
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.bot import Bot
from src.config import ConfigError, load_config
from src.constants import APP_NAME, DEFAULT_CONFIG_FILE, VERSION
from src.logger import setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION}")
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG_FILE, help="путь к файлу config.yaml"
    )
    parser.add_argument("-v", "--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser.parse_args(argv)


async def _run(config) -> None:
    bot = Bot(config)
    try:
        await bot.run()
    except asyncio.CancelledError:
        logging.getLogger("bot").info("Получен сигнал остановки")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config)
    logger.info("Старт %s %s с конфигом %s", APP_NAME, VERSION, args.config)
    logger.debug("Конфигурация (секреты скрыты): %s", config.safe_dump())

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        logger.warning("Остановка по Ctrl+C")
    except Exception:
        logger.exception("Критический сбой при запуске бота")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
