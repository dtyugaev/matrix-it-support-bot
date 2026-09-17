# -*- coding: utf-8 -*-
"""Текстовые меню с эмодзи-реакциями — замена inline-кнопок Telegram (п. 2 ТЗ).

Идея: бот отправляет сообщение с заголовком и пунктами, каждый пункт помечен
эмодзи. Затем бот сам проставляет все нужные реакции под этим сообщением.
Пользователь ставит свою реакцию на то же сообщение, бот слушает события
``m.reaction`` и по эмодзи определяет выбранный пункт.

Модуль чистый: не зависит ни от nio, ни от aiohttp, и покрыт тестами.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.constants import (
    ACTION_ATTACHMENTS,
    ACTION_COMMENT,
    ACTION_CONFIRM,
    ACTION_GIVE_INFO,
    ACTION_REOPEN,
)
from src.texts import t

# ---------------------------------------------------------------------------
# Идентификаторы меню
# ---------------------------------------------------------------------------
MENU_GUEST = "guest"
MENU_MAIN = "main"
MENU_STATUS = "status"
MENU_LIST = "list"
MENU_ISSUE_ACTIONS = "issue_actions"
MENU_PROFILE = "profile"
MENU_CANCEL = "cancel"
MENU_CONFIRM = "confirm"

# ---------------------------------------------------------------------------
# Эмодзи-маркеры пунктов
# ---------------------------------------------------------------------------
EMOJI_START = "🚀"
EMOJI_HELP = "❓"
EMOJI_FEEDBACK = "✉️"
EMOJI_CREATE = "📝"
EMOJI_STATUS = "🔍"
EMOJI_PROFILE = "👤"
EMOJI_LIST = "📋"
EMOJI_FIND = "🔢"
EMOJI_BACK = "⬅️"
EMOJI_PREV = "◀️"
EMOJI_NEXT = "▶️"
EMOJI_DELETE = "🗑️"
EMOJI_GIVE_INFO = "📝"
EMOJI_REOPEN = "🔁"
EMOJI_CONFIRM = "✅"
EMOJI_COMMENT = "🗨️"
EMOJI_ATTACHMENTS = "📎"
EMOJI_CANCEL = "❌"
EMOJI_YES = "✅"
EMOJI_NO = "❌"

#: Эмодзи-цифры для пунктов списка заявок.
NUMBER_EMOJI: tuple[str, ...] = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣")

#: Действие -> (эмодзи, ключ фразы) для динамического подменю заявки.
ISSUE_ACTION_ITEMS: dict[str, tuple[str, str]] = {
    ACTION_GIVE_INFO: (EMOJI_GIVE_INFO, "item_give_info"),
    ACTION_REOPEN: (EMOJI_REOPEN, "item_reopen"),
    ACTION_CONFIRM: (EMOJI_CONFIRM, "item_confirm"),
    ACTION_COMMENT: (EMOJI_COMMENT, "item_comment"),
    ACTION_ATTACHMENTS: (EMOJI_ATTACHMENTS, "item_attachments"),
}


@dataclass(frozen=True)
class MenuItem:
    """Пункт меню: эмодзи-маркер, подпись и действие с контекстом."""

    emoji: str
    label: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Menu:
    """Готовое к отправке меню."""

    menu_id: str
    title: str
    items: list[MenuItem]
    payload: dict[str, Any] = field(default_factory=dict)
    prefix_text: str = ""

    @property
    def emojis(self) -> list[str]:
        """Эмодзи в порядке пунктов, без повторов (реакция должна быть уникальной)."""
        seen: list[str] = []
        for item in self.items:
            if item.emoji not in seen:
                seen.append(item.emoji)
        return seen

    def action_map(self) -> dict[str, dict[str, Any]]:
        """Соответствие ``эмодзи -> {action, payload}`` для обработки m.reaction."""
        mapping: dict[str, dict[str, Any]] = {}
        for item in self.items:
            mapping.setdefault(item.emoji, {"action": item.action, "payload": item.payload})
        return mapping

    def render(self, as_list: bool = True, title_bold: bool = True) -> str:
        """Собрать текст меню в Markdown."""
        title = f"**{self.title}**" if title_bold else self.title
        lines = [title, "\n"]
        if self.prefix_text:
            lines.append("")
            lines.append(self.prefix_text)
            lines.append("")
        for item in self.items:
            bullet = "- " if as_list else ""
            lines.append(f"{bullet}{item.emoji} {item.label}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Конструкторы меню
# ---------------------------------------------------------------------------
def guest_menu() -> Menu:
    """Гостевое меню для незарегистрированного пользователя (п. 1.3 ТЗ)."""
    return Menu(
        MENU_GUEST,
        t("menu_title_guest"),
        [
            MenuItem(EMOJI_START, t("item_start"), "start"),
            MenuItem(EMOJI_HELP, t("item_help"), "help"),
            MenuItem(EMOJI_FEEDBACK, t("item_feedback"), "feedback"),
        ],
    )


def main_menu() -> Menu:
    """Главное меню зарегистрированного пользователя."""
    return Menu(
        MENU_MAIN,
        t("menu_title_main"),
        [
            MenuItem(EMOJI_CREATE, t("item_create_issue"), "create_issue"),
            MenuItem(EMOJI_STATUS, t("item_issue_status"), "issue_status"),
            MenuItem(EMOJI_PROFILE, t("item_profile"), "profile"),
            MenuItem(EMOJI_HELP, t("item_help"), "help"),
            MenuItem(EMOJI_FEEDBACK, t("item_feedback"), "feedback"),
        ],
    )


def status_menu() -> Menu:
    """Подменю «Узнать статус заявки» (п. 5.1 ТЗ)."""
    return Menu(
        MENU_STATUS,
        t("menu_title_status"),
        [
            MenuItem(EMOJI_LIST, t("item_list_issues"), "list_issues"),
            MenuItem(EMOJI_FIND, t("item_find_issue"), "find_issue"),
            MenuItem(EMOJI_BACK, t("item_back_to_main"), "main_menu"),
        ],
    )


def list_menu(
    issues: Sequence[dict[str, Any]],
    page: int,
    total_pages: int,
) -> Menu:
    """Страница списка заявок пользователя.

    :param issues: заявки текущей страницы (``key``, ``summary``, ``status``).
    :param page: номер страницы, начиная с 1.
    :param total_pages: всего страниц.
    """
    items: list[MenuItem] = []
    for index, issue in enumerate(issues[: len(NUMBER_EMOJI)]):
        label = t(
            "list_line",
            index="",
            issue_key=issue.get("key", ""),
            summary=issue.get("summary", ""),
            status=issue.get("status", ""),
        ).strip()
        items.append(
            MenuItem(
                NUMBER_EMOJI[index],
                label,
                "open_issue",
                {"issue_key": issue.get("key", ""), "page": page},
            )
        )
    if total_pages > 1:
        items.append(MenuItem(EMOJI_PREV, t("item_prev_page"), "list_page", {"page": page - 1}))
        items.append(MenuItem(EMOJI_NEXT, t("item_next_page"), "list_page", {"page": page + 1}))
    items.append(MenuItem(EMOJI_BACK, t("item_back_to_main"), "main_menu"))

    return Menu(
        MENU_LIST,
        t("menu_title_list", page=page, total=total_pages),
        items,
        payload={"page": page, "total_pages": total_pages},
    )


def issue_actions_menu(issue_key: str, actions: Iterable[str]) -> Menu:
    """Динамическое подменю действий по заявке (п. 5.2 ТЗ)."""
    items: list[MenuItem] = []
    for action in actions:
        marker = ISSUE_ACTION_ITEMS.get(action)
        if not marker:
            continue
        emoji, label_key = marker
        items.append(MenuItem(emoji, t(label_key), action, {"issue_key": issue_key}))
    items.append(MenuItem(EMOJI_BACK, t("item_back_to_main"), "main_menu"))
    return Menu(
        MENU_ISSUE_ACTIONS,
        t("menu_title_issue_actions", issue_key=issue_key),
        items,
        payload={"issue_key": issue_key},
    )


def profile_menu() -> Menu:
    """Меню «Мой профиль»."""
    return Menu(
        MENU_PROFILE,
        t("menu_title_profile"),
        [
            MenuItem(EMOJI_DELETE, t("item_delete_profile"), "delete_profile"),
            MenuItem(EMOJI_BACK, t("item_back_to_main"), "main_menu"),
        ],
    )


def cancel_menu() -> Menu:
    """Меню отмены ввода (одна реакция ❌)."""
    return Menu(
        MENU_CANCEL,
        t("menu_title_cancel"),
        [MenuItem(EMOJI_CANCEL, t("item_cancel"), "cancel")],
    )


def confirm_menu(action: str, payload: dict[str, Any] | None = None) -> Menu:
    """Меню подтверждения «Да/Нет» для действия ``action``."""
    data = dict(payload or {})
    data["confirm_action"] = action
    return Menu(
        MENU_CONFIRM,
        t("menu_title_cancel"),
        [
            MenuItem(EMOJI_YES, t("item_yes"), "confirm_yes", data),
            MenuItem(EMOJI_NO, t("item_no"), "confirm_no", data),
        ],
        payload=data,
    )


def paginate(items: Sequence[Any], per_page: int, max_pages: int) -> list[list[Any]]:
    """Разбить список на страницы с ограничением их количества.

    >>> paginate([1, 2, 3, 4, 5], 2, 2)
    [[1, 2], [3, 4]]
    """
    per_page = max(int(per_page), 1)
    pages = [list(items[index : index + per_page]) for index in range(0, len(items), per_page)]
    if max_pages > 0:
        pages = pages[:max_pages]
    return pages or [[]]
