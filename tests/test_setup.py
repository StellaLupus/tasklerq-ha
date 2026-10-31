"""Загрузка и выгрузка интеграции Tasklerq."""

import logging

import pytest
from custom_components.tasklerq import MIN_HOME_ASSISTANT_VERSION, SERVICES
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    """Вернуть состояние entry без сужения типа между проверками."""
    return entry.state


def _mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://127.0.0.1:18080", CONF_TOKEN: "tlq_test"},
        entry_id="tasklerq_test",
    )


async def test_setup_and_unload_registers_services(hass: HomeAssistant) -> None:
    """Интеграция загружается и регистрирует сервисы, выгрузка их снимает."""
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


async def test_incompatible_home_assistant_version(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При версии HA ниже минимальной интеграция не загружается."""
    monkeypatch.setattr("custom_components.tasklerq.HA_VERSION", "2025.6.0")

    entry = _mock_entry()
    entry.add_to_hass(hass)

    with caplog.at_level(logging.WARNING, logger="custom_components.tasklerq"):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.LOADED
    assert MIN_HOME_ASSISTANT_VERSION in caplog.text
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service)
