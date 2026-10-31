"""Сервисы интеграции Tasklerq."""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    SERVICE_CANCEL_CAPTURE,
    SERVICE_CAPTURE,
    SERVICE_CONFIRM_CAPTURE,
    SERVICE_UNDO_LAST_CAPTURE,
)

_LOGGER = logging.getLogger(__name__)

ServiceHandler = Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]


async def async_handle_capture(call: ServiceCall) -> ServiceResponse:
    """Отправить текст в Tasklerq (реализация — в 6.10)."""
    raise HomeAssistantError("Сервис tasklerq.capture ещё не реализован")


async def async_handle_confirm_capture(call: ServiceCall) -> ServiceResponse:
    """Подтвердить черновик capture (реализация — в 6.10)."""
    raise HomeAssistantError("Сервис tasklerq.confirm_capture ещё не реализован")


async def async_handle_cancel_capture(call: ServiceCall) -> ServiceResponse:
    """Отменить черновик capture (реализация — в 6.10)."""
    raise HomeAssistantError("Сервис tasklerq.cancel_capture ещё не реализован")


async def async_handle_undo_last_capture(call: ServiceCall) -> ServiceResponse:
    """Отменить последний голосовой capture (реализация — в 6.10)."""
    raise HomeAssistantError("Сервис tasklerq.undo_last_capture ещё не реализован")


SERVICES: dict[str, ServiceHandler] = {
    SERVICE_CAPTURE: async_handle_capture,
    SERVICE_CONFIRM_CAPTURE: async_handle_confirm_capture,
    SERVICE_CANCEL_CAPTURE: async_handle_cancel_capture,
    SERVICE_UNDO_LAST_CAPTURE: async_handle_undo_last_capture,
}


def async_setup_services(hass: HomeAssistant) -> None:
    """Зарегистрировать сервисы Tasklerq."""
    for name, handler in SERVICES.items():
        if hass.services.has_service(DOMAIN, name):
            continue
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("Registered service %s.%s", DOMAIN, name)


def async_unload_services(hass: HomeAssistant) -> None:
    """Снять сервисы Tasklerq."""
    for name in SERVICES:
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
            _LOGGER.debug("Removed service %s.%s", DOMAIN, name)
