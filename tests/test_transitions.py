# -*- coding: utf-8 -*-
"""Тесты матрицы «статус заявки -> доступные действия» (п. 5.2 ТЗ)."""
from __future__ import annotations

import conftest  # noqa: F401  (добавляет корень проекта в sys.path)

from src.constants import (
    ACTION_ATTACHMENTS,
    ACTION_COMMENT,
    ACTION_CONFIRM,
    ACTION_GIVE_INFO,
    ACTION_REOPEN,
)
from src.jira import transitions as tr


def test_information_request_allows_give_info():
    actions = tr.available_actions(tr.STATUS_INFORMATION_REQUEST, attachments_count=0)
    assert ACTION_GIVE_INFO in actions
    assert ACTION_REOPEN not in actions
    assert ACTION_CONFIRM not in actions


def test_solution_confirmation_allows_reopen_and_confirm():
    actions = tr.available_actions(tr.STATUS_SOLUTION_CONFIRMATION)
    assert ACTION_REOPEN in actions
    assert ACTION_CONFIRM in actions
    assert ACTION_GIVE_INFO not in actions


def test_attachments_action_depends_on_attachments_count():
    assert ACTION_ATTACHMENTS not in tr.available_actions(tr.STATUS_IN_WORK, 0)
    assert ACTION_ATTACHMENTS in tr.available_actions(tr.STATUS_IN_WORK, 3)


def test_always_allow_comment_flag():
    # always_allow_comment = True — комментарий доступен даже в закрытой заявке
    assert ACTION_COMMENT in tr.available_actions(tr.STATUS_CLOSED, 0, True)
    # False — воспроизводим логику эталонного бота
    assert ACTION_COMMENT not in tr.available_actions(tr.STATUS_CLOSED, 0, False)
    assert ACTION_COMMENT not in tr.available_actions(tr.STATUS_SOLUTION_CONFIRMATION, 0, False)
    assert ACTION_COMMENT not in tr.available_actions(tr.STATUS_INFORMATION_REQUEST, 0, False)
    assert ACTION_COMMENT in tr.available_actions(tr.STATUS_IN_WORK, 0, False)


def test_is_action_available_guard():
    assert tr.is_action_available(ACTION_CONFIRM, tr.STATUS_SOLUTION_CONFIRMATION)
    assert not tr.is_action_available(ACTION_CONFIRM, tr.STATUS_IN_WORK)


def test_action_transition_mapping():
    assert tr.transition_for(ACTION_GIVE_INFO) == "Ответ для робота"
    assert tr.transition_for(ACTION_REOPEN) == "Переоткрыть"
    assert tr.transition_for(ACTION_CONFIRM) == "Закрыть"
    try:
        tr.transition_for(ACTION_COMMENT)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Для комментария перехода быть не должно")


def test_transition_metadata():
    assert tr.target_status("Закрыть") == tr.STATUS_CLOSED
    assert tr.STATUS_SOLUTION_CONFIRMATION in tr.allowed_from_statuses("Переоткрыть")
    assert tr.STATUS_INFORMATION_REQUEST in tr.allowed_from_statuses("Ответ для робота")


def test_status_ids_match_reference_plugin():
    assert tr.STATUS_BY_ID["10005"] == tr.STATUS_INFORMATION_REQUEST
    assert tr.STATUS_BY_ID["10017"] == tr.STATUS_IN_WORK
    assert tr.ID_BY_STATUS[tr.STATUS_CLOSED] == "10508"
