"""Календарь Tasklerq: одна read-only сущность на запись конфигурации."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, tzinfo

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TasklerqConfigEntry
from .api import (
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqCalendarEvent,
    TasklerqConnectionError,
    TasklerqItem,
    TasklerqPermissionError,
    TasklerqValidationError,
)
from .const import DOMAIN
from .coordinator import TasklerqCoordinator, TasklerqData

_LOGGER = logging.getLogger(__name__)

_API_MAX_DAYS = 366


def _unique_id(household_id: str) -> str:
    """Стабильный unique_id календарной сущности."""
    return f"{DOMAIN}:{household_id}:calendar"


def _payload_start_date(payload: TasklerqCalendarEvent, timezone: tzinfo) -> date:
    """Локальная дата начала вхождения в timezone household."""
    if payload["all_day"]:
        return date.fromisoformat(payload["start"])
    start = dt_util.parse_datetime(payload["start"])
    if start is None:
        raise HomeAssistantError(
            "Tasklerq вернул событие календаря с неверным временем."  # noqa: RUF001
        )
    return start.astimezone(timezone).date()


def _build_event(
    payload: TasklerqCalendarEvent, items: dict[str, TasklerqItem]
) -> CalendarEvent:
    """Собрать CalendarEvent, взяв описание из notes известной позиции."""
    start: date | datetime
    end: date | datetime
    if payload["all_day"]:
        start = date.fromisoformat(payload["start"])
        end = date.fromisoformat(payload["end"])
    else:
        parsed_start = dt_util.parse_datetime(payload["start"])
        parsed_end = dt_util.parse_datetime(payload["end"])
        if parsed_start is None or parsed_end is None:
            raise HomeAssistantError(
                "Tasklerq вернул событие календаря с неверным временем."  # noqa: RUF001
            )
        start = parsed_start
        end = parsed_end

    item = items.get(payload["item_id"])
    return CalendarEvent(
        start=start,
        end=end,
        summary=payload["title"],
        description=None if item is None else item["notes"],
        uid=payload["occurrence_key"],
    )


def _overlaps(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    """Пересекается ли событие с запрошенным интервалом."""  # noqa: RUF002
    return event.start_datetime_local < end and event.end_datetime_local > start


def _exclusive_end_date(end: datetime, timezone: tzinfo) -> date:
    """Исключающая конечная дата запроса для API календаря."""
    local_end = end.astimezone(timezone)
    result = local_end.date()
    if local_end.time() != time.min:
        result += timedelta(days=1)
    return result


def _calendar_error(err: TasklerqApiError) -> HomeAssistantError:
    """Русская ошибка чтения календаря по типу ответа клиента."""
    if isinstance(err, TasklerqValidationError):
        return HomeAssistantError(
            "Tasklerq не смог отдать календарь: слишком большой период "
            "или больше 1000 вхождений. Уменьшите период."
        )
    if isinstance(err, TasklerqAuthenticationError):
        return HomeAssistantError("Tasklerq отклонил токен. Обновите токен интеграции.")
    if isinstance(err, TasklerqPermissionError):
        return HomeAssistantError(
            "У токена Tasklerq нет прав на чтение календаря: нужен scope ha:read."  # noqa: RUF001
        )
    if isinstance(err, TasklerqConnectionError):
        return HomeAssistantError(
            "Не удалось подключиться к Tasklerq для загрузки календаря. "  # noqa: RUF001
            "Проверьте подключение и повторите попытку."
        )
    return HomeAssistantError(
        "Не удалось загрузить события календаря Tasklerq. "  # noqa: RUF001
        "Проверьте подключение и повторите попытку."
    )


class TasklerqCalendarEntity(CalendarEntity):
    """Read-only календарь задач Tasklerq."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = CalendarEntityFeature(0)

    def __init__(self, coordinator: TasklerqCoordinator, household_id: str) -> None:
        """Создать единственную календарную сущность household."""
        self._coordinator = coordinator
        self._attr_name = "Tasklerq"
        self._attr_unique_id = _unique_id(household_id)
        self._last_calendar_truncated: bool | None = None

    @property
    def available(self) -> bool:
        """Сущность доступна, пока успешен polling координатора."""
        return self._coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """Подписаться на обновления координатора."""
        self._async_check_truncation()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Обновить состояние из координатора."""
        self._async_check_truncation()
        self.async_write_ha_state()

    @callback
    def _async_check_truncation(self) -> None:
        """Предупредить об усечении снапшота один раз на смену флага."""  # noqa: RUF002
        data = self._coordinator.data
        truncated = data.calendar_truncated
        if truncated == self._last_calendar_truncated:
            return
        self._last_calendar_truncated = truncated
        if truncated:
            _LOGGER.warning(
                "Tasklerq calendar snapshot is truncated to 1000 events; "
                "later events in the horizon are not shown"
            )

    def _snapshot_events(
        self, data: TasklerqData, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """Вхождения в пределах горизонта из данных координатора."""
        timezone = self._coordinator.timezone
        today = dt_util.now(timezone).date()
        horizon_end = self._coordinator.calendar_horizon_end
        events: list[CalendarEvent] = []
        for payload in data.calendar_events:
            event_date = _payload_start_date(payload, timezone)
            if event_date < today or event_date >= horizon_end:
                continue
            event = _build_event(payload, data.items)
            if _overlaps(event, start, end):
                events.append(event)
        return events

    async def _async_fetch_past(
        self,
        items: dict[str, TasklerqItem],
        start_date: date,
        end_date: date,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEvent]:
        """Запросить у API часть интервала раньше сегодняшнего дня."""  # noqa: RUF002
        events: list[CalendarEvent] = []
        current = start_date
        while current < end_date:
            chunk_end = min(current + timedelta(days=_API_MAX_DAYS), end_date)
            try:
                payloads = await self._coordinator.client.async_get_calendar(
                    start=current.isoformat(), end=chunk_end.isoformat()
                )
            except TasklerqApiError as err:
                raise _calendar_error(err) from err
            for payload in payloads:
                event = _build_event(payload, items)
                if _overlaps(event, start, end):
                    events.append(event)
            current = chunk_end
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """Текущее или ближайшее событие для state on/off."""
        data = self._coordinator.data
        timezone = self._coordinator.timezone
        today = dt_util.now(timezone).date()
        horizon_end = self._coordinator.calendar_horizon_end
        now = dt_util.now()
        current: CalendarEvent | None = None
        upcoming: CalendarEvent | None = None

        for payload in data.calendar_events:
            event_date = _payload_start_date(payload, timezone)
            if event_date < today or event_date >= horizon_end:
                continue
            event = _build_event(payload, data.items)
            if event.start_datetime_local <= now < event.end_datetime_local:
                if (
                    current is None
                    or event.start_datetime_local > current.start_datetime_local
                ):
                    current = event
            elif event.start_datetime_local > now and (
                upcoming is None
                or event.start_datetime_local < upcoming.start_datetime_local
            ):
                upcoming = event

        return current or upcoming

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """События за интервал: снапшот на горизонте, API — за прошлое."""
        data = self._coordinator.data
        timezone = self._coordinator.timezone
        today = dt_util.now(timezone).date()
        start_day = start_date.astimezone(timezone).date()
        end_day = _exclusive_end_date(end_date, timezone)

        events = self._snapshot_events(data, start_date, end_date)
        if start_day < today:
            api_end = min(end_day, today)
            if start_day < api_end:
                events.extend(
                    await self._async_fetch_past(
                        data.items,
                        start_day,
                        api_end,
                        start_date,
                        end_date,
                    )
                )

        events.sort(
            key=lambda event: (
                event.start_datetime_local,
                not event.all_day,
                event.uid or "",
            )
        )
        return events


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TasklerqConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Создать календарную сущность записи конфигурации."""
    coordinator = entry.runtime_data.coordinator
    household_id = entry.runtime_data.household["id"]
    async_add_entities([TasklerqCalendarEntity(coordinator, household_id)])
