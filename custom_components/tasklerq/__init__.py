"""Интеграция Tasklerq для Home Assistant."""

import logging

from awesomeversion import AwesomeVersion
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from .api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqConnectionError,
    TasklerqServerError,
)
from .const import CONF_TOKEN, CONF_URL, DOMAIN, MIN_HOME_ASSISTANT_VERSION
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузить запись после изменения options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
        await client.async_get_household()
    except TasklerqAuthenticationError as err:
        raise ConfigEntryAuthFailed("Tasklerq rejected the API token") from err
    except (TasklerqConnectionError, TasklerqServerError) as err:
        raise ConfigEntryNotReady(f"Tasklerq is not available: {err}") from err
    except TasklerqApiError as err:
        raise ConfigEntryError(f"Tasklerq setup failed: {err}") from err

    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузить интеграцию Tasklerq."""
    other_loaded = any(
        other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if not other_loaded:
        async_unload_services(hass)
    return True
