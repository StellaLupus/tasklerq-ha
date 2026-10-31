"""Todo-сущности Tasklerq: списки, запись и атрибут tasklerq_items."""

from typing import Any

import pytest
from custom_components.tasklerq.api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqConflictError,
    TasklerqValidationError,
)
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from custom_components.tasklerq.coordinator import TasklerqCoordinator
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .const import (
    ASSIGNEE_ID,
    BASE_URL,
    HOUSEHOLD_ID,
    HOUSEHOLD_URL,
    ITEM_ID,
    LIST_ID,
    OTHER_LIST_ID,
    SECOND_ITEM_ID,
    SNAPSHOT_URL,
    TOKEN,
    household_payload,
    item_payload,
    list_payload,
    member_payload,
    recurrence_payload,
    snapshot_payload,
    tag_payload,
)

ITEMS_URL = f"{BASE_URL}/api/v1/lists/{LIST_ID}/items"
ITEM_URL = f"{BASE_URL}/api/v1/items/{ITEM_ID}"
SECOND_ITEM_URL = f"{BASE_URL}/api/v1/items/{SECOND_ITEM_ID}"
COMPLETE_URL = f"{ITEM_URL}/complete"
REOPEN_URL = f"{ITEM_URL}/reopen"

ARCHIVED_LIST_ID = "019a1b2c-0000-7000-8000-000000000099"
ARCHIVED_ITEM_ID = "019a1b2c-0001-7000-8000-000000000099"


def _coordinator(entry: MockConfigEntry) -> TasklerqCoordinator:
    """Вернуть координатор загруженной записи."""
    return entry.runtime_data.coordinator


def _calls(aioclient_mock: Any, method: str, path: str) -> list[Any]:
    """Запросы указанного метода и пути."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


def _entity_state(hass: HomeAssistant, entity_id: str) -> Any:
    """Состояние сущности и проверкой существования."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _registry_entry(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    """Запись реестра и проверкой существования."""
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return entry


async def _setup_entry(
    hass: HomeAssistant,
    aioclient_mock: Any,
    *,
    lists: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    members: list[dict[str, Any]] | None = None,
) -> MockConfigEntry:
    """Добавить и загрузить запись и household, и снапшот."""
    aioclient_mock.get(HOUSEHOLD_URL, json=household_payload(members=members))
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(lists=lists, items=items),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: TOKEN},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_todo_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _refresh_with(
    hass: HomeAssistant,
    aioclient_mock: Any,
    entry: MockConfigEntry,
    *,
    lists: list[dict[str, Any]],
    items: list[dict[str, Any]],
    members: list[dict[str, Any]] | None = None,
) -> None:
    """Подменить снапшот и выполнить refresh координатора."""
    aioclient_mock.clear_requests()
    if members is not None:
        aioclient_mock.get(HOUSEHOLD_URL, json=household_payload(members=members))
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(lists=lists, items=items),
    )
    await _coordinator(entry).async_refresh()
    await hass.async_block_till_done()


async def test_entities_follow_lists_and_entity_ids(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сущность на неархивированный список, todo.dacha и стабильный unique_id."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
            list_payload(
                ARCHIVED_LIST_ID,
                kind="checklist",
                name="Архив",
                is_default=False,
                is_archived=True,
                position=2,
            ),
        ],
        items=[
            item_payload(ITEM_ID, title="Оплатить интернет"),
            item_payload(
                SECOND_ITEM_ID,
                list_id=OTHER_LIST_ID,
                title="Купить лампочку",
            ),
            item_payload(
                ARCHIVED_ITEM_ID,
                list_id=ARCHIVED_LIST_ID,
                title="Скрытая задача",
            ),
        ],
    )

    state = _entity_state(hass, "todo.dacha")
    assert state.name == "Дача"
    assert state.attributes["tasklerq_items"][0]["title"] == "Купить лампочку"
    assert hass.states.get("todo.arhiv") is None
    assert all(
        item["title"] != "Скрытая задача"
        for item in _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"]
    )
    assert all(
        item["title"] != "Скрытая задача" for item in state.attributes["tasklerq_items"]
    )

    assert (
        _registry_entry(hass, "todo.dacha").unique_id
        == f"tasklerq:{HOUSEHOLD_ID}:{OTHER_LIST_ID}"
    )
    assert (
        _registry_entry(hass, "todo.zadachi").unique_id
        == f"tasklerq:{HOUSEHOLD_ID}:{LIST_ID}"
    )

    # Новый список появляется без перезапуска.
    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
            list_payload(
                "019a1b2c-0000-7000-8000-000000000098",
                kind="checklist",
                name="Гараж",
                is_default=False,
                position=2,
            ),
        ],
        items=[item_payload(ITEM_ID, title="Оплатить интернет")],
    )
    assert hass.states.get("todo.garazh") is not None


async def test_rename_keeps_unique_id_and_entity_id(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Переименование меняет имя, но сохраняет unique_id и entity_id."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
        ],
        items=[],
    )
    unique_id = _registry_entry(hass, "todo.dacha").unique_id

    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача 2",
                is_default=False,
                position=1,
            ),
        ],
        items=[],
    )

    state = _entity_state(hass, "todo.dacha")
    assert state.name == "Дача 2"
    assert _registry_entry(hass, "todo.dacha").unique_id == unique_id


async def test_archived_list_removes_entity(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Архивирование списка удаляет сущность из реестра и состояния."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
        ],
        items=[item_payload(ITEM_ID, list_id=OTHER_LIST_ID, title="Лампочка")],
    )
    assert hass.states.get("todo.dacha") is not None

    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                is_archived=True,
                position=1,
            ),
        ],
        items=[item_payload(ITEM_ID, list_id=OTHER_LIST_ID, title="Лампочка")],
    )

    assert hass.states.get("todo.dacha") is None
    assert er.async_get(hass).async_get("todo.dacha") is None


async def test_missing_list_removes_entity(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Исчезнувший из снапшота список удаляет сущность."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
        ],
        items=[],
    )
    assert hass.states.get("todo.dacha") is not None

    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[list_payload(LIST_ID)],
        items=[],
    )

    assert hass.states.get("todo.dacha") is None
    assert er.async_get(hass).async_get("todo.dacha") is None


async def test_archived_list_removed_from_registry_on_setup(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Архивный список удаляется из реестра при повторной загрузке."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        lists=[
            list_payload(LIST_ID),
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
                position=1,
            ),
        ],
        items=[],
    )
    assert er.async_get(hass).async_get("todo.dacha") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("todo.dacha") is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(HOUSEHOLD_URL, json=household_payload())
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            lists=[
                list_payload(LIST_ID),
                list_payload(
                    OTHER_LIST_ID,
                    kind="checklist",
                    name="Дача",
                    is_default=False,
                    is_archived=True,
                    position=1,
                ),
            ],
            items=[],
        ),
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("todo.dacha") is None
    assert er.async_get(hass).async_get("todo.dacha") is None


@pytest.mark.parametrize(
    ("service_data", "expected_due"),
    [
        ({"item": "Купить лампочку"}, {}),
        (
            {"item": "Купить лампочку", "due_date": "2026-11-10"},
            {"due_date": "2026-11-10"},
        ),
        (
            {"item": "Купить лампочку", "description": "В магазине"},  # noqa: RUF001
            {"notes": "В магазине"},  # noqa: RUF001
        ),
    ],
)
async def test_add_item_posts_native_fields(
    hass: HomeAssistant,
    aioclient_mock: Any,
    service_data: dict[str, Any],
    expected_due: dict[str, str],
) -> None:
    """todo.add_item отправляет title, source и заданные поля."""
    await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.post(
        ITEMS_URL,
        status=201,
        json=item_payload(title=service_data["item"]),
    )

    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": "todo.zadachi", **service_data},
        blocking=True,
    )

    calls = _calls(aioclient_mock, "post", f"/api/v1/lists/{LIST_ID}/items")
    assert len(calls) == 1
    body = calls[0][2]
    assert body["title"] == service_data["item"]
    assert body["source"] == "ha_todo"
    assert {key: body[key] for key in expected_due} == expected_due
    if "notes" not in expected_due:
        assert "notes" not in body
    if "due_date" not in expected_due:
        assert "due_at" not in body


async def test_add_item_posts_due_datetime(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """todo.add_item передаёт due_datetime как timezone-aware due_at."""
    await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.post(ITEMS_URL, status=201, json=item_payload())

    await hass.services.async_call(
        "todo",
        "add_item",
        {
            "entity_id": "todo.zadachi",
            "item": "Позвонить",
            "due_datetime": "2026-11-10 18:00:00+03:00",
        },
        blocking=True,
    )

    body = _calls(aioclient_mock, "post", f"/api/v1/lists/{LIST_ID}/items")[0][2]
    assert "due_date" not in body
    assert dt_util.parse_datetime(body["due_at"]) == dt_util.parse_datetime(
        "2026-11-10 18:00:00+03:00"
    )


async def test_update_item_patches_only_changed_title(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Смена названия — PATCH содержит version и только title."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="Оплатить интернет", version=2)],
    )
    aioclient_mock.patch(
        ITEM_URL,
        json=item_payload(ITEM_ID, title="Оплатить связь", version=3),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "rename": "Оплатить связь"},
        blocking=True,
    )

    calls = _calls(aioclient_mock, "patch", f"/api/v1/items/{ITEM_ID}")
    assert len(calls) == 1
    assert calls[0][2] == {"version": 2, "title": "Оплатить связь"}


@pytest.mark.parametrize(
    ("current", "expected_field"),
    [
        ({"due_date": "2026-11-05", "due_at": None}, "due_date"),
        ({"due_date": None, "due_at": "2026-11-05T15:00:00Z"}, "due_at"),
    ],
)
async def test_update_item_clears_due(
    hass: HomeAssistant,
    aioclient_mock: Any,
    current: dict[str, str | None],
    expected_field: str,
) -> None:
    """Очистка срока отправляет null только для установленного поля."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(
                ITEM_ID,
                version=2,
                due_date=current["due_date"],
                due_at=current["due_at"],
            )
        ],
    )
    aioclient_mock.patch(
        ITEM_URL,
        json=item_payload(ITEM_ID, version=3, due_date=None),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "due_date": None},
        blocking=True,
    )

    body = _calls(aioclient_mock, "patch", f"/api/v1/items/{ITEM_ID}")[0][2]
    assert body == {"version": 2, expected_field: None}


async def test_update_item_completes_and_reopens(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Завершение и возврат идут через complete/reopen и version."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, status="open", version=2)],
    )
    aioclient_mock.post(
        COMPLETE_URL,
        json=item_payload(ITEM_ID, status="done", version=3),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "status": "completed"},
        blocking=True,
    )

    calls = _calls(aioclient_mock, "post", f"/api/v1/items/{ITEM_ID}/complete")
    assert len(calls) == 1
    assert calls[0][2] == {"version": 2}
    assert _calls(aioclient_mock, "patch", f"/api/v1/items/{ITEM_ID}") == []

    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[list_payload(LIST_ID)],
        items=[item_payload(ITEM_ID, status="done", version=3)],
    )
    aioclient_mock.post(
        REOPEN_URL,
        json=item_payload(ITEM_ID, status="open", version=4),
    )
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "status": "needs_action"},
        blocking=True,
    )

    calls = _calls(aioclient_mock, "post", f"/api/v1/items/{ITEM_ID}/reopen")
    assert len(calls) == 1
    assert calls[0][2] == {"version": 3}


async def test_update_item_field_and_status_uses_patch_version(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """После PATCH статус использует version из ответа PATCH."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="Старое", status="open", version=2)],
    )
    aioclient_mock.patch(
        ITEM_URL,
        json=item_payload(ITEM_ID, title="Новое", version=3),
    )
    aioclient_mock.post(
        COMPLETE_URL,
        json=item_payload(ITEM_ID, title="Новое", status="done", version=4),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": "todo.zadachi",
            "item": ITEM_ID,
            "rename": "Новое",
            "status": "completed",
        },
        blocking=True,
    )

    assert _calls(aioclient_mock, "patch", f"/api/v1/items/{ITEM_ID}")[0][2] == {
        "version": 2,
        "title": "Новое",
    }
    complete = _calls(aioclient_mock, "post", f"/api/v1/items/{ITEM_ID}/complete")
    assert complete[0][2] == {"version": 3}


async def test_remove_item_deletes_each_uid(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """todo.remove_item вызывает DELETE для каждого uid."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(ITEM_ID, title="A"),
            item_payload(SECOND_ITEM_ID, title="B"),
        ],
    )
    aioclient_mock.delete(ITEM_URL, status=204)
    aioclient_mock.delete(SECOND_ITEM_URL, status=204)

    await hass.services.async_call(
        "todo",
        "remove_item",
        {"entity_id": "todo.zadachi", "item": [ITEM_ID, SECOND_ITEM_ID]},
        blocking=True,
    )

    assert _calls(aioclient_mock, "delete", f"/api/v1/items/{ITEM_ID}")
    assert _calls(aioclient_mock, "delete", f"/api/v1/items/{SECOND_ITEM_ID}")


async def test_write_requests_coordinator_refresh(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Успешная запись запрашивает refresh координатора."""
    await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.post(ITEMS_URL, status=201, json=item_payload())
    before = len(_calls(aioclient_mock, "get", "/api/v1/snapshot"))

    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": "todo.zadachi", "item": "Новая задача"},
        blocking=True,
    )
    await hass.async_block_till_done()

    after = len(_calls(aioclient_mock, "get", "/api/v1/snapshot"))
    assert after == before + 1


async def test_sequential_writes_use_refreshed_state(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Следующая запись видит состояние после предыдущего refresh."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, status="open", version=2)],
    )
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            lists=[list_payload(LIST_ID)],
            items=[item_payload(ITEM_ID, status="done", version=3)],
        ),
    )
    aioclient_mock.post(
        COMPLETE_URL,
        json=item_payload(ITEM_ID, status="done", version=3),
    )
    aioclient_mock.post(
        REOPEN_URL,
        json=item_payload(ITEM_ID, status="open", version=4),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "status": "completed"},
        blocking=True,
    )

    assert _coordinator(entry).data.items[ITEM_ID]["status"] == "done"

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "status": "needs_action"},
        blocking=True,
    )

    calls = _calls(aioclient_mock, "post", f"/api/v1/items/{ITEM_ID}/reopen")
    assert len(calls) == 1
    assert calls[0][2] == {"version": 3}


async def test_write_error_is_home_assistant_error_and_keeps_state(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Ошибка сервера не проглатывается и не меняет состояние сущности."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="Было")],
    )
    before = _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"]
    snapshot_calls = len(_calls(aioclient_mock, "get", "/api/v1/snapshot"))
    aioclient_mock.patch(
        ITEM_URL,
        status=500,
        json={"error": {"code": "internal_error", "message": "Boom"}},
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo",
            "update_item",
            {"entity_id": "todo.zadachi", "item": ITEM_ID, "rename": "Стало"},
            blocking=True,
        )

    assert _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"] == before
    assert len(_calls(aioclient_mock, "get", "/api/v1/snapshot")) == snapshot_calls


async def test_partial_update_failure_still_refreshes(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """PATCH прошёл, complete упал — координатор всё равно обновляется."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="Старое", status="open", version=2)],
    )
    aioclient_mock.patch(
        ITEM_URL,
        json=item_payload(ITEM_ID, title="Новое", version=3),
    )
    aioclient_mock.post(
        COMPLETE_URL,
        status=500,
        json={"error": {"code": "internal_error", "message": "Boom"}},
    )
    snapshot_calls = len(_calls(aioclient_mock, "get", "/api/v1/snapshot"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo",
            "update_item",
            {
                "entity_id": "todo.zadachi",
                "item": ITEM_ID,
                "rename": "Новое",
                "status": "completed",
            },
            blocking=True,
        )

    assert len(_calls(aioclient_mock, "get", "/api/v1/snapshot")) > snapshot_calls


async def test_partial_delete_failure_still_refreshes(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Первый DELETE прошёл, второй упал — координатор всё равно обновляется."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(ITEM_ID, title="A"),
            item_payload(SECOND_ITEM_ID, title="B"),
        ],
    )
    aioclient_mock.delete(ITEM_URL, status=204)
    aioclient_mock.delete(
        SECOND_ITEM_URL,
        status=500,
        json={"error": {"code": "internal_error", "message": "Boom"}},
    )
    snapshot_calls = len(_calls(aioclient_mock, "get", "/api/v1/snapshot"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo",
            "remove_item",
            {"entity_id": "todo.zadachi", "item": [ITEM_ID, SECOND_ITEM_ID]},
            blocking=True,
        )

    assert _calls(aioclient_mock, "delete", f"/api/v1/items/{ITEM_ID}")
    assert len(_calls(aioclient_mock, "get", "/api/v1/snapshot")) > snapshot_calls


@pytest.mark.parametrize(
    ("status", "error_class", "code", "message"),
    [
        (409, TasklerqConflictError, "conflict", "Task was modified"),
        (422, TasklerqValidationError, "validation_error", "Invalid title"),
    ],
)
async def test_write_error_keeps_code_and_details(
    hass: HomeAssistant,
    aioclient_mock: Any,
    status: int,
    error_class: type[TasklerqApiError],
    code: str,
    message: str,
) -> None:
    """409/422 сохраняют code, message и details из конверта."""
    await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.patch(
        ITEM_URL,
        status=status,
        json={
            "error": {
                "code": code,
                "message": message,
                "details": {"current_version": 4},
            }
        },
    )
    client = TasklerqApiClient(hass, BASE_URL, TOKEN)

    with pytest.raises(error_class) as raised:
        await client.async_update_item(ITEM_ID, version=2, fields={"title": "X"})

    assert raised.value.code == code
    assert raised.value.message == message
    assert raised.value.details == {"current_version": 4}


@pytest.mark.parametrize(
    ("status", "code", "fragment"),
    [
        (409, "conflict", "уже изменена"),
        (422, "validation_error", "отклонил изменения задачи"),
    ],
)
async def test_entity_write_error_has_russian_message(
    hass: HomeAssistant,
    aioclient_mock: Any,
    status: int,
    code: str,
    fragment: str,
) -> None:
    """Ошибки 409/422 на уровне сущности дают русский HomeAssistantError."""
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="Старое", version=2)],
    )
    aioclient_mock.patch(
        ITEM_URL,
        status=status,
        json={
            "error": {
                "code": code,
                "message": "Task was modified",
                "details": {"current_version": 4},
            }
        },
    )

    with pytest.raises(HomeAssistantError, match=fragment):
        await hass.services.async_call(
            "todo",
            "update_item",
            {"entity_id": "todo.zadachi", "item": ITEM_ID, "rename": "Новое"},
            blocking=True,
        )


async def test_tasklerq_items_contains_extra_fields(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """tasklerq_items содержит priority, tags, assignee, recurrence и source."""
    recurrence = recurrence_payload()
    await _setup_entry(
        hass,
        aioclient_mock,
        members=[member_payload(ASSIGNEE_ID, "Алексей")],
        items=[
            item_payload(
                ITEM_ID,
                title="Оплатить интернет",
                priority="high",
                assignee_user_id=ASSIGNEE_ID,
                source="telegram",
                recurrence=recurrence,
                tags=[tag_payload("дом")],
            )
        ],
    )

    items = _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"]
    assert items == [
        {
            "id": ITEM_ID,
            "title": "Оплатить интернет",
            "status": "open",
            "priority": "high",
            "due": "2026-11-05",
            "assignee": "Алексей",
            "tags": ["дом"],
            "recurrence": recurrence,
            "source": "telegram",
        }
    ]


async def test_tasklerq_items_survives_completion(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Завершение из HA не теряет дополнительные поля после снапшота."""
    recurrence = recurrence_payload()
    item = item_payload(
        ITEM_ID,
        title="Оплатить интернет",
        priority="high",
        assignee_user_id=ASSIGNEE_ID,
        source="telegram",
        recurrence=recurrence,
        tags=[tag_payload("дом")],
    )
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        members=[member_payload(ASSIGNEE_ID, "Алексей")],
        items=[item],
    )
    aioclient_mock.post(
        COMPLETE_URL,
        json=item_payload(
            ITEM_ID,
            status="done",
            version=2,
            priority="high",
            assignee_user_id=ASSIGNEE_ID,
            source="telegram",
            recurrence=recurrence,
            tags=[tag_payload("дом")],
        ),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "status": "completed"},
        blocking=True,
    )

    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        lists=[list_payload(LIST_ID)],
        items=[
            item_payload(
                ITEM_ID,
                status="done",
                version=2,
                priority="high",
                assignee_user_id=ASSIGNEE_ID,
                source="telegram",
                recurrence=recurrence,
                tags=[tag_payload("дом")],
            )
        ],
    )

    items = _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"]
    assert items[0]["status"] == "done"
    assert items[0]["priority"] == "high"
    assert items[0]["tags"] == ["дом"]
    assert items[0]["assignee"] == "Алексей"
    assert items[0]["recurrence"] == recurrence
    assert items[0]["source"] == "telegram"


async def test_native_rename_does_not_touch_extra_fields(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """PATCH при смене названия не отправляет и не обнуляет доп. поля."""
    recurrence = recurrence_payload()
    await _setup_entry(
        hass,
        aioclient_mock,
        members=[member_payload(ASSIGNEE_ID, "Алексей")],
        items=[
            item_payload(
                ITEM_ID,
                title="Старое",
                version=2,
                priority="high",
                assignee_user_id=ASSIGNEE_ID,
                recurrence=recurrence,
                tags=[tag_payload("дом")],
            )
        ],
    )
    aioclient_mock.patch(
        ITEM_URL,
        json=item_payload(
            ITEM_ID,
            title="Новое",
            version=3,
            priority="high",
            assignee_user_id=ASSIGNEE_ID,
            recurrence=recurrence,
            tags=[tag_payload("дом")],
        ),
    )

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.zadachi", "item": ITEM_ID, "rename": "Новое"},
        blocking=True,
    )

    body = _calls(aioclient_mock, "patch", f"/api/v1/items/{ITEM_ID}")[0][2]
    assert body == {"version": 2, "title": "Новое"}
    items = _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"]
    assert items[0]["priority"] == "high"
    assert items[0]["tags"] == ["дом"]
    assert items[0]["assignee"] == "Алексей"
    assert items[0]["recurrence"] == recurrence


async def test_full_sync_refreshes_member_names(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Новый участник получает имя после полной синхронизации."""
    freezer.move_to("2026-11-03T12:00:00+00:00")
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        members=[member_payload(ASSIGNEE_ID, "Алексей")],
        items=[item_payload(ITEM_ID, title="Задача", assignee_user_id=ASSIGNEE_ID)],
    )
    assert (
        _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"][0]["assignee"]
        == "Алексей"
    )

    freezer.move_to("2026-11-04T12:00:01+00:00")
    await _refresh_with(
        hass,
        aioclient_mock,
        entry,
        members=[member_payload(ASSIGNEE_ID, "Алексей Петров")],
        lists=[list_payload(LIST_ID)],
        items=[item_payload(ITEM_ID, title="Задача", assignee_user_id=ASSIGNEE_ID)],
    )

    assert (
        _entity_state(hass, "todo.zadachi").attributes["tasklerq_items"][0]["assignee"]
        == "Алексей Петров"
    )
