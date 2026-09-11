# -*- coding: utf-8 -*-
"""Тесты имени zip-архива вложений и вспомогательных текстовых функций."""
from __future__ import annotations

import datetime as dt

import conftest  # noqa: F401

from src.services.attachments import render_zip_name
from src.utils.text import human_duration, split_message, truncate

MOMENT = dt.datetime(2026, 9, 11, 14, 8, 5)


def test_template_with_issue_key_and_strftime():
    assert render_zip_name("%(issue_key)s_%Y-%m-%d.zip", "IT-148764", MOMENT) == "IT-148764_2026-09-11.zip"


def test_template_without_s_suffix_supported():
    assert render_zip_name("%(issue_key)_%H-%M.zip", "IT-1", MOMENT) == "IT-1_14-08.zip"


def test_unsafe_characters_are_replaced():
    assert render_zip_name("%(issue_key)s_%H:%M:%S.zip", "IT-1", MOMENT) == "IT-1_14-08-05.zip"


def test_empty_template_falls_back_to_default():
    assert render_zip_name("", "IT-1", MOMENT) == "IT-1_2026-09-11_14-08-05.zip"


def test_zip_extension_is_enforced():
    assert render_zip_name("%(issue_key)s_%Y", "IT-1", MOMENT).endswith(".zip")


def test_human_duration():
    assert human_duration(604800) == "7 дн."
    assert human_duration(3660) == "1 ч. 1 мин."
    assert human_duration(45) == "45 сек."


def test_truncate_and_split():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 10) == "abc"
    assert split_message("aaa\nbbb\nccc", 8) == ["aaa\nbbb", "ccc"]
