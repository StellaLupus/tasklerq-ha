"""Загрузка и выгрузка интеграции Tasklerq."""

import logging
from typing import Any

import aiohttp
import pytest
from custom_components.tasklerq.const import (
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    MIN_HOME_ASSISTANT_VERSION,
)
from custom_components.tasklerq.services import SERVICES
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

BASE_URL = "http://tasklerq.test"
HOUSEHOLD_URL = f"{BASE_URL}/api/v1/household"
HOUSEHOLD_ID = "019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a5b"


def _household_payload() -> dict[str, Any]:
    """Ответ GET /api/v1/household по примеру из ha-api.md."""
    return {
        "household": {
            "id": HOUSEHOLD_ID,
            "name": "Семья",
            "timezone": "Europe/Moscow",
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "deleted_at": None,
        },
        "members": [],
    }


def _entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    """Вернуть состояние entry без сужения типа между проверками."""
    return entry.state


def _mock_entry(
    entry_id: str = "tasklerq_test",
    unique_id: str = HOUSEHOLD_ID,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: "tlq_test"},
        unique_id=unique_id,
        entry_id=entry_id,
    )


async def test_setup_and_unload_registers_services(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Интеграция загружается и регистрирует сервисы, выгрузка их снимает."""
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    entry = _mock_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _entry_state(entry) is ConfigEntryState.LOADED
    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    state_after_unload: ConfigEntryState = entry.state
    assert state_after_unload is ConfigEntryState.NOT_LOADED
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


async def test_unload_one_entry_keeps_services_for_another(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Выгрузка одной записи не снимает сервисы, пока загружена другая."""
    aioclient_mock.get(HOUSEHOLD_URL, json=_household_payload())
    first = _mock_entry(entry_id="tasklerq_first", unique_id=HOUSEHOLD_ID)
    second = _mock_entry(
        entry_id="tasklerq_second",
        unique_id="019a1b2c-4a5b-7c6d-8e9f-0a1b2c3d4e5f",
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert _entry_state(first) is ConfigEntryState.LOADED
    assert _entry_state(second) is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()

    assert _entry_state(first) is ConfigEntryState.NOT_LOADED
    assert _entry_state(second) is ConfigEntryState.LOADED
    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()

    assert _entry_state(second) is ConfigEntryState.NOT_LOADED
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


async def test_incompatible_home_assistant_version(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При версии HA ниже минимальной интеграция падает в SETUP_ERROR."""
    monkeypatch.setattr("custom_components.tasklerq.HA_VERSION", "2025.6.0")

    entry = _mock_entry()
    entry.add_to_hass(hass)

    with caplog.at_level(logging.WARNING, logger="custom_components.tasklerq"):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason is not None
    assert MIN_HOME_ASSISTANT_VERSION in entry.reason
    assert MIN_HOME_ASSISTANT_VERSION in caplog.text
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """401 при загрузке запускает reauth flow."""
    aioclient_mock.get(HOUSEHOLD_URL, status=401, json={})
    entry = _mock_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


@pytest.mark.parametrize("status", [403, 404])
async def test_setup_permanent_http_error(
    hass: HomeAssistant, aioclient_mock: Any, status: int
) -> None:
    """403 и 404 при загрузке дают постоянную ошибку настройки."""
    aioclient_mock.get(HOUSEHOLD_URL, status=status, json={})
    entry = _mock_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason is not None
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


async def test_setup_connection_error_schedules_retry(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Нет связи с Tasklerq переводит запись в SETUP_RETRY."""  # noqa: RUF002
    aioclient_mock.get(HOUSEHOLD_URL, exc=aiohttp.ClientConnectionError())
    entry = _mock_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)

    entry.async_cancel_retry_setup()


async def test_setup_server_error_schedules_retry(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """5xx при загрузке переводит запись в SETUP_RETRY."""
    aioclient_mock.get(HOUSEHOLD_URL, status=500, json={})
    entry = _mock_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)

    entry.async_cancel_retry_setup()
