"""Сервисы интеграции Tasklerq."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, cast

import probatio as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from .api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqCapture,
    TasklerqConflictError,
    TasklerqConnectionError,
    TasklerqGoneError,
    TasklerqNotFoundError,
    TasklerqPayloadTooLargeError,
    TasklerqPermissionError,
    TasklerqRateLimitError,
    TasklerqServerError,
    TasklerqServiceUnavailableError,
    TasklerqValidationError,
)
from .const import (
    DOMAIN,
    SERVICE_CANCEL_CAPTURE,
    SERVICE_CAPTURE,
    SERVICE_CONFIRM_CAPTURE,
    SERVICE_UNDO_LAST_CAPTURE,
)
from .coordinator import TasklerqCoordinator

_LOGGER = logging.getLogger(__name__)

ServiceHandler = Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]

CAPTURE_SCHEMA = vol.Schema(
    {
        vol.Required("text"): vol.All(str, vol.Length(min=1)),
        vol.Optional("source", default="ha_automation"): vol.In(
            ["ha_voice", "ha_automation"]
        ),
        vol.Optional("mode", default="auto"): vol.In(["auto", "draft"]),
        vol.Optional("hint"): vol.In(["tasks", "shopping", "checklist"]),
        vol.Optional("list_id"): vol.All(str, vol.Length(min=1)),
        vol.Optional("idempotency_key"): vol.All(
            str, vol.Any("", vol.Match(r"^[!-~]{1,128}$"))
        ),
    }
)

DRAFT_SCHEMA = vol.Schema({vol.Required("draft_id"): vol.All(str, vol.Length(min=1))})

EMPTY_SCHEMA = vol.Schema({})


def _validation_reason(err: TasklerqApiError) -> str:
    """Причина отказа из сообщения клиента или простых полей details."""
    if err.message:
        return err.message
    return "; ".join(
        f"{key}: {value}"
        for key, value in err.details.items()
        if isinstance(value, (str, int, float, bool))
    )


def _select_runtime(
    hass: HomeAssistant, *, list_id: str | None
) -> tuple[ConfigEntry, TasklerqApiClient, TasklerqCoordinator]:
    """Выбрать загруженную запись Tasklerq для вызова capture."""
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not loaded:
        raise HomeAssistantError(
            "Tasklerq не настроен или не загружен. Добавьте интеграцию Tasklerq.",
            translation_domain=DOMAIN,
            translation_key="capture_not_configured",
        )

    if len(loaded) == 1:
        entry = loaded[0]
    else:
        matches = [
            candidate
            for candidate in loaded
            if list_id is not None
            and candidate.runtime_data.coordinator.data is not None
            and list_id in candidate.runtime_data.coordinator.data.lists
        ]
        if len(matches) != 1:
            raise HomeAssistantError(
                "Несколько подключений Tasklerq. Укажите list_id из списков "
                "нужного подключения.",
                translation_domain=DOMAIN,
                translation_key="capture_ambiguous_entry",
            )
        entry = matches[0]

    runtime = entry.runtime_data
    return entry, runtime.client, runtime.coordinator


def _capture_response(result: TasklerqCapture) -> ServiceResponse:
    """Ответ capture/confirm в форме сервиса."""
    return {
        "status": result.status,
        "speech": result.speech,
        "items": cast("list[Any]", result.items),
        "draft_id": result.draft_id,
        "capture_id": result.capture_id,
        "fallback": result.fallback,
    }


def _common_capture_error(
    hass: HomeAssistant,
    entry: ConfigEntry,
    err: TasklerqApiError,
    *,
    text: str | None,
) -> HomeAssistantError | None:
    """Общие ошибки capture/confirm/cancel/undo: 401 запускает reauth, 403 понятен."""
    if isinstance(err, TasklerqAuthenticationError):
        entry.async_start_reauth(hass)
        if text is None:
            return HomeAssistantError(
                "Tasklerq: нужна повторная авторизация. Обновите токен интеграции.",
                translation_domain=DOMAIN,
                translation_key="capture_action_reauth",
            )
        return HomeAssistantError(
            f"Tasklerq: нужна повторная авторизация. Текст «{text}» не сохранён. "
            "Обновите токен интеграции.",
            translation_domain=DOMAIN,
            translation_key="capture_reauth",
            translation_placeholders={"text": text},
        )
    if isinstance(err, TasklerqPermissionError):
        if text is None:
            return HomeAssistantError(
                "У токена Tasklerq нет прав на capture: нужен scope capture:write.",  # noqa: RUF001
                translation_domain=DOMAIN,
                translation_key="capture_action_forbidden",
            )
        return HomeAssistantError(
            f"У токена Tasklerq нет прав на capture: нужен scope capture:write. "  # noqa: RUF001
            f"Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_forbidden",
            translation_placeholders={"text": text},
        )
    return None


def _capture_error(
    hass: HomeAssistant,
    entry: ConfigEntry,
    err: TasklerqApiError,
    text: str,
) -> HomeAssistantError:
    """Русская ошибка capture и исходный текст; 401 запускает reauth."""
    common = _common_capture_error(hass, entry, err, text=text)
    if common is not None:
        return common
    if isinstance(err, TasklerqNotFoundError):
        return HomeAssistantError(
            f"Capture API в Tasklerq выключен. Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_disabled",
            translation_placeholders={"text": text},
        )
    if isinstance(err, TasklerqPayloadTooLargeError):
        return HomeAssistantError(
            f"Текст для capture слишком длинный. Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_too_long",
            translation_placeholders={"text": text},
        )
    if isinstance(err, TasklerqRateLimitError):
        retry_after = err.retry_after if err.retry_after is not None else 60
        return HomeAssistantError(
            f"Слишком много запросов capture к Tasklerq. Повторите через "
            f"{retry_after} сек. Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_rate_limited",
            translation_placeholders={
                "text": text,
                "retry_after": str(retry_after),
            },
        )
    if isinstance(err, TasklerqServiceUnavailableError):
        return HomeAssistantError(
            f"Tasklerq не смог разобрать текст: разбор недоступен. "
            f"Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_llm_unavailable",
            translation_placeholders={"text": text},
        )
    if isinstance(err, TasklerqConflictError):
        return HomeAssistantError(
            f"Tasklerq уже обрабатывает этот capture. Повторите попытку позже. "
            f"Если задача не появилась, добавьте текст «{text}» снова.",
            translation_domain=DOMAIN,
            translation_key="capture_conflict",
            translation_placeholders={"text": text},
        )
    if isinstance(err, TasklerqValidationError) or (
        err.status is not None and 400 <= err.status < 500
    ):
        reason = _validation_reason(err)
        reason_text = f": {reason}" if reason else ""
        return HomeAssistantError(
            f"Tasklerq отклонил capture{reason_text}. Текст «{text}» не сохранён.",
            translation_domain=DOMAIN,
            translation_key="capture_invalid",
            translation_placeholders={"text": text, "reason": reason_text},
        )
    if isinstance(err, (TasklerqConnectionError, TasklerqServerError)):
        return HomeAssistantError(
            f"Tasklerq недоступен. Возможно, текст «{text}» не сохранён: "
            "проверьте список и повторите попытку.",
            translation_domain=DOMAIN,
            translation_key="capture_unavailable",
            translation_placeholders={"text": text},
        )
    return HomeAssistantError(
        f"Не удалось отправить текст «{text}» в Tasklerq. "  # noqa: RUF001
        "Возможно, он не сохранён: проверьте список и повторите попытку.",
        translation_domain=DOMAIN,
        translation_key="capture_failed",
        translation_placeholders={"text": text},
    )


def _confirm_capture_error(
    hass: HomeAssistant, entry: ConfigEntry, err: TasklerqApiError
) -> HomeAssistantError:
    """Русская ошибка подтверждения черновика."""
    common = _common_capture_error(hass, entry, err, text=None)
    if common is not None:
        return common
    if isinstance(err, TasklerqNotFoundError):
        return HomeAssistantError(
            "Черновик capture не найден в Tasklerq, либо Capture API выключен.",
            translation_domain=DOMAIN,
            translation_key="confirm_capture_not_found",
        )
    if isinstance(err, TasklerqGoneError):
        return HomeAssistantError(
            "Черновик capture устарел (старше 15 минут). Создайте новый черновик.",
            translation_domain=DOMAIN,
            translation_key="confirm_capture_expired",
        )
    if isinstance(err, TasklerqConflictError):
        return HomeAssistantError(
            "Черновик capture уже отменён или не может быть подтверждён.",
            translation_domain=DOMAIN,
            translation_key="confirm_capture_conflict",
        )
    if isinstance(err, TasklerqValidationError) or (
        err.status is not None and 400 <= err.status < 500
    ):
        reason = _validation_reason(err)
        reason_text = f": {reason}" if reason else ""
        return HomeAssistantError(
            f"Tasklerq отклонил элементы черновика{reason_text}.",
            translation_domain=DOMAIN,
            translation_key="confirm_capture_invalid",
            translation_placeholders={"reason": reason_text},
        )
    return HomeAssistantError(
        "Tasklerq недоступен. Черновик capture не подтверждён. "
        "Проверьте подключение и повторите попытку.",
        translation_domain=DOMAIN,
        translation_key="capture_action_unavailable",
    )


def _cancel_capture_error(
    hass: HomeAssistant, entry: ConfigEntry, err: TasklerqApiError
) -> HomeAssistantError:
    """Русская ошибка отмены черновика."""
    common = _common_capture_error(hass, entry, err, text=None)
    if common is not None:
        return common
    if isinstance(err, TasklerqNotFoundError):
        return HomeAssistantError(
            "Черновик capture не найден в Tasklerq, либо Capture API выключен.",
            translation_domain=DOMAIN,
            translation_key="cancel_capture_not_found",
        )
    if isinstance(err, TasklerqConflictError):
        return HomeAssistantError(
            "Capture уже применён. Отменить можно только ожидающий черновик.",
            translation_domain=DOMAIN,
            translation_key="cancel_capture_conflict",
        )
    return HomeAssistantError(
        "Tasklerq недоступен. Черновик capture не отменён. "
        "Проверьте подключение и повторите попытку.",
        translation_domain=DOMAIN,
        translation_key="capture_action_unavailable",
    )


def _undo_capture_error(
    hass: HomeAssistant, entry: ConfigEntry, err: TasklerqApiError
) -> HomeAssistantError:
    """Русская ошибка отмены последнего capture."""
    common = _common_capture_error(hass, entry, err, text=None)
    if common is not None:
        return common
    if isinstance(err, TasklerqNotFoundError):
        return HomeAssistantError(
            "Capture API в Tasklerq выключен.",
            translation_domain=DOMAIN,
            translation_key="undo_capture_disabled",
        )
    if isinstance(err, TasklerqConflictError):
        return HomeAssistantError(
            "Элементы последнего capture уже изменены или отменены; ничего не удалено.",
            translation_domain=DOMAIN,
            translation_key="undo_capture_conflict",
        )
    return HomeAssistantError(
        "Tasklerq недоступен. Не удалось отменить последний capture. "  # noqa: RUF001
        "Проверьте подключение и повторите попытку.",
        translation_domain=DOMAIN,
        translation_key="undo_capture_unavailable",
    )


async def async_handle_capture(call: ServiceCall) -> ServiceResponse:
    """Отправить текст в Tasklerq и вернуть результат разбора."""
    text = cast("str", call.data["text"])
    source = cast("str", call.data.get("source", "ha_automation"))
    mode = cast("str", call.data.get("mode", "auto"))
    hint = cast("str | None", call.data.get("hint"))
    list_id = cast("str | None", call.data.get("list_id"))
    idempotency_key = cast("str | None", call.data.get("idempotency_key"))
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    entry, client, coordinator = _select_runtime(call.hass, list_id=list_id)
    try:
        result = await client.async_capture(
            text=text,
            source=source,
            mode=mode,
            hint=hint,
            list_id=list_id,
            idempotency_key=idempotency_key,
        )
    except TasklerqApiError as err:
        raise _capture_error(call.hass, entry, err, text) from err

    if result.status == "applied":
        await coordinator.async_refresh()
    return _capture_response(result)


async def async_handle_confirm_capture(call: ServiceCall) -> ServiceResponse:
    """Подтвердить черновик capture и создать элементы."""
    draft_id = cast("str", call.data["draft_id"])
    entry, client, coordinator = _select_runtime(call.hass, list_id=None)
    try:
        result = await client.async_confirm_capture(draft_id)
    except TasklerqApiError as err:
        raise _confirm_capture_error(call.hass, entry, err) from err

    await coordinator.async_refresh()
    return _capture_response(result)


async def async_handle_cancel_capture(call: ServiceCall) -> ServiceResponse:
    """Отменить черновик capture без создания элементов."""
    draft_id = cast("str", call.data["draft_id"])
    entry, client, _ = _select_runtime(call.hass, list_id=None)
    try:
        result = await client.async_cancel_capture(draft_id)
    except TasklerqApiError as err:
        raise _cancel_capture_error(call.hass, entry, err) from err

    return {
        "status": result.status,
        "speech": result.speech,
        "capture_id": result.capture_id,
    }


async def async_handle_undo_last_capture(call: ServiceCall) -> ServiceResponse:
    """Отменить последний голосовой capture."""
    entry, client, coordinator = _select_runtime(call.hass, list_id=None)
    try:
        result = await client.async_undo_last_capture()
    except TasklerqApiError as err:
        raise _undo_capture_error(call.hass, entry, err) from err

    await coordinator.async_refresh()
    return {
        "count": result.removed,
        "speech": result.speech,
        "capture_id": result.capture_id,
    }


SERVICES: dict[str, ServiceHandler] = {
    SERVICE_CAPTURE: async_handle_capture,
    SERVICE_CONFIRM_CAPTURE: async_handle_confirm_capture,
    SERVICE_CANCEL_CAPTURE: async_handle_cancel_capture,
    SERVICE_UNDO_LAST_CAPTURE: async_handle_undo_last_capture,
}

SERVICE_SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_CAPTURE: CAPTURE_SCHEMA,
    SERVICE_CONFIRM_CAPTURE: DRAFT_SCHEMA,
    SERVICE_CANCEL_CAPTURE: DRAFT_SCHEMA,
    SERVICE_UNDO_LAST_CAPTURE: EMPTY_SCHEMA,
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
            schema=SERVICE_SCHEMAS[name],
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("Registered service %s.%s", DOMAIN, name)


def async_unload_services(hass: HomeAssistant) -> None:
    """Снять сервисы Tasklerq."""
    for name in SERVICES:
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
            _LOGGER.debug("Removed service %s.%s", DOMAIN, name)
