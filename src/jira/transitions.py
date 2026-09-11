# -*- coding: utf-8 -*-
"""Статусы Jira, переходы и матрица доступности действий по заявке.

Значения перенесены из эталонного Telegram-бота:

* карта ``statusId -> имя статуса`` — из ``service/monitoring_issue/monitoring.py``;
* карта переходов — из ``core/core_jira/api.py``
  (``GetInstance.complete_switch_statused``);
* правила доступности действий — из
  ``modules/private/find_issue/keyboards.py::kb_processing_issue``.

Модуль не имеет внешних зависимостей и полностью покрыт юнит-тестами.
"""
from __future__ import annotations

from typing import Final

from src.constants import (
    ACTION_ATTACHMENTS,
    ACTION_COMMENT,
    ACTION_CONFIRM,
    ACTION_GIVE_INFO,
    ACTION_REOPEN,
)

# ---------------------------------------------------------------------------
# Статусы Jira проекта IT
# ---------------------------------------------------------------------------
STATUS_POSTPONED: Final[str] = "Отложено"
STATUS_INFORMATION_REQUEST: Final[str] = "Запрос информации"
STATUS_SPAM: Final[str] = "Спам"
STATUS_IN_WORK: Final[str] = "В работе"
STATUS_CLOSED: Final[str] = "Закрыто"
STATUS_REGISTERED: Final[str] = "Зарегистрировано"
STATUS_WAIT_SOLUTION: Final[str] = "Ожидает решения"
STATUS_ANSWER_PROVIDED: Final[str] = "Ответ предоставлен"
STATUS_SOLUTION_PROPOSED: Final[str] = "Предложено решение"
STATUS_REOPENED: Final[str] = "Переоткрыто"
STATUS_SOLUTION_CONFIRMATION: Final[str] = "Подтверждение решения"
STATUS_APPROVAL_FZ: Final[str] = "Согласование ФЗ"
STATUS_THIRD_LINE: Final[str] = "Передано на 3 линию"
STATUS_APPROVAL_UIB: Final[str] = "Согласование УИБ"
STATUS_COMMENTED_UIB: Final[str] = "Прокомментировано УИБ"
STATUS_OPENED: Final[str] = "Открыта"

#: Соответствие идентификаторов статусов Jira их названиям.
STATUS_BY_ID: Final[dict[str, str]] = {
    "10002": STATUS_POSTPONED,
    "10005": STATUS_INFORMATION_REQUEST,
    "10006": STATUS_SPAM,
    "10017": STATUS_IN_WORK,
    "10508": STATUS_CLOSED,
    "10900": STATUS_REGISTERED,
    "10901": STATUS_WAIT_SOLUTION,
    "10902": STATUS_ANSWER_PROVIDED,
    "10903": STATUS_SOLUTION_PROPOSED,
    "10904": STATUS_REOPENED,
    "12000": STATUS_APPROVAL_FZ,
    "14200": STATUS_THIRD_LINE,
    "14300": STATUS_APPROVAL_UIB,
    "14301": STATUS_COMMENTED_UIB,
}

#: Обратное соответствие «имя статуса -> id» (нужно для JQL-запросов).
ID_BY_STATUS: Final[dict[str, str]] = {name: key for key, name in STATUS_BY_ID.items()}

# ---------------------------------------------------------------------------
# Переходы Jira (имя перехода -> целевой статус и допустимые исходные статусы)
# ---------------------------------------------------------------------------
TRANSITIONS: Final[dict[str, dict[str, object]]] = {
    "Запросить информацию": {
        "new_status": STATUS_INFORMATION_REQUEST,
        "from_status": (STATUS_IN_WORK, STATUS_ANSWER_PROVIDED),
    },
    "Ответить": {
        "new_status": STATUS_ANSWER_PROVIDED,
        "from_status": (STATUS_INFORMATION_REQUEST,),
    },
    # Отдельный переход для бота: не отправляет собственное уведомление Jira,
    # поэтому эталонный бот использует именно его вместо «Ответить».
    "Ответ для робота": {
        "new_status": STATUS_ANSWER_PROVIDED,
        "from_status": (STATUS_INFORMATION_REQUEST,),
    },
    "Подтверждение решения": {
        "new_status": STATUS_INFORMATION_REQUEST,
        "from_status": (STATUS_IN_WORK,),
    },
    "Решение": {
        "new_status": STATUS_SOLUTION_CONFIRMATION,
        "from_status": (STATUS_IN_WORK, STATUS_ANSWER_PROVIDED),
    },
    "Переоткрыть": {
        "new_status": STATUS_REOPENED,
        "from_status": (STATUS_SOLUTION_CONFIRMATION, STATUS_CLOSED),
    },
    "Закрыть": {
        "new_status": STATUS_CLOSED,
        "from_status": (STATUS_SOLUTION_CONFIRMATION,),
    },
    "В работу": {
        "new_status": STATUS_IN_WORK,
        "from_status": (STATUS_REOPENED, STATUS_ANSWER_PROVIDED),
    },
}

#: Действие бота -> имя перехода Jira.
ACTION_TRANSITION: Final[dict[str, str]] = {
    ACTION_GIVE_INFO: "Ответ для робота",
    ACTION_REOPEN: "Переоткрыть",
    ACTION_CONFIRM: "Закрыть",
}

#: Статусы, в которых заявка считается «решение предложено» (ожидает реакции автора).
SOLUTION_STATUSES: Final[tuple[str, ...]] = (STATUS_SOLUTION_CONFIRMATION,)

#: Статусы, в которых эталонный бот запрещает обычный комментарий
#: (``always_allow_comment: false``).
COMMENT_FORBIDDEN_STATUSES: Final[tuple[str, ...]] = (
    STATUS_SOLUTION_CONFIRMATION,
    STATUS_CLOSED,
    STATUS_INFORMATION_REQUEST,
)


def available_actions(
    status: str,
    attachments_count: int = 0,
    always_allow_comment: bool = True,
) -> list[str]:
    """Вернуть список действий, доступных для заявки в статусе ``status``.

    :param status: текущий статус заявки в Jira.
    :param attachments_count: количество вложений заявки.
    :param always_allow_comment: значение ``jira.always_allow_comment`` из конфига.
    """
    actions: list[str] = []
    if status == STATUS_INFORMATION_REQUEST:
        actions.append(ACTION_GIVE_INFO)
    if status in SOLUTION_STATUSES:
        actions.append(ACTION_REOPEN)
        actions.append(ACTION_CONFIRM)
    if always_allow_comment or status not in COMMENT_FORBIDDEN_STATUSES:
        actions.append(ACTION_COMMENT)
    if attachments_count > 0:
        actions.append(ACTION_ATTACHMENTS)
    return actions


def is_action_available(
    action: str,
    status: str,
    attachments_count: int = 0,
    always_allow_comment: bool = True,
) -> bool:
    """Проверить доступность действия перед его выполнением (п. 5.2 ТЗ)."""
    return action in available_actions(status, attachments_count, always_allow_comment)


def transition_for(action: str) -> str:
    """Имя перехода Jira для действия бота."""
    try:
        return ACTION_TRANSITION[action]
    except KeyError as exc:
        raise ValueError(f"Для действия '{action}' не задан переход Jira") from exc


def allowed_from_statuses(transition_name: str) -> tuple[str, ...]:
    """Допустимые исходные статусы для перехода Jira."""
    meta = TRANSITIONS.get(transition_name)
    if not meta:
        raise ValueError(f"Неизвестный переход Jira: {transition_name}")
    return tuple(meta["from_status"])  # type: ignore[arg-type]


def target_status(transition_name: str) -> str:
    """Ожидаемый статус после перехода."""
    meta = TRANSITIONS.get(transition_name)
    if not meta:
        raise ValueError(f"Неизвестный переход Jira: {transition_name}")
    return str(meta["new_status"])
