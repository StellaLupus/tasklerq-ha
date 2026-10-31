"""Интеграция Tasklerq для Home Assistant."""

import logging

from awesomeversion import AwesomeVersion
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import MIN_HOME_ASSISTANT_VERSION
from .services import SERVICES, async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

__all__ = ["HA_VERSION", "MIN_HOME_ASSISTANT_VERSION", "SERVICES"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Загрузить интеграцию Tasklerq."""
    if AwesomeVersion(HA_VERSION) < AwesomeVersion(MIN_HOME_ASSISTANT_VERSION):
        _LOGGER.warning(
            "Tasklerq requires Home Assistant %s or newer; current version is %s, "
            "integration is not loaded",
            MIN_HOME_ASSISTANT_VERSION,
            HA_VERSION,
        )
        return False

    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузить интеграцию Tasklerq."""
    async_unload_services(hass)
    return True
