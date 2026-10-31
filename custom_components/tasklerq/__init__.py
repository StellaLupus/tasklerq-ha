"""Интеграция Tasklerq для Home Assistant."""

import logging
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any

from awesomeversion import AwesomeVersion
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import async_get_time_zone

from .api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqConnectionError,
    TasklerqServerError,
)
from .const import CONF_TOKEN, CONF_URL, DOMAIN, MIN_HOME_ASSISTANT_VERSION
from .coordinator import TasklerqCoordinator, read_options
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.TODO]


@dataclass(slots=True)
class TasklerqRuntimeData:
    """Данные загруженной записи Tasklerq."""

    client: TasklerqApiClient
    coordinator: TasklerqCoordinator
    household: dict[str, Any]


TasklerqConfigEntry = ConfigEntry[TasklerqRuntimeData]


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузить запись после изменения options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: TasklerqConfigEntry) -> bool:
    """Загрузить интеграцию Tasklerq."""
    if AwesomeVersion(HA_VERSION) < AwesomeVersion(MIN_HOME_ASSISTANT_VERSION):
        _LOGGER.warning(
            "Tasklerq requires Home Assistant %s or newer; current version is %s, "
            "integration is not loaded",
            MIN_HOME_ASSISTANT_VERSION,
            HA_VERSION,
        )
        raise ConfigEntryError(
            "Tasklerq требует Home Assistant "
            f"{MIN_HOME_ASSISTANT_VERSION} или новее; "
            f"установлена версия {HA_VERSION}"
        )

    client = TasklerqApiClient(
        hass,
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
    )
    try:
        household, members = await client.async_get_household_and_members()
    except TasklerqAuthenticationError as err:
        raise ConfigEntryAuthFailed("Tasklerq rejected the API token") from err
    except (TasklerqConnectionError, TasklerqServerError) as err:
        raise ConfigEntryNotReady(f"Tasklerq is not available: {err}") from err
    except TasklerqApiError as err:
        raise ConfigEntryError(f"Tasklerq setup failed: {err}") from err

    timezone_name = household.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name:
        timezone_name = hass.config.time_zone
    resolved_timezone = await async_get_time_zone(timezone_name)
    timezone: tzinfo = (
        resolved_timezone
        if resolved_timezone is not None
        else dt_util.DEFAULT_TIME_ZONE
    )

    scan_interval, calendar_days = read_options(entry)
    coordinator = TasklerqCoordinator(
        hass,
        entry,
        client,
        timezone=timezone,
        timezone_name=timezone_name,
        scan_interval=scan_interval,
        calendar_days=calendar_days,
        members=members,
    )
    entry.runtime_data = TasklerqRuntimeData(
        client=client,
        coordinator=coordinator,
        household=household,
    )
    await coordinator.async_config_entry_first_refresh()

    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TasklerqConfigEntry) -> bool:
    """Выгрузить интеграцию Tasklerq."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    other_loaded = any(
        other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if not other_loaded:
        async_unload_services(hass)
    return True
