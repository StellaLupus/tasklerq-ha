"""Diagnostics Tasklerq без токена и пользовательского контента."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import TasklerqConfigEntry
from .const import CONF_TOKEN, CONF_URL, DOMAIN

_TO_REDACT = {CONF_TOKEN}


def _url_without_credentials(raw_url: str) -> str:
    """Оставить схему, хост и путь URL без userinfo, query и fragment."""
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return ""
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _isoformat(value: datetime | None) -> str | None:
    """Момент времени в UTC для JSON."""
    if value is None:
        return None
    return dt_util.as_utc(value).isoformat()


def _entity_diagnostics(
    hass: HomeAssistant, entry: TasklerqConfigEntry
) -> list[dict[str, Any]]:
    """Список сущностей интеграции без их названий и содержимого."""
    registry = er.async_get(hass)
    result: list[dict[str, Any]] = []
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(registry_entry.entity_id)
        result.append(
            {
                "entity_id": registry_entry.entity_id,
                "platform": registry_entry.domain,
                "available": state is not None and state.state != STATE_UNAVAILABLE,
            }
        )
    result.sort(key=lambda item: item["entity_id"])
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TasklerqConfigEntry
) -> dict[str, Any]:
    """Собрать диагностику конфигурации, сущностей и последней синхронизации."""
    integration = await async_get_integration(hass, DOMAIN)
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    diagnostics: dict[str, Any] = {
        "url": _url_without_credentials(entry.data[CONF_URL]),
        "version": integration.version or "",
        "scan_interval": coordinator.scan_interval,
        "calendar_days": coordinator.calendar_days,
        "last_successful_sync": _isoformat(coordinator.last_successful_sync),
        "lists_count": len(data.lists) if data is not None else 0,
        "items_count": len(data.items) if data is not None else 0,
        "entities": _entity_diagnostics(hass, entry),
        "last_sync_error": (
            None
            if coordinator.last_error_code is None
            else {
                "code": coordinator.last_error_code,
                "at": _isoformat(coordinator.last_error_at),
            }
        ),
    }
    return async_redact_data(diagnostics, _TO_REDACT)
