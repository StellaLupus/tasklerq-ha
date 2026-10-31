"""Config flow Tasklerq."""

from typing import Any

import aiohttp
import pytest
from custom_components.tasklerq.const import (
    CONF_CALENDAR_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_CALENDAR_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_USER,
    ConfigEntryState,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

BASE_URL = "http://tasklerq.test"
HOUSEHOLD_URL = f"{BASE_URL}/api/v1/household"
HOUSEHOLD_ID = "019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a5b"
OTHER_HOUSEHOLD_ID = "019a1b2c-4a5b-7c6d-8e9f-0a1b2c3d4e5f"
TOKEN = "tlq_test"
NEW_TOKEN = "tlq_new"


def _entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    """Вернуть состояние entry без сужения типа между проверками."""
    return entry.state


def _household_payload(
    household_id: str = HOUSEHOLD_ID, name: str = "Семья"
) -> dict[str, Any]:
    """Ответ GET /api/v1/household по примеру из ha-api.md."""
    return {
        "household": {
            "id": household_id,
            "name": name,
            "timezone": "Europe/Moscow",
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "deleted_at": None,
        },
        "members": [],
    }


async def _start_user_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Открыть config flow и вернуть форму шага user."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def _submit_user(
    hass: HomeAssistant,
    url: str = BASE_URL,
    token: str = TOKEN,
) -> ConfigFlowResult:
    """Пройти шаг user с указанными URL и токеном."""  # noqa: RUF002
    result = await _start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: url, CONF_TOKEN: token},
    )


async def _setup_entry(
    hass: HomeAssistant, aioclient_mock: Any, token: str = "tlq_old"
) -> MockConfigEntry:
    """Добавить и загрузить запись с известным household."""  # noqa: RUF002
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: token},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_user_form(hass: HomeAssistant) -> None:
    """Config flow показывает форму ввода URL и токена."""
    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] is None


async def test_user_success(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Успешная настройка сохраняет household, URL и токен."""
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())

    result = await _submit_user(hass, url=f"{BASE_URL}/")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Семья"
    assert result["data"] == {CONF_URL: BASE_URL, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == HOUSEHOLD_ID


async def test_user_invalid_token(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """401 показывает «Неверный токен» и не создаёт запись."""
    aioclient_mock.get(
        HOUSEHOLD_URL,
        status=401,
        json={"error": {"code": "unauthorized", "message": "Invalid token"}},
    )

    result = await _submit_user(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, "missing_scope"),
        (404, "household_not_found"),
        (500, "unknown"),
    ],
)
async def test_user_http_errors(
    hass: HomeAssistant,
    aioclient_mock: Any,
    status: int,
    error: str,
) -> None:
    """403, 404 и 5xx дают понятные ошибки формы."""
    aioclient_mock.get(HOUSEHOLD_URL, status=status, json={})

    result = await _submit_user(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientConnectionError(), TimeoutError()],
)
async def test_user_cannot_connect(
    hass: HomeAssistant, aioclient_mock: Any, exc: BaseException
) -> None:
    """Нет связи или таймаут показывают ошибку подключения."""
    aioclient_mock.get(HOUSEHOLD_URL, exc=exc)

    result = await _submit_user(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize(
    "url",
    [
        "tasklerq.test",
        "ftp://tasklerq.test",
        "http://",
        "http://[::1",
        "http://tasklerq.test:abc",
        "",
        "   ",
    ],
)
async def test_user_invalid_url(
    hass: HomeAssistant, aioclient_mock: Any, url: str
) -> None:
    """Некорректный URL отклоняется без запроса к серверу."""
    result = await _submit_user(hass, url=url)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}
    assert not aioclient_mock.mock_calls
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_duplicate_household(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Повторная настройка того же household прерывается."""
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())

    first = await _submit_user(hass)
    second = await _submit_user(hass)

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_success(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Новый токен сохраняется, запись перезагружается."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.clear_requests()
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == NEW_TOKEN

    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == f"Bearer {NEW_TOKEN}"


async def test_reauth_after_setup_auth_failure(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Успешный reauth поднимает запись после SETUP_ERROR."""
    aioclient_mock.get(HOUSEHOLD_URL, status=401, json={})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: "tlq_old"},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_test",
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _entry_state(entry) is ConfigEntryState.SETUP_ERROR

    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(reauth_flows) == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    result = await hass.config_entries.flow.async_configure(
        reauth_flows[0]["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    await hass.async_block_till_done()
    assert _entry_state(entry) is ConfigEntryState.LOADED
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == f"Bearer {NEW_TOKEN}"


async def test_reauth_wrong_household(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Токен другого household отклоняется, запись не меняется."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        HOUSEHOLD_URL, json=_household_payload(OTHER_HOUSEHOLD_ID, "Другая семья")
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_household"
    assert entry.data[CONF_TOKEN] == "tlq_old"


async def test_reauth_invalid_token(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """401 на новом токене показывает ошибку формы."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    aioclient_mock.clear_requests()
    aioclient_mock.get(HOUSEHOLD_URL, status=401, json={})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_TOKEN] == "tlq_old"


async def test_options_defaults(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Options предлагают 60 секунд и 30 дней по умолчанию."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    assert schema is not None
    assert schema({}) == {
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_CALENDAR_DAYS: DEFAULT_CALENDAR_DAYS,
    }


async def test_options_save_reloads_entry(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сохранение options обновляет запись и перезагружает её."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    aioclient_mock.clear_requests()
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 120, CONF_CALENDAR_DAYS: 45},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 120, CONF_CALENDAR_DAYS: 45}

    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        (CONF_SCAN_INTERVAL, 10, "invalid_scan_interval"),
        (CONF_SCAN_INTERVAL, 301, "invalid_scan_interval"),
        (CONF_CALENDAR_DAYS, 6, "invalid_calendar_days"),
        (CONF_CALENDAR_DAYS, 91, "invalid_calendar_days"),
    ],
)
async def test_options_out_of_range(
    hass: HomeAssistant,
    aioclient_mock: Any,
    key: str,
    value: int,
    error: str,
) -> None:
    """Значения вне диапазона отклоняются формой."""
    entry = await _setup_entry(hass, aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_CALENDAR_DAYS: DEFAULT_CALENDAR_DAYS,
            key: value,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {key: error}
    assert not entry.options
    schema = result["data_schema"]
    assert schema is not None
    assert schema({})[key] == value
