"""Офлайн-проверка примера пакета Assist Tasklerq."""

from pathlib import Path
from typing import Any

import aiohttp
import pytest
import yaml
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent as intent_helper
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .const import (
    BASE_URL,
    HOUSEHOLD_ID,
    LIST_ID,
    SHOPPING_LIST_ID,
    TOKEN,
    household_payload,
    snapshot_payload,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "assist"
PACKAGE_PATH = EXAMPLES_DIR / "packages" / "tasklerq_assist.yaml"
SENTENCES_PATH = EXAMPLES_DIR / "custom_sentences" / "ru" / "tasklerq.yaml"

CAPTURE_URL = f"{BASE_URL}/api/v1/capture"
UNDO_LAST_URL = f"{BASE_URL}/api/v1/captures/undo-last"
SNAPSHOT_URL = f"{BASE_URL}/api/v1/snapshot"

CAPTURE_ID = "019a1b2c-e5f6-7a7b-8c8d-92a3b4c5d6e7"

TASK_FALLBACK = (
    "Не удалось добавить задачу. Возможно, она не сохранена: "  # noqa: RUF001
    "проверьте список в Tasklerq."
)
SHOPPING_FALLBACK = (
    "Не удалось добавить покупки. Возможно, они не сохранены: "  # noqa: RUF001
    "проверьте список в Tasklerq."
)
UNDO_FALLBACK = "Не удалось отменить последнее добавление. Проверьте Tasklerq."  # noqa: RUF001


def _load_yaml(path: Path) -> dict[str, Any]:
    """Загрузить YAML примера как обычный файл конфигурации HA."""
    with path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file)
    assert isinstance(data, dict)
    return data


def _capture_item(*, item_id: str, title: str, list_id: str) -> dict[str, Any]:
    """Элемент ответа capture."""
    return {
        "id": item_id,
        "title": title,
        "list_id": list_id,
        "kind": "shopping" if list_id == SHOPPING_LIST_ID else "tasks",
        "due_date": None,
        "due_at": None,
    }


def _capture_payload(speech: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Ответ POST /api/v1/capture."""
    return {
        "capture_id": CAPTURE_ID,
        "status": "applied",
        "fallback": False,
        "speech": speech,
        "items": items,
        "draft_id": None,
        "expires_at": None,
        "clarifications": [],
    }


def _error_payload(message: str) -> dict[str, Any]:
    """Конверт ошибки API."""
    return {"error": {"code": "error", "message": message, "details": {}}}


def _calls(aioclient_mock: Any, method: str, path: str) -> list[Any]:
    """Запросы указанного метода и пути."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


async def _setup_entry(hass: HomeAssistant, aioclient_mock: Any) -> None:
    """Добавить и загрузить запись Tasklerq."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/household",
        json=household_payload(household_id=HOUSEHOLD_ID),
    )
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(lists=[], items=[]))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: BASE_URL, CONF_TOKEN: TOKEN},
        unique_id=HOUSEHOLD_ID,
        title="Семья",
        entry_id="tasklerq_assist_test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _setup_intent_script(hass: HomeAssistant) -> None:
    """Подключить intent_script из примера пакета."""
    package = _load_yaml(PACKAGE_PATH)
    assert await async_setup_component(
        hass, "intent_script", {"intent_script": package["intent_script"]}
    )


async def _handle(
    hass: HomeAssistant, intent_type: str, text: str | None = None
) -> str:
    """Обработать интент и вернуть озвученную фразу."""
    slots = {} if text is None else {"tasklerq_text": {"value": text}}
    response = await intent_helper.async_handle(
        hass, "conversation", intent_type, slots
    )
    return str(response.speech["plain"]["speech"])


def test_custom_sentences_match_intent_script() -> None:
    """Фразы примера привязаны к правильным интентам и совпадают с пакетом."""  # noqa: RUF002
    sentences = _load_yaml(SENTENCES_PATH)
    package = _load_yaml(PACKAGE_PATH)

    assert sentences["lists"]["tasklerq_text"]["wildcard"] is True
    actual = {
        intent_name: {
            sentence for block in intent["data"] for sentence in block["sentences"]
        }
        for intent_name, intent in sentences["intents"].items()
    }
    assert actual == {
        "TasklerqAddTask": {
            "добавь задачу {tasklerq_text}",
            "запиши задачу {tasklerq_text}",
            "напомни {tasklerq_text}",
        },
        "TasklerqAddShopping": {
            "купи {tasklerq_text}",
            "добавь в покупки {tasklerq_text}",
            "в список покупок {tasklerq_text}",
        },
        "TasklerqUndo": {
            "отмени последнее добавление",
            "убери последнюю задачу",
        },
    }
    assert set(sentences["intents"]) == set(package["intent_script"])


async def test_example_package_loads_as_intent_script(hass: HomeAssistant) -> None:
    """Пакет примера проходит схему intent_script HA 2026.10."""
    package = _load_yaml(PACKAGE_PATH)

    assert "conversation" not in package
    assert set(package["intent_script"]) == {
        "TasklerqAddTask",
        "TasklerqAddShopping",
        "TasklerqUndo",
    }
    await _setup_intent_script(hass)


@pytest.mark.parametrize(
    ("intent_type", "text", "hint", "speech", "items"),
    [
        (
            "TasklerqAddTask",
            "позвонить маме завтра",
            "tasks",
            "Добавил задачу: позвонить маме.",
            [
                _capture_item(
                    item_id="019a1b2c-8e9f-7a0b-9c1d-4d5e6f708192",
                    title="позвонить маме",
                    list_id=LIST_ID,
                )
            ],
        ),
        (
            "TasklerqAddShopping",
            "молоко и хлеб",
            "shopping",
            "Добавил две покупки",
            [
                _capture_item(
                    item_id="019a1b2c-9f0a-7b1c-8d2e-5e6f708192a3",
                    title="молоко",
                    list_id=SHOPPING_LIST_ID,
                ),
                _capture_item(
                    item_id="019a1b2c-a0b1-7c2d-9e3f-6f708192a3b4",
                    title="хлеб",
                    list_id=SHOPPING_LIST_ID,
                ),
            ],
        ),
    ],
)
async def test_capture_intent_calls_capture_and_speaks_response(
    hass: HomeAssistant,
    aioclient_mock: Any,
    intent_type: str,
    text: str,
    hint: str,
    speech: str,
    items: list[dict[str, Any]],
) -> None:
    """Интент вызывает capture, а Assist озвучивает speech сервиса."""  # noqa: RUF002
    await _setup_entry(hass, aioclient_mock)
    await _setup_intent_script(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, json=_capture_payload(speech, items))
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(lists=[], items=[]))

    assert await _handle(hass, intent_type, text) == speech

    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 1
    body = calls[0][2]
    assert body["text"] == text
    assert body["source"] == "ha_voice"
    assert body["mode"] == "auto"
    assert body["hint"] == hint


async def test_undo_intent_speaks_response(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Отмена вызывает undo-last и озвучивает ответ сервиса."""
    await _setup_entry(hass, aioclient_mock)
    await _setup_intent_script(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        UNDO_LAST_URL,
        json={
            "capture_id": CAPTURE_ID,
            "status": "undone",
            "removed": 2,
            "speech": "Отменил добавление: 2 элемента.",
        },
    )
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(lists=[], items=[]))

    assert await _handle(hass, "TasklerqUndo") == "Отменил добавление: 2 элемента."
    assert len(_calls(aioclient_mock, "POST", "/api/v1/captures/undo-last")) == 1


async def test_undo_intent_speaks_nothing_to_undo(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """«Нечего отменять» — тоже озвучиваемая фраза, а не ошибка."""  # noqa: RUF002
    await _setup_entry(hass, aioclient_mock)
    await _setup_intent_script(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        UNDO_LAST_URL,
        json={
            "capture_id": None,
            "status": "undone",
            "removed": 0,
            "speech": "Нечего отменять: голосовых добавлений не было.",
        },
    )
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload(lists=[], items=[]))

    assert (
        await _handle(hass, "TasklerqUndo")
        == "Нечего отменять: голосовых добавлений не было."
    )


@pytest.mark.parametrize(
    ("intent_type", "url", "fallback"),
    [
        ("TasklerqAddTask", CAPTURE_URL, TASK_FALLBACK),
        ("TasklerqAddShopping", CAPTURE_URL, SHOPPING_FALLBACK),
        ("TasklerqUndo", UNDO_LAST_URL, UNDO_FALLBACK),
    ],
)
async def test_tasklerq_unavailable_speaks_fallback(
    hass: HomeAssistant,
    aioclient_mock: Any,
    intent_type: str,
    url: str,
    fallback: str,
) -> None:
    """Сбой Tasklerq не роняет Assist: озвучивается понятная фраза."""
    await _setup_entry(hass, aioclient_mock)
    await _setup_intent_script(hass)
    aioclient_mock.clear_requests()

    async def _fail(method: str, request_url: Any, data: Any) -> Any:
        raise aiohttp.ClientConnectionError()

    aioclient_mock.post(url, side_effect=_fail)

    text = None if intent_type == "TasklerqUndo" else "молоко"
    assert await _handle(hass, intent_type, text) == fallback


@pytest.mark.parametrize(
    ("intent_type", "url", "status", "fallback"),
    [
        ("TasklerqAddTask", CAPTURE_URL, 413, TASK_FALLBACK),
        ("TasklerqUndo", UNDO_LAST_URL, 409, UNDO_FALLBACK),
    ],
)
async def test_tasklerq_rejection_speaks_neutral_fallback(
    hass: HomeAssistant,
    aioclient_mock: Any,
    intent_type: str,
    url: str,
    status: int,
    fallback: str,
) -> None:
    """Отказ Tasklerq не выдаётся за недоступность: фраза нейтральная."""
    await _setup_entry(hass, aioclient_mock)
    await _setup_intent_script(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=status, json=_error_payload("rejected"))

    text = None if intent_type == "TasklerqUndo" else "молоко"
    speech = await _handle(hass, intent_type, text)

    assert speech == fallback
    assert "недоступен" not in speech
