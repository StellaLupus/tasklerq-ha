"""Координатор синхронизации Tasklerq."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqCalendarEvent,
    TasklerqConnectionError,
    TasklerqItem,
    TasklerqList,
    TasklerqPermissionError,
    TasklerqSnapshot,
    TasklerqValidationError,
)
from .const import (
    CONF_CALENDAR_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_CALENDAR_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

FULL_SYNC_INTERVAL = timedelta(hours=24)


@dataclass(slots=True)
class TasklerqData:
    """Состояние синхронизации Tasklerq."""

    lists: dict[str, TasklerqList]
    items: dict[str, TasklerqItem]
    calendar_events: list[TasklerqCalendarEvent]
    next_since: str | None
    calendar_truncated: bool
    timezone: str
    members: dict[str, str]
    last_full_sync: datetime


class TasklerqCoordinator(DataUpdateCoordinator[TasklerqData]):
    """Polling-координатор Tasklerq на запись конфигурации."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TasklerqApiClient,
        *,
        timezone: tzinfo,
        timezone_name: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        calendar_days: int = DEFAULT_CALENDAR_DAYS,
        members: dict[str, str] | None = None,
    ) -> None:
        """Создать координатор с интервалом и горизонтом из options."""  # noqa: RUF002
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._timezone = timezone
        self._timezone_name = timezone_name
        self._scan_interval = scan_interval
        self._calendar_days = calendar_days
        self._members = dict(members or {})
        self._backoff_seconds = MIN_SCAN_INTERVAL
        self._last_full_sync: datetime | None = None

    @property
    def client(self) -> TasklerqApiClient:
        """Клиент API для платформ."""
        return self._client

    async def _async_update_data(self) -> TasklerqData:
        """Запросить снапшот и применить его к локальному состоянию."""  # noqa: RUF002
        now = dt_util.utcnow()
        full_sync = (
            self._last_full_sync is None
            or now - self._last_full_sync >= FULL_SYNC_INTERVAL
        )
        since = None if full_sync else self.data.next_since
        if full_sync and self.data is not None:
            await self._async_refresh_members()

        try:
            snapshot = await self._client.async_get_snapshot(
                since=since,
                calendar_end=self._calendar_end(),
            )
        except TasklerqValidationError as err:
            if since is None:
                self._apply_backoff()
                raise UpdateFailed(f"Tasklerq synchronization failed: {err}") from err
            _LOGGER.warning(
                "Tasklerq rejected the snapshot cursor; performing a full sync"
            )
            full_sync = True
            snapshot = await self._async_fetch_full_snapshot()
        except TasklerqAuthenticationError as err:
            raise ConfigEntryAuthFailed("Tasklerq rejected the API token") from err
        except TasklerqConnectionError as err:
            self._apply_backoff()
            raise UpdateFailed(f"Tasklerq is unavailable: {err}") from err
        except TasklerqPermissionError as err:
            self._apply_backoff()
            raise UpdateFailed("Tasklerq token is missing the required scope") from err
        except TasklerqApiError as err:
            self._apply_backoff()
            raise UpdateFailed(f"Tasklerq synchronization failed: {err}") from err

        data = self._apply_snapshot(snapshot, full_sync=full_sync, now=now)
        self._last_full_sync = data.last_full_sync
        self._reset_interval()
        return data

    async def _async_fetch_full_snapshot(self) -> TasklerqSnapshot:
        """Повторить запрос без since после отказа курсора."""
        try:
            return await self._client.async_get_snapshot(
                calendar_end=self._calendar_end()
            )
        except TasklerqAuthenticationError as err:
            raise ConfigEntryAuthFailed("Tasklerq rejected the API token") from err
        except (TasklerqConnectionError, TasklerqPermissionError) as err:
            self._apply_backoff()
            raise UpdateFailed(f"Tasklerq is unavailable: {err}") from err
        except TasklerqApiError as err:
            self._apply_backoff()
            raise UpdateFailed(f"Tasklerq synchronization failed: {err}") from err

    async def _async_refresh_members(self) -> None:
        """Обновить имена участников без влияния на доступность todo-сущностей."""
        try:
            self._members = await self._client.async_get_household_members()
        except TasklerqApiError as err:
            _LOGGER.warning("Could not refresh Tasklerq household members: %s", err)

    def _apply_snapshot(
        self, snapshot: TasklerqSnapshot, *, full_sync: bool, now: datetime
    ) -> TasklerqData:
        """Слить снапшот с состоянием: списки всегда, позиции по id."""  # noqa: RUF002
        previous = self.data
        lists = {item["id"]: item for item in snapshot.lists}

        if full_sync or previous is None:
            items = {item["id"]: item for item in snapshot.items}
            last_full_sync = now
        else:
            items = dict(previous.items)
            for item in snapshot.items:
                existing = items.get(item["id"])
                if existing is not None and item["version"] < existing["version"]:
                    continue
                items[item["id"]] = item
            for item_id in snapshot.deleted_item_ids:
                items.pop(item_id, None)
            last_full_sync = previous.last_full_sync

        # Позиций списка, которого больше нет в полном массиве lists, быть не должно.
        items = {
            item_id: item for item_id, item in items.items() if item["list_id"] in lists
        }

        return TasklerqData(
            lists=lists,
            items=items,
            calendar_events=list(snapshot.calendar_events),
            next_since=snapshot.next_since,
            calendar_truncated=snapshot.calendar_truncated,
            timezone=self._timezone_name,
            members=dict(self._members),
            last_full_sync=last_full_sync,
        )

    def _calendar_end(self) -> str:
        """Исключающий конец горизонта: сегодня household + calendar_days."""
        today = dt_util.now(self._timezone).date()
        return (today + timedelta(days=self._calendar_days)).isoformat()

    def _apply_backoff(self) -> None:
        """Увеличить интервал следующего опроса в пределах 30…300 секунд."""
        seconds = self._backoff_seconds
        self._backoff_seconds = min(MAX_SCAN_INTERVAL, seconds * 2)
        self.update_interval = timedelta(seconds=seconds)

    def _reset_interval(self) -> None:
        """Вернуть интервал из options после успешного обновления."""
        self._backoff_seconds = MIN_SCAN_INTERVAL
        self.update_interval = timedelta(seconds=self._scan_interval)


def read_options(entry: ConfigEntry) -> tuple[int, int]:
    """Вернуть интервал polling и горизонт календаря из options."""
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    calendar_days = entry.options.get(CONF_CALENDAR_DAYS, DEFAULT_CALENDAR_DAYS)
    return int(scan_interval), int(calendar_days)
