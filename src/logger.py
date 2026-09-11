# -*- coding: utf-8 -*-
"""Настройка логирования: уровни, формат с таймстампом, файл без ротации."""
from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.config import Config

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def setup_logging(config: "Config") -> logging.Logger:
    """Сконфигурировать корневой логгер по секции ``logging`` конфига.

    Ротация намеренно не используется (требование ТЗ): пишем в один файл,
    ротацию при необходимости выполняет системный logrotate.
    """
    level_name = str(config.get("logging.level", "INFO")).upper()
    if level_name not in LEVELS:
        level_name = "INFO"
    level = getattr(logging, level_name)

    formatter = logging.Formatter(
        fmt=str(config.get("logging.format")),
        datefmt=str(config.get("logging.date_format")),
    )

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_path = config.path_of("logging.file_path")
    if file_path:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if bool(config.get("logging.console", False)):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if not root.handlers:  # чтобы логи не терялись при пустой конфигурации
        root.addHandler(logging.StreamHandler(sys.stdout))

    for name in config.get("logging.quiet_loggers", []) or []:
        logging.getLogger(str(name)).setLevel(logging.WARNING)

    logger = logging.getLogger("bot")
    logger.info("Логирование настроено: уровень=%s, файл=%s", level_name, file_path or "-")
    return logger
