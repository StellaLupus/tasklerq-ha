"""Координатор синхронизации Tasklerq."""

from datetime import timedelta
from typing import Any, cast

import aiohttp
import pytest
from custom_components.tasklerq.api import TasklerqApiClient, TasklerqServerError
from custom_components.tasklerq.const import (
    CONF_CALENDAR_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
)
from custom_components.tasklerq.coordinator import TasklerqCoordinator
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .const import (
    BASE_URL,
    FIFTH_ITEM_ID,
    FOURTH_ITEM_ID,
    HOUSEHOLD_ID,
    HOUSEHOLD_URL,
    ITEM_ID,
    LIST_ID,
    NEXT_SINCE,
    OTHER_LIST_ID,
    SECOND_ITEM_ID,
    SHOPPING_LIST_ID,
    SIXTH_ITEM_ID,
    SNAPSHOT_URL,
    THIRD_ITEM_ID,
    TOKEN,
    calendar_event_payload,
    household_payload,
    item_payload,
    list_payload,
    snapshot_payload,
)

SECOND_SINCE = "2026-11-03T12:00:55Z"
THIRD_SINCE = "2026-11-03T12:01:55Z"


def _coordinator(entry: MockConfigEntry) -> TasklerqCoordinator:
    """Вернуть координатор загруженной записи."""
    return cast(TasklerqCoordinator, entry.runtime_data.coordinator)


def _snapshot_calls(aioclient_mock: Any) -> list[Any]:
    """Вернуть запросы к snapshot в порядке выполнения."""
    return [
        call for call in aioclient_mock.mock_calls if call[1].path == "/api/v1/snapshot"
    ]


def _query(call: Any) -> dict[str, str]:
    """Query-параметры запроса."""
    return dict(call[1].query)


async def _fail_refresh(
    coordinator: TasklerqCoordinator,
    aioclient_mock: Any,
    *,
    expected_seconds: int,
    **response: Any,
) -> None:
    """Выполнить неудачный refresh и проверить backoff."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, **response)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.update_interval == timedelta(seconds=expected_seconds)


async def _setup_entry(
    hass: HomeAssistant,
    aioclient_mock: Any,
    *,
    snapshot: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Добавить и загрузить запись с household и первым снапшотом."""  # noqa: RUF002
    aioclient_mock.get(HOUSEHOLD_URL, json=household_payload())
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot if snapshot is not None else snapshot_payload(),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: TOKEN},
        options=options or {},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_first_snapshot_is_full_and_next_uses_cursor(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Первый запрос без since, следующие — с next_since предыдущего ответа."""  # noqa: RUF002
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(next_since=NEXT_SINCE),
    )
    coordinator = _coordinator(entry)

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 1
    assert "since" not in _query(calls[0])
    assert "calendar_end" in _query(calls[0])
    assert coordinator.data.next_since == NEXT_SINCE

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(next_since=SECOND_SINCE))
    await coordinator.async_refresh()

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 1
    assert _query(calls[0])["since"] == NEXT_SINCE
    assert coordinator.data.next_since == SECOND_SINCE

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(next_since=THIRD_SINCE))
    await coordinator.async_refresh()

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 1
    assert _query(calls[0])["since"] == SECOND_SINCE
    assert coordinator.data.next_since == THIRD_SINCE


async def test_rejected_cursor_falls_back_to_full_sync(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """422 на since повторяет запрос без курсора и заменяет состояние."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(
            items=[
                item_payload(ITEM_ID, title="A"),
                item_payload(SECOND_ITEM_ID, title="B"),
            ],
            next_since=NEXT_SINCE,
        ),
    )
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        params={"since": NEXT_SINCE},
        status=422,
        json={},
    )
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            items=[item_payload(SECOND_ITEM_ID, title="B")],
            next_since=SECOND_SINCE,
        ),
    )
    await coordinator.async_refresh()

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 2
    assert _query(calls[0])["since"] == NEXT_SINCE
    assert "since" not in _query(calls[1])
    assert set(coordinator.data.items) == {SECOND_ITEM_ID}


async def test_client_never_sends_empty_since(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Пустая строка since не попадает в query-параметры."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())
    client = TasklerqApiClient(hass, BASE_URL, TOKEN)

    snapshot = await client.async_get_snapshot(since="")

    assert snapshot.next_since == NEXT_SINCE
    assert "since" not in _query(_snapshot_calls(aioclient_mock)[0])


async def test_incremental_merge_upserts_deletes_and_replaces_lists(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Инкрементальный ответ сливается по правилам ha-api.md §5.2–5.3."""  # noqa: RUF002
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(
            lists=[
                list_payload(LIST_ID),
                list_payload(
                    SHOPPING_LIST_ID,
                    kind="shopping",
                    name="Покупки",
                    is_default=False,
                    position=1,
                ),
            ],
            items=[
                item_payload(ITEM_ID, title="A", version=1),
                item_payload(SECOND_ITEM_ID, title="B", version=2),
                item_payload(THIRD_ITEM_ID, title="C", version=1),
                item_payload(FOURTH_ITEM_ID, list_id=SHOPPING_LIST_ID, title="F"),
            ],
            calendar_events=[calendar_event_payload(ITEM_ID, title="A")],
            next_since=NEXT_SINCE,
        ),
    )
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
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
                    position=2,
                ),
            ],
            items=[
                item_payload(ITEM_ID, title="A-updated", version=2),
                # Более старая версия не должна затереть текущую.
                item_payload(SECOND_ITEM_ID, title="B-stale", version=1),
                item_payload(FIFTH_ITEM_ID, list_id=OTHER_LIST_ID, title="D"),
                item_payload(SIXTH_ITEM_ID, title="E", version=5),
            ],
            deleted_item_ids=[THIRD_ITEM_ID],
            calendar_events=[
                calendar_event_payload(FIFTH_ITEM_ID, list_id=OTHER_LIST_ID, title="D")
            ],
            next_since=SECOND_SINCE,
        ),
    )
    await coordinator.async_refresh()

    data = coordinator.data
    assert set(data.lists) == {LIST_ID, OTHER_LIST_ID}
    assert set(data.items) == {ITEM_ID, SECOND_ITEM_ID, FIFTH_ITEM_ID, SIXTH_ITEM_ID}
    assert data.items[ITEM_ID]["title"] == "A-updated"
    assert data.items[ITEM_ID]["version"] == 2
    assert data.items[SECOND_ITEM_ID]["title"] == "B"
    assert data.items[SECOND_ITEM_ID]["version"] == 2
    assert [event["item_id"] for event in data.calendar_events] == [FIFTH_ITEM_ID]
    assert data.next_since == SECOND_SINCE


async def test_daily_resync_replaces_state(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Через сутки запрос идёт без since и полностью заменяет состояние."""
    freezer.move_to("2026-11-03T12:00:00+00:00")
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(
            items=[
                item_payload(ITEM_ID, title="A"),
                item_payload(SECOND_ITEM_ID, title="B"),
            ],
            next_since=NEXT_SINCE,
        ),
    )
    coordinator = _coordinator(entry)

    freezer.move_to("2026-11-04T12:00:01+00:00")
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            items=[item_payload(SECOND_ITEM_ID, title="B")],
            next_since=SECOND_SINCE,
        ),
    )
    await coordinator.async_refresh()

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 1
    assert "since" not in _query(calls[0])
    assert set(coordinator.data.items) == {SECOND_ITEM_ID}


async def test_calendar_end_uses_household_timezone_and_options(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """calendar_end — today household timezone плюс горизонт из options."""
    freezer.move_to("2026-11-03T22:30:00+00:00")
    await _setup_entry(
        hass,
        aioclient_mock,
        options={CONF_SCAN_INTERVAL: 60, CONF_CALENDAR_DAYS: 45},
    )

    calls = _snapshot_calls(aioclient_mock)
    assert len(calls) == 1
    # В Москве уже 4 ноября; 4 ноября + 45 дней = 19 декабря.  # noqa: RUF003
    assert _query(calls[0])["calendar_end"] == "2026-12-19"


async def test_backoff_and_recovery(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Ошибки связи и 5xx дают backoff 30…300, успех возвращает options."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(items=[item_payload(ITEM_ID, title="A")]),
        options={CONF_SCAN_INTERVAL: 90, CONF_CALENDAR_DAYS: 30},
    )
    coordinator = _coordinator(entry)
    assert coordinator.update_interval == timedelta(seconds=90)

    await _fail_refresh(
        coordinator,
        aioclient_mock,
        expected_seconds=30,
        exc=aiohttp.ClientConnectionError(),
    )
    for expected_seconds in (60, 120, 240, 300, 300):
        await _fail_refresh(
            coordinator,
            aioclient_mock,
            expected_seconds=expected_seconds,
            status=500,
            json={},
        )

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(items=[item_payload(ITEM_ID, title="Recovered")]),
    )
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.update_interval == timedelta(seconds=90)
    assert coordinator.data.items[ITEM_ID]["title"] == "Recovered"


async def test_runtime_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """401 во время polling запускает reauth, не роняя запись."""
    entry = await _setup_entry(hass, aioclient_mock)
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, status=401, json={})
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    "response",
    [
        {"text": "not json", "headers": {"Content-Type": "application/json"}},
        {"json": {"unexpected": True}},
        {"json": snapshot_payload(items=[{"id": ITEM_ID}])},
    ],
)
async def test_invalid_snapshot_is_update_failed(
    hass: HomeAssistant, aioclient_mock: Any, response: dict[str, Any]
) -> None:
    """Битый JSON и неожиданная форма дают UpdateFailed, а не падение."""  # noqa: RUF002
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        snapshot=snapshot_payload(items=[item_payload(ITEM_ID, title="A")]),
    )
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, **response)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.data.items[ITEM_ID]["title"] == "A"


async def test_client_rejects_invalid_snapshot(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Клиент поднимает ошибку клиента на неожиданной форме ответа."""
    aioclient_mock.get(SNAPSHOT_URL, json={"unexpected": True})
    client = TasklerqApiClient(hass, BASE_URL, TOKEN)

    with pytest.raises(TasklerqServerError):
        await client.async_get_snapshot()
