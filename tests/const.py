"""Общие константы и фабрики ответов Tasklerq для тестов."""

from typing import Any

BASE_URL = "http://tasklerq.test"
HOUSEHOLD_URL = f"{BASE_URL}/api/v1/household"
SNAPSHOT_URL = f"{BASE_URL}/api/v1/snapshot"

HOUSEHOLD_ID = "019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a5b"
OTHER_HOUSEHOLD_ID = "019a1b2c-4a5b-7c6d-8e9f-0a1b2c3d4e5f"

LIST_ID = "019a1b2c-5b6c-7d8e-9f00-1a2b3c4d5e6f"
SHOPPING_LIST_ID = "019a1b2c-6c7d-7e8f-9a0b-2b3c4d5e6f70"
OTHER_LIST_ID = "019a1b2c-7d8e-7f90-8a1b-3c4d5e6f7081"

ITEM_ID = "019a1b2c-8e9f-7a0b-9c1d-4d5e6f708192"
SECOND_ITEM_ID = "019a1b2c-9f0a-7b1c-8d2e-5e6f708192a3"
THIRD_ITEM_ID = "019a1b2c-a0b1-7c2d-9e3f-6f708192a3b4"
FOURTH_ITEM_ID = "019a1b2c-b1c2-7d3e-8f40-708192a3b4c5"
FIFTH_ITEM_ID = "019a1b2c-c2d3-7e4f-8a51-8192a3b4c5d6"
SIXTH_ITEM_ID = "019a1b2c-d3e4-7f5a-9b62-92a3b4c5d6e7"

ASSIGNEE_ID = "019a1b2c-4b6c-7d7e-8f80-1a2b3c4d5e6f"
TAG_ID = "019a1b2c-aa11-7b22-8c33-445566778899"

TOKEN = "tlq_test"
NEW_TOKEN = "tlq_new"

SERVER_TIME = "2026-11-03T12:00:00Z"
NEXT_SINCE = "2026-11-03T11:59:55Z"


def household_payload(
    household_id: str = HOUSEHOLD_ID,
    name: str = "Семья",
    timezone: str = "Europe/Moscow",
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ответ GET /api/v1/household по примеру из ha-api.md."""
    return {
        "household": {
            "id": household_id,
            "name": name,
            "timezone": timezone,
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "deleted_at": None,
        },
        "members": [] if members is None else members,
    }


def member_payload(
    member_id: str = ASSIGNEE_ID,
    display_name: str = "Алексей",
) -> dict[str, Any]:
    """Активный участник household по примеру из ha-api.md."""
    return {
        "id": member_id,
        "household_id": HOUSEHOLD_ID,
        "telegram_user_id": 100000001,
        "telegram_chat_id": 100000001,
        "display_name": display_name,
        "username": "alexey",
        "role": "owner",
        "timezone": "Europe/Moscow",
        "settings": {},
        "created_at": "2026-10-01T08:00:00Z",
        "updated_at": "2026-10-01T08:00:00Z",
        "deleted_at": None,
    }


def list_payload(
    list_id: str = LIST_ID,
    *,
    kind: str = "tasks",
    name: str = "Задачи",
    is_default: bool = True,
    is_archived: bool = False,
    position: int = 0,
) -> dict[str, Any]:
    """Объект List по примеру из ha-api.md."""
    return {
        "id": list_id,
        "household_id": HOUSEHOLD_ID,
        "kind": kind,
        "name": name,
        "is_default": is_default,
        "is_archived": is_archived,
        "position": position,
        "created_at": "2026-10-01T08:00:00Z",
        "updated_at": "2026-10-01T08:00:00Z",
        "deleted_at": None,
    }


def item_payload(
    item_id: str = ITEM_ID,
    *,
    list_id: str = LIST_ID,
    title: str = "Оплатить интернет",
    status: str = "open",
    version: int = 1,
    due_date: str | None = "2026-11-05",
    due_at: str | None = None,
    notes: str | None = None,
    priority: str = "normal",
    assignee_user_id: str | None = None,
    source: str = "ha_todo",
    recurrence: dict[str, Any] | None = None,
    tags: list[dict[str, Any]] | None = None,
    position: int = 0,
    completed_at: str | None = None,
    updated_at: str = "2026-10-20T09:00:00Z",
) -> dict[str, Any]:
    """Объект ItemResponse по примеру из ha-api.md."""
    return {
        "id": item_id,
        "household_id": HOUSEHOLD_ID,
        "list_id": list_id,
        "title": title,
        "notes": notes,
        "status": status,
        "priority": priority,
        "due_at": due_at,
        "due_date": due_date,
        "assignee_user_id": assignee_user_id,
        "created_by_user_id": None,
        "source": source,
        "capture_id": None,
        "completed_at": completed_at,
        "completed_by_user_id": None,
        "quantity": None,
        "unit": None,
        "position": position,
        "version": version,
        "recurrence": recurrence,
        "created_at": "2026-10-20T09:00:00Z",
        "updated_at": updated_at,
        "deleted_at": None,
        "tags": [] if tags is None else tags,
        "subtasks": [],
        "subtask_progress": {"done": 0, "total": 0},
    }


def tag_payload(
    name: str = "дом",
    tag_id: str = TAG_ID,
) -> dict[str, Any]:
    """Тег позиции по примеру из ha-api.md."""  # noqa: RUF002
    return {
        "id": tag_id,
        "household_id": HOUSEHOLD_ID,
        "name": name,
        "created_at": "2026-10-20T09:00:00Z",
    }


def recurrence_payload(
    *,
    freq: str = "monthly",
    interval: int = 1,
    byweekday: list[int] | None = None,
    until: str | None = None,
    timezone: str = "Europe/Moscow",
    anchor_date: str = "2026-11-05",
    month_rule: str | None = "anchor_day",
) -> dict[str, Any]:
    """Правило повторения позиции по примеру из ha-api.md."""
    return {
        "freq": freq,
        "interval": interval,
        "byweekday": [] if byweekday is None else byweekday,
        "until": until,
        "timezone": timezone,
        "anchor_date": anchor_date,
        "month_rule": month_rule,
    }


def calendar_event_payload(
    item_id: str = ITEM_ID,
    *,
    list_id: str = LIST_ID,
    occurrence_key: str | None = None,
    title: str = "Оплатить интернет",
    status: str = "open",
) -> dict[str, Any]:
    """Объект CalendarEvent по примеру из ha-api.md."""
    return {
        "item_id": item_id,
        "list_id": list_id,
        "occurrence_key": occurrence_key or f"{item_id}:2026-11-05",
        "start": "2026-11-05",
        "end": "2026-11-06",
        "all_day": True,
        "title": title,
        "status": status,
    }


def snapshot_payload(
    *,
    lists: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    deleted_item_ids: list[str] | None = None,
    calendar_events: list[dict[str, Any]] | None = None,
    server_time: str = SERVER_TIME,
    next_since: str = NEXT_SINCE,
    calendar_truncated: bool = False,
) -> dict[str, Any]:
    """Ответ GET /api/v1/snapshot; None означает набор по умолчанию."""
    return {
        "server_time": server_time,
        "next_since": next_since,
        "lists": [list_payload()] if lists is None else lists,
        "items": [item_payload()] if items is None else items,
        "deleted_item_ids": [] if deleted_item_ids is None else deleted_item_ids,
        "calendar_events": (
            [calendar_event_payload()] if calendar_events is None else calendar_events
        ),
        "calendar_truncated": calendar_truncated,
    }
