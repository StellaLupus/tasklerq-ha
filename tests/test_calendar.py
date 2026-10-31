"""Календарная сущность Tasklerq: события, горизонт и read-only."""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from custom_components.tasklerq.coordinator import TasklerqCoordinator
from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .const import (
    BASE_URL,
    HOUSEHOLD_ID,
    HOUSEHOLD_URL,
    ITEM_ID,
    LIST_ID,
    SECOND_ITEM_ID,
    SNAPSHOT_URL,
    TOKEN,
    calendar_event_payload,
    household_payload,
    item_payload,
    list_payload,
    snapshot_payload,
)

CALENDAR_URL = f"{BASE_URL}/api/v1/calendar"
MOSCOW = ZoneInfo("Europe/Moscow")


def _coordinator(entry: MockConfigEntry) -> TasklerqCoordinator:
    """Вернуть координатор загруженной записи."""
    return entry.runtime_data.coordinator


def _calendar_entity(hass: HomeAssistant) -> CalendarEntity:
    """Вернуть календарную сущность Tasklerq."""
    entity = hass.data[DATA_COMPONENT].get_entity("calendar.tasklerq")
    assert isinstance(entity, CalendarEntity)
    return entity


def _calls(aioclient_mock: Any, path: str) -> list[Any]:
    """Запросы к указанному пути."""
    return [call for call in aioclient_mock.mock_calls if call[1].path == path]


def _state(hass: HomeAssistant, entity_id: str) -> Any:
    """Состояние сущности с проверкой существования."""  # noqa: RUF002
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _query(call: Any) -> dict[str, str]:
    """Query-параметры запроса."""
    return dict(call[1].query)


async def _setup_entry(
    hass: HomeAssistant,
    aioclient_mock: Any,
    *,
    items: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
    calendar_truncated: bool = False,
) -> MockConfigEntry:
    """Добавить и загрузить запись со снапшотом."""  # noqa: RUF002
    aioclient_mock.get(HOUSEHOLD_URL, json=household_payload())
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            lists=[list_payload(LIST_ID)],
            items=items,
            calendar_events=calendar_events,
            calendar_truncated=calendar_truncated,
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: TOKEN},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_calendar_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_timed_event_uses_household_timezone(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Событие со временем начинается в 18:00 household timezone."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    occurrence_key = f"{ITEM_ID}:2026-11-03T18:00:00+03:00"
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(
                ITEM_ID,
                title="Встреча",
                due_date=None,
                due_at="2026-11-03T15:00:00Z",
                notes="Взять документы",
            )
        ],
        calendar_events=[
            calendar_event_payload(
                ITEM_ID,
                occurrence_key=occurrence_key,
                start="2026-11-03T15:00:00Z",
                end="2026-11-03T16:00:00Z",
                all_day=False,
                title="Встреча",
            )
        ],
    )

    state = hass.states.get("calendar.tasklerq")
    assert state is not None
    assert state.name == "Tasklerq"
    assert state.attributes["message"] == "Встреча"
    assert state.attributes["all_day"] is False
    assert state.attributes["description"] == "Взять документы"

    events = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 11, 4, tzinfo=MOSCOW),
    )

    assert len(events) == 1
    event = events[0]
    assert event.summary == "Встреча"
    assert event.start == datetime(2026, 11, 3, 18, 0, tzinfo=MOSCOW)
    assert event.end == datetime(2026, 11, 3, 19, 0, tzinfo=MOSCOW)
    assert event.all_day is False
    assert event.uid == occurrence_key
    assert event.description == "Взять документы"


async def test_all_day_event_stays_all_day(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Вседневная задача — all-day событие с исключающим end."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(
                ITEM_ID,
                title="Вседневная",
                due_date="2026-11-05",
                due_at=None,
            )
        ],
        calendar_events=[
            # Завершённая позиция показывается как обычное событие.
            calendar_event_payload(
                ITEM_ID,
                occurrence_key=f"{ITEM_ID}:2026-11-05",
                start="2026-11-05",
                end="2026-11-06",
                all_day=True,
                title="Вседневная",
                status="done",
            )
        ],
    )

    state = hass.states.get("calendar.tasklerq")
    assert state is not None
    assert state.attributes["message"] == "Вседневная"
    assert state.attributes["all_day"] is True

    events = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 11, 7, tzinfo=MOSCOW),
    )

    assert len(events) == 1
    assert events[0].start == date(2026, 11, 5)
    assert events[0].end == date(2026, 11, 6)
    assert events[0].all_day is True


async def test_recurring_events_have_distinct_uids_within_horizon(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Повторения на горизонте — отдельные события, за горизонтом их нет."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    occurrences = ["2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"]
    ends = ["2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25"]
    events = [
        calendar_event_payload(
            ITEM_ID,
            occurrence_key=f"{ITEM_ID}:{day}",
            start=day,
            end=end,
            title="Еженедельная",
        )
        for day, end in zip(occurrences, ends, strict=True)
    ]
    # Горизонт 30 дней: 24 ноября входит, 8 декабря — уже нет.
    events.append(
        calendar_event_payload(
            ITEM_ID,
            occurrence_key=f"{ITEM_ID}:2026-12-08",
            start="2026-12-08",
            end="2026-12-09",
            title="Еженедельная",
        )
    )
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[
            item_payload(
                ITEM_ID,
                title="Еженедельная",
                due_date="2026-11-03",
                due_at=None,
            )
        ],
        calendar_events=events,
    )
    aioclient_mock.clear_requests()

    result = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 12, 31, tzinfo=MOSCOW),
    )

    assert [event.uid for event in result] == [
        f"{ITEM_ID}:{day}" for day in occurrences
    ]
    assert len({event.uid for event in result}) == len(occurrences)
    assert _calls(aioclient_mock, "/api/v1/calendar") == []


async def test_create_event_is_not_supported(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """calendar.create_event не поддерживается и не пишет в Tasklerq."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    await _setup_entry(hass, aioclient_mock, items=[], calendar_events=[])
    entity = _calendar_entity(hass)
    assert entity.supported_features == 0

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "calendar",
            "create_event",
            {
                "entity_id": "calendar.tasklerq",
                "summary": "Новое событие",
                "start_date": "2026-11-05",
                "end_date": "2026-11-06",
            },
            blocking=True,
        )

    assert not any(
        call[0].lower() in ("post", "patch", "delete")
        for call in aioclient_mock.mock_calls
    )


async def test_get_events_for_past_week_uses_calendar_api(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Прошлый интервал запрашивается у /calendar с исключающим end."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-10T12:00:00+00:00")
    await _setup_entry(hass, aioclient_mock, items=[], calendar_events=[])
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        CALENDAR_URL,
        json={
            "events": [
                calendar_event_payload(
                    ITEM_ID,
                    occurrence_key=f"{ITEM_ID}:2026-11-05",
                    start="2026-11-05",
                    end="2026-11-06",
                    title="Прошлая задача",
                )
            ]
        },
    )

    events = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 11, 10, tzinfo=MOSCOW),
    )

    calls = _calls(aioclient_mock, "/api/v1/calendar")
    assert len(calls) == 1
    assert _query(calls[0]) == {"start": "2026-11-03", "end": "2026-11-10"}
    assert len(events) == 1
    assert events[0].uid == f"{ITEM_ID}:2026-11-05"


async def test_get_events_splits_intervals_longer_than_366_days(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Длинный прошлый интервал дробится на запросы не больше 366 дней."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-10T12:00:00+00:00")
    await _setup_entry(hass, aioclient_mock, items=[], calendar_events=[])
    aioclient_mock.clear_requests()
    aioclient_mock.get(CALENDAR_URL, json={"events": []})

    await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2025, 10, 1, tzinfo=MOSCOW),
        datetime(2026, 11, 10, tzinfo=MOSCOW),
    )

    calls = _calls(aioclient_mock, "/api/v1/calendar")
    assert [_query(call) for call in calls] == [
        {"start": "2025-10-01", "end": "2026-10-02"},
        {"start": "2026-10-02", "end": "2026-11-10"},
    ]


async def test_get_events_inside_horizon_does_not_call_api(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Интервал внутри горизонта берётся только из снапшота."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[],
        calendar_events=[calendar_event_payload(ITEM_ID, title="Из снапшота")],
    )
    aioclient_mock.clear_requests()

    events = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 11, 10, tzinfo=MOSCOW),
    )

    assert [event.summary for event in events] == ["Из снапшота"]
    assert _calls(aioclient_mock, "/api/v1/calendar") == []


async def test_calendar_api_422_raises_home_assistant_error(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """422 от /calendar не превращается в молча пустой список."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-10T12:00:00+00:00")
    await _setup_entry(hass, aioclient_mock, items=[], calendar_events=[])
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        CALENDAR_URL,
        status=422,
        json={
            "error": {
                "code": "validation_error",
                "message": "Too many occurrences",
            }
        },
    )

    with pytest.raises(HomeAssistantError, match="календар"):
        await _calendar_entity(hass).async_get_events(
            hass,
            datetime(2026, 11, 3, tzinfo=MOSCOW),
            datetime(2026, 11, 10, tzinfo=MOSCOW),
        )


async def test_description_comes_from_item_notes(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """Описание — notes известной позиции, у неизвестной оно пустое."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, notes="Из заметки")],
        calendar_events=[
            calendar_event_payload(ITEM_ID, title="С заметкой"),  # noqa: RUF001
            calendar_event_payload(SECOND_ITEM_ID, title="Без позиции"),
        ],
    )

    events = await _calendar_entity(hass).async_get_events(
        hass,
        datetime(2026, 11, 3, tzinfo=MOSCOW),
        datetime(2026, 11, 10, tzinfo=MOSCOW),
    )

    assert events[0].description == "Из заметки"
    assert events[1].description in (None, "")


async def test_state_is_on_during_timed_event(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """State on внутри timed-события и off вне его."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T15:30:00+00:00")
    await _setup_entry(
        hass,
        aioclient_mock,
        items=[],
        calendar_events=[
            calendar_event_payload(
                ITEM_ID,
                start="2026-11-03T15:00:00Z",
                end="2026-11-03T16:00:00Z",
                all_day=False,
                title="Встреча",
            )
        ],
    )
    entity = _calendar_entity(hass)
    assert _state(hass, "calendar.tasklerq").state == "on"

    freezer.move_to("2026-11-03T16:30:00+00:00")
    entity.async_write_ha_state()

    assert _state(hass, "calendar.tasklerq").state == "off"


async def test_truncated_calendar_warns_once_per_flag_change(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any, caplog: Any
) -> None:
    """calendar_truncated даёт одно предупреждение, а не на каждом опросе."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    caplog.set_level("WARNING", logger="custom_components.tasklerq.calendar")
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        items=[],
        calendar_events=[],
        calendar_truncated=True,
    )

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            lists=[list_payload(LIST_ID)],
            items=[],
            calendar_events=[],
            calendar_truncated=True,
        ),
    )
    await _coordinator(entry).async_refresh()
    await hass.async_block_till_done()

    warnings = [
        record
        for record in caplog.records
        if record.name == "custom_components.tasklerq.calendar"
        and "truncat" in record.message.lower()
    ]
    assert len(warnings) == 1


async def test_entity_unique_id_and_registry(
    hass: HomeAssistant, aioclient_mock: Any, freezer: Any
) -> None:
    """unique_id календаря согласован с todo-платформой."""  # noqa: RUF002
    await hass.config.async_set_time_zone("Europe/Moscow")
    freezer.move_to("2026-11-03T12:00:00+00:00")
    await _setup_entry(hass, aioclient_mock, items=[], calendar_events=[])

    registry_entry = er.async_get(hass).async_get("calendar.tasklerq")
    assert registry_entry is not None
    assert registry_entry.unique_id == f"tasklerq:{HOUSEHOLD_ID}:calendar"
    assert registry_entry.domain == "calendar"
