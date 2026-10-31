"""Diagnostics Tasklerq: конфигурация, сущности и отсутствие секретов."""

import json
from typing import Any

import aiohttp
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from custom_components.tasklerq.diagnostics import (
    _url_without_credentials,
    async_get_config_entry_diagnostics,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .const import (
    ASSIGNEE_ID,
    BASE_URL,
    HOUSEHOLD_ID,
    HOUSEHOLD_URL,
    ITEM_ID,
    LIST_ID,
    SNAPSHOT_URL,
    TOKEN,
    household_payload,
    item_payload,
    list_payload,
    member_payload,
    snapshot_payload,
)

TASK_TITLE = "Купить молоко"
TASK_NOTES = "Секретное описание"
MEMBER_NAME = "Алексей"
LIST_NAME = "Семейные дела"


def _coordinator(entry: MockConfigEntry) -> Any:
    """Вернуть координатор загруженной записи."""
    return entry.runtime_data.coordinator


async def _setup_entry(
    hass: HomeAssistant,
    aioclient_mock: Any,
    *,
    url: str = BASE_URL,
) -> MockConfigEntry:
    """Добавить и загрузить запись с задачами и участником."""  # noqa: RUF002
    aioclient_mock.get(
        HOUSEHOLD_URL,
        json=household_payload(members=[member_payload(ASSIGNEE_ID, MEMBER_NAME)]),
    )
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            lists=[list_payload(LIST_ID, name=LIST_NAME)],
            items=[
                item_payload(
                    ITEM_ID,
                    title=TASK_TITLE,
                    notes=TASK_NOTES,
                    assignee_user_id=ASSIGNEE_ID,
                )
            ],
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: url, CONF_TOKEN: TOKEN},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_diagnostics_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def _serialized(data: dict[str, Any]) -> str:
    """Сериализовать diagnostics как обычный JSON-файл."""
    return json.dumps(data, ensure_ascii=False)


async def test_diagnostics_contains_configuration_and_entities(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Штатная диагностика содержит настройки, сущности и счётчики."""
    entry = await _setup_entry(hass, aioclient_mock)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["url"] == BASE_URL
    assert diagnostics["version"] == "0.1.0"
    assert diagnostics["scan_interval"] == 60
    assert diagnostics["calendar_days"] == 30
    assert diagnostics["lists_count"] == 1
    assert diagnostics["items_count"] == 1
    assert diagnostics["last_successful_sync"] is not None

    entities = {entity["platform"]: entity for entity in diagnostics["entities"]}
    assert entities["todo"]["available"] is True
    assert entities["calendar"]["available"] is True

    assert diagnostics["last_sync_error"] is None
    payload = _serialized(diagnostics)
    assert TOKEN not in payload
    assert TASK_TITLE not in payload
    assert TASK_NOTES not in payload
    assert MEMBER_NAME not in payload
    assert LIST_NAME not in payload


async def test_diagnostics_redacts_url_credentials(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Userinfo из URL Tasklerq не попадает в diagnostics."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        url="http://user:pass@tasklerq.test",
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["url"] == BASE_URL
    payload = _serialized(diagnostics)
    assert "user" not in payload
    assert "pass" not in payload


def test_url_without_credentials_strips_userinfo_query_and_fragment() -> None:
    """Userinfo, query и fragment не попадают в diagnostics URL."""
    assert (
        _url_without_credentials(
            "http://user:pass@tasklerq.test/base?token=secret#frag"
        )
        == "http://tasklerq.test/base"
    )
    assert _url_without_credentials("http://[broken") == ""


async def test_diagnostics_reports_last_sync_error_code_only(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Ошибка синхронизации попадает как код и время, без тела ответа."""
    entry = await _setup_entry(hass, aioclient_mock)
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        SNAPSHOT_URL,
        status=500,
        json={
            "error": {
                "code": "internal_error",
                "message": "Тело ответа с персональными данными",  # noqa: RUF001
            }
        },
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["last_sync_error"]["code"] == "500"
    assert diagnostics["last_sync_error"]["at"] is not None
    assert diagnostics["last_successful_sync"] is not None
    assert all(entity["available"] is False for entity in diagnostics["entities"])

    payload = _serialized(diagnostics)
    assert "Тело ответа с персональными данными" not in payload  # noqa: RUF001
    assert TASK_TITLE not in payload
    assert TASK_NOTES not in payload
    assert MEMBER_NAME not in payload
    assert TOKEN not in payload

    # Ошибка сохраняется и после восстановления, пока её не сменит новая.
    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["last_sync_error"]["code"] == "500"


async def test_diagnostics_reports_network_error_type(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сетевая ошибка попадает в diagnostics именем типа без текста исключения."""
    entry = await _setup_entry(hass, aioclient_mock)
    coordinator = _coordinator(entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, exc=aiohttp.ClientConnectionError("secret"))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["last_sync_error"]["code"] == "TasklerqConnectionError"
    assert "secret" not in _serialized(diagnostics)
