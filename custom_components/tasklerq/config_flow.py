"""Config flow Tasklerq."""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback

from .api import (
    TasklerqApiClient,
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqConnectionError,
    TasklerqNotFoundError,
    TasklerqPermissionError,
)
from .const import (
    CONF_CALENDAR_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_CALENDAR_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CALENDAR_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_CALENDAR_DAYS,
    MIN_SCAN_INTERVAL,
)


class _ConfigFlowApiError(Exception):
    """Ошибка клиента Tasklerq с ключом для формы."""  # noqa: RUF002

    def __init__(self, error_key: str) -> None:
        """Сохранить ключ ошибки формы."""
        super().__init__(error_key)
        self.error_key = error_key


_LOCAL_HOST_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal")
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _parse_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Разобрать IPv4 в любой форме inet_aton или вернуть None."""
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError, ValueError:
        return None


def _is_local_host(host: str) -> bool:
    """Вернуть True, если хост относится к локальной сети.

    Однословное имя считается локальным (Docker-имя), только если его нельзя
    разобрать как IPv4 ни в одной форме inet_aton: десятичной, восьмеричной с
    ведущим нулём, шестнадцатеричной 0x, из 1–4 частей. Числовые формы
    локальных адресов, например http://2130706433, считаются локальными.
    Имя с завершающей точкой — FQDN: после её снятия однословный хост
    локальный, только если он в списке разрешённых (localhost).
    Однословные имена принимаются только в ASCII: IDNA-нормализация иначе
    превращает, например, полноширинные цифры в IPv4-адрес.
    """  # noqa: RUF002
    host = host.lower()
    if not host:
        return False
    is_fqdn = host.endswith(".")
    host = host.rstrip(".")
    if not host:
        return False

    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = _parse_ipv4(host)
    if address is None:
        with contextlib.suppress(ValueError):
            address = ipaddress.ip_address(host)
    if address is not None:
        return (
            address.is_loopback
            or address.is_link_local
            or any(address in network for network in _PRIVATE_NETWORKS)
        )
    if host == "localhost":
        return True
    # Docker-имя без домена, например tasklerq; FQDN tasklerq. сюда не входит.
    if "." not in host:
        return host.isascii() and not is_fqdn
    return host.endswith(_LOCAL_HOST_SUFFIXES)


def _requires_https(url: str) -> bool:
    """Вернуть True, если HTTP-адрес указывает вне локальной сети."""
    parsed = urlsplit(url)
    if parsed.scheme != "http":
        return False
    return not _is_local_host(parsed.hostname or "")


def _normalize_url(raw_url: str) -> str | None:
    """Проверить URL и убрать завершающий слэш."""
    url = raw_url.strip().rstrip("/")
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # невалидный порт поднимает ValueError
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return url


async def _async_fetch_household(
    hass: HomeAssistant, url: str, token: str
) -> dict[str, Any]:
    """Получить household, превратив ошибку клиента в ключ формы."""
    try:
        return await TasklerqApiClient(hass, url, token).async_get_household()
    except TasklerqAuthenticationError as err:
        raise _ConfigFlowApiError("invalid_auth") from err
    except TasklerqPermissionError as err:
        raise _ConfigFlowApiError("missing_scope") from err
    except TasklerqNotFoundError as err:
        raise _ConfigFlowApiError("household_not_found") from err
    except TasklerqConnectionError as err:
        raise _ConfigFlowApiError("cannot_connect") from err
    except TasklerqApiError as err:
        raise _ConfigFlowApiError("unknown") from err


def _user_schema() -> vol.Schema:
    """Схема шага ввода URL и токена."""
    return vol.Schema(
        {
            vol.Required(CONF_URL): str,
            vol.Required(CONF_TOKEN): str,
        }
    )


def _reauth_schema() -> vol.Schema:
    """Схема шага повторной аутентификации."""
    return vol.Schema({vol.Required(CONF_TOKEN): str})


def _options_schema(values: Mapping[str, Any]) -> vol.Schema:
    """Схема options с текущими значениями по умолчанию."""  # noqa: RUF002
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.Coerce(int),
            vol.Required(
                CONF_CALENDAR_DAYS,
                default=values.get(CONF_CALENDAR_DAYS, DEFAULT_CALENDAR_DAYS),
            ): vol.Coerce(int),
        }
    )


class TasklerqConfigFlow(ConfigFlow, domain=DOMAIN):
    """Настройка подключения к Tasklerq."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TasklerqOptionsFlow:
        """Вернуть options flow Tasklerq."""
        return TasklerqOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Запросить URL и API-токен Tasklerq."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            if url is None:
                errors["base"] = "invalid_url"
            elif _requires_https(url):
                errors["base"] = "https_required"
            else:
                token = user_input[CONF_TOKEN].strip()
                try:
                    household = await _async_fetch_household(self.hass, url, token)
                except _ConfigFlowApiError as err:
                    errors["base"] = err.error_key
                else:
                    await self.async_set_unique_id(household["id"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=household["name"],
                        data={CONF_URL: url, CONF_TOKEN: token},
                    )

        # HA типизирует data_schema как probatio.Schema; vol.Schema — другой тип.
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),  # type: ignore[arg-type]
            errors=errors or None,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Запустить повторную аутентификацию записи."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Проверить новый токен и обновить запись."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                household = await _async_fetch_household(
                    self.hass, entry.data[CONF_URL], token
                )
            except _ConfigFlowApiError as err:
                errors["base"] = err.error_key
            else:
                if household["id"] != entry.unique_id:
                    return self.async_abort(reason="wrong_household")
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_TOKEN: token},
                )
                if (
                    entry.state is not ConfigEntryState.LOADED
                    or not entry.update_listeners
                ):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        # HA типизирует data_schema как probatio.Schema; vol.Schema — другой тип.
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),  # type: ignore[arg-type]
            errors=errors or None,
        )


class TasklerqOptionsFlow(OptionsFlow):
    """Options Tasklerq: polling и горизонт календаря."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Запросить интервал polling и горизонт календаря."""
        errors: dict[str, str] = {}

        if user_input is not None:
            scan_interval = user_input[CONF_SCAN_INTERVAL]
            calendar_days = user_input[CONF_CALENDAR_DAYS]
            if not MIN_SCAN_INTERVAL <= scan_interval <= MAX_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "invalid_scan_interval"
            if not MIN_CALENDAR_DAYS <= calendar_days <= MAX_CALENDAR_DAYS:
                errors[CONF_CALENDAR_DAYS] = "invalid_calendar_days"
            if not errors:
                return self.async_create_entry(data=user_input)

        values = user_input if user_input is not None else self.config_entry.options
        # HA типизирует data_schema как probatio.Schema; vol.Schema — другой тип.
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(values),  # type: ignore[arg-type]
            errors=errors or None,
        )
