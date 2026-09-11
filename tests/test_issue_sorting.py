# -*- coding: utf-8 -*-
"""Тесты сортировки списка заявок и разбора дат Jira."""
from __future__ import annotations

import conftest  # noqa: F401

from src.jira import transitions as tr
from src.jira.client import sort_issues
from src.utils.dates import format_jira_datetime, parse_jira_datetime


def test_sorting_priority_matches_reference_logic():
    issues = [
        {"key": "IT-4", "status": tr.STATUS_CLOSED, "created": "2026-09-01T10:00:00.000+0300"},
        {"key": "IT-3", "status": tr.STATUS_IN_WORK, "created": "2026-08-01T10:00:00.000+0300"},
        {"key": "IT-2", "status": tr.STATUS_SOLUTION_CONFIRMATION, "created": "2026-07-01T10:00:00.000+0300"},
        {"key": "IT-1", "status": tr.STATUS_INFORMATION_REQUEST, "created": "2026-06-01T10:00:00.000+0300"},
    ]
    assert [issue["key"] for issue in sort_issues(issues)] == ["IT-1", "IT-2", "IT-3", "IT-4"]


def test_newest_first_inside_status_group():
    issues = [
        {"key": "OLD", "status": tr.STATUS_IN_WORK, "created": "2026-01-01T10:00:00.000+0300"},
        {"key": "NEW", "status": tr.STATUS_IN_WORK, "created": "2026-09-01T10:00:00.000+0300"},
    ]
    assert [issue["key"] for issue in sort_issues(issues)] == ["NEW", "OLD"]


def test_dates_are_formatted_for_user():
    assert format_jira_datetime("2026-07-10T15:11:51.000+0300") == "10.07.2026 15:11:51"
    assert format_jira_datetime("2026-07-10T15:11:51.000+0300", short=True) == "10.07.2026 15:11"
    assert parse_jira_datetime("некорректно") is None
    assert format_jira_datetime("") == ""
