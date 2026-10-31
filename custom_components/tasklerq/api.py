"""Асинхронный клиент API Tasklerq."""

from typing import Any, cast

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class TasklerqApiError(Exception):
    """Базовая ошибка клиента Tasklerq."""


class TasklerqConnectionError(TasklerqApiError):
    """Tasklerq недоступен: нет связи или истёк таймаут."""


class TasklerqAuthenticationError(TasklerqApiError):
    """Tasklerq отклонил токен (401)."""


class TasklerqPermissionError(TasklerqApiError):
    """Нет нужного scope (403)."""


class TasklerqNotFoundError(TasklerqApiError):
    """Объект не найден (404)."""


class TasklerqServerError(TasklerqApiError):
    """Tasklerq вернул ошибку сервера или неожиданный ответ."""


class TasklerqApiClient:
    """Минимальный клиент Tasklerq для config flow."""

    def __init__(self, hass: HomeAssistant, url: str, token: str) -> None:
        """Создать клиент с базовым URL и API-токеном."""  # noqa: RUF002
        self._hass = hass
        self._url = url.rstrip("/")
        self._token = token

    async def async_get_household(self) -> dict[str, Any]:
        """Получить household текущего токена."""
        session = async_get_clientsession(self._hass)
        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._url}/api/v1/household"

        try:
            async with session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status == 401:
                    raise TasklerqAuthenticationError("Tasklerq rejected the API token")
                if response.status == 403:
                    raise TasklerqPermissionError(
                        "Tasklerq token is missing the required scope"
                    )
                if response.status == 404:
                    raise TasklerqNotFoundError("Tasklerq household was not found")
                if response.status != 200:
                    raise TasklerqServerError(
                        f"Tasklerq returned HTTP {response.status}"
                    )

                try:
                    payload: Any = await response.json(content_type=None)
                except ValueError as err:
                    raise TasklerqServerError(
                        "Tasklerq returned an invalid JSON response"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TasklerqConnectionError("Could not connect to Tasklerq") from err

        household = payload.get("household") if isinstance(payload, dict) else None
        if not isinstance(household, dict):
            raise TasklerqServerError("Tasklerq response does not contain a household")

        household_id = household.get("id")
        name = household.get("name")
        if not isinstance(household_id, str) or not isinstance(name, str):
            raise TasklerqServerError("Tasklerq response has an invalid household")

        return cast("dict[str, Any]", household)
