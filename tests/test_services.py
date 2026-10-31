"""Сервисы capture Tasklerq: запросы, ответы, ошибки и идемпотентность."""

from typing import Any, cast

import aiohttp
import probatio
import pytest
from custom_components.tasklerq import api as api_module
from custom_components.tasklerq.const import CONF_TOKEN, CONF_URL, DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from .const import (
    BASE_URL,
    HOUSEHOLD_ID,
    ITEM_ID,
    LIST_ID,
    OTHER_HOUSEHOLD_ID,
    OTHER_LIST_ID,
    SNAPSHOT_URL,
    TOKEN,
    household_payload,
    item_payload,
    list_payload,
    snapshot_payload,
)

SECOND_BASE_URL = "http://tasklerq-two.test"
CAPTURE_URL = f"{BASE_URL}/api/v1/capture"
SECOND_CAPTURE_URL = f"{SECOND_BASE_URL}/api/v1/capture"
DRAFT_CAPTURE_URL = f"{BASE_URL}/api/v1/captures"
UNDO_LAST_URL = f"{BASE_URL}/api/v1/captures/undo-last"

CAPTURE_ID = "019a1b2c-e5f6-7a7b-8c8d-92a3b4c5d6e7"
ITEM_TITLE = "позвонить маме завтра"


def _capture_item(
    *,
    item_id: str | None = ITEM_ID,
    title: str = "позвонить маме",
    list_id: str | None = LIST_ID,
) -> dict[str, Any]:
    """Элемент ответа capture."""
    return {
        "id": item_id,
        "title": title,
        "list_id": list_id,
        "kind": "tasks",
        "due_date": "2026-11-01",
        "due_at": None,
    }


def _capture_payload(
    *,
    status: str = "applied",
    capture_id: str = CAPTURE_ID,
    speech: str = "Добавил задачу: позвонить маме.",
    items: list[dict[str, Any]] | None = None,
    draft_id: str | None = None,
    fallback: bool = False,
) -> dict[str, Any]:
    """Ответ POST /capture или confirm."""
    return {
        "capture_id": capture_id,
        "status": status,
        "fallback": fallback,
        "speech": speech,
        "items": [_capture_item()] if items is None else items,
        "draft_id": draft_id,
        "expires_at": "2026-11-03T12:15:00Z" if draft_id else None,
        "clarifications": [],
    }


def _error_payload(message: str = "error") -> dict[str, Any]:
    """Конверт ошибки API."""
    return {"error": {"code": "error", "message": message, "details": {}}}


def _calls(aioclient_mock: Any, method: str, path: str) -> list[Any]:
    """Запросы указанного метода и пути."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


async def _setup_entry(
    hass: HomeAssistant,
    aioclient_mock: Any,
    *,
    entry_id: str = "tasklerq_capture_test",
    base_url: str = BASE_URL,
    household_id: str = HOUSEHOLD_ID,
    lists: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> MockConfigEntry:
    """Добавить и загрузить запись Tasklerq."""
    aioclient_mock.get(
        f"{base_url}/api/v1/household",
        json=household_payload(household_id=household_id),
    )
    aioclient_mock.get(
        f"{base_url}/api/v1/snapshot",
        json=snapshot_payload(
            lists=[list_payload()] if lists is None else lists,
            items=[] if items is None else items,
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: base_url, CONF_TOKEN: TOKEN},
        unique_id=household_id,
        title="Семья",
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _call_capture(
    hass: HomeAssistant, data: dict[str, Any], *, return_response: bool = True
) -> Any:
    """Вызвать tasklerq.capture."""
    return await hass.services.async_call(
        DOMAIN,
        "capture",
        data,
        blocking=True,
        return_response=return_response,
    )


ACTION_CALLS = [
    (
        "confirm_capture",
        {"draft_id": CAPTURE_ID},
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/confirm",
    ),
    (
        "cancel_capture",
        {"draft_id": CAPTURE_ID},
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/cancel",
    ),
    ("undo_last_capture", {}, UNDO_LAST_URL),
]


class _FakeLoop:
    """Управляемые часы для проверки бюджета повторов."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        """Текущее время бюджета."""
        return self.now


class _FakeAsyncio:
    """Минимальная замена asyncio и управляемое время."""

    def __init__(self, loop: _FakeLoop) -> None:
        self._loop = loop

    def get_running_loop(self) -> _FakeLoop:
        """Вернуть управляемые часы."""
        return self._loop

    async def sleep(self, delay: float) -> None:
        """Продвинуть часы на время ожидания."""
        self._loop.now += delay


async def test_capture_applied_returns_response_and_refreshes(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Capture создаёт задачу, возвращает ответ и обновляет координатор."""
    entry = await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, json=_capture_payload())
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(
            items=[
                item_payload(
                    ITEM_ID,
                    title="позвонить маме",
                    due_date="2026-11-01",
                )
            ]
        ),
    )

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "applied"
    assert response["speech"] == "Добавил задачу: позвонить маме."
    assert response["capture_id"] == CAPTURE_ID
    assert response["draft_id"] is None
    assert response["fallback"] is False
    assert response["items"][0]["id"] == ITEM_ID

    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 1
    body = calls[0][2]
    headers = calls[0][3]
    assert body["text"] == ITEM_TITLE
    assert body["source"] == "ha_automation"
    assert body["mode"] == "auto"
    assert body["idempotency_key"] == headers["Idempotency-Key"]
    assert (
        entry.runtime_data.coordinator.data.items[ITEM_ID]["title"] == "позвонить маме"
    )


async def test_capture_draft_confirm_refreshes(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Черновик возвращает draft_id, confirm создаёт элементы и обновляет список."""
    entry = await _setup_entry(hass, aioclient_mock, items=[])
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        json=_capture_payload(
            status="draft",
            capture_id=CAPTURE_ID,
            items=[_capture_item(item_id=None)],
            draft_id=CAPTURE_ID,
            speech="Сохранил черновик.",
        ),
    )

    draft = await _call_capture(hass, {"text": ITEM_TITLE, "mode": "draft"})

    assert draft["status"] == "draft"
    assert draft["draft_id"] == CAPTURE_ID
    assert draft["items"][0]["id"] is None
    assert entry.runtime_data.coordinator.data.items == {}
    assert not _calls(aioclient_mock, "GET", "/api/v1/snapshot")

    aioclient_mock.post(
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/confirm",
        json=_capture_payload(speech="Добавил задачу."),
    )
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(items=[item_payload(ITEM_ID, title="позвонить маме")]),
    )

    confirmed = cast(
        "dict[str, Any]",
        await hass.services.async_call(
            DOMAIN,
            "confirm_capture",
            {"draft_id": CAPTURE_ID},
            blocking=True,
            return_response=True,
        ),
    )

    assert confirmed["status"] == "applied"
    assert confirmed["capture_id"] == CAPTURE_ID
    assert confirmed["items"][0]["id"] == ITEM_ID
    assert ITEM_ID in entry.runtime_data.coordinator.data.items
    assert (
        len(_calls(aioclient_mock, "POST", f"/api/v1/captures/{CAPTURE_ID}/confirm"))
        == 1
    )


async def test_confirm_capture_gone_is_clear_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """410 при подтверждении черновика — понятная русская ошибка."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/confirm",
        status=410,
        json=_error_payload("Draft expired"),
    )

    with pytest.raises(HomeAssistantError, match="устарел"):
        await hass.services.async_call(
            DOMAIN,
            "confirm_capture",
            {"draft_id": CAPTURE_ID},
            blocking=True,
            return_response=True,
        )


async def test_cancel_capture_returns_status_and_speech(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Отмена черновика возвращает status, speech и capture_id."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/cancel",
        json={
            "capture_id": CAPTURE_ID,
            "status": "cancelled",
            "speech": "Отменил черновик.",
        },
    )

    response = await hass.services.async_call(
        DOMAIN,
        "cancel_capture",
        {"draft_id": CAPTURE_ID},
        blocking=True,
        return_response=True,
    )

    assert response == {
        "status": "cancelled",
        "speech": "Отменил черновик.",
        "capture_id": CAPTURE_ID,
    }


async def test_confirm_capture_conflict_is_clear_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """409 при подтверждении черновика — понятная ошибка."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/confirm",
        status=409,
        json=_error_payload("Capture was undone"),
    )

    with pytest.raises(HomeAssistantError, match="уже отменён"):
        await hass.services.async_call(
            DOMAIN,
            "confirm_capture",
            {"draft_id": CAPTURE_ID},
            blocking=True,
            return_response=True,
        )


async def test_cancel_capture_conflict_is_clear_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """409 при отмене черновика — понятная ошибка про уже применённый capture."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{DRAFT_CAPTURE_URL}/{CAPTURE_ID}/cancel",
        status=409,
        json=_error_payload("Capture already applied"),
    )

    with pytest.raises(HomeAssistantError, match="уже применён"):
        await hass.services.async_call(
            DOMAIN,
            "cancel_capture",
            {"draft_id": CAPTURE_ID},
            blocking=True,
            return_response=True,
        )


async def test_capture_without_return_response_works(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сервис работает без response_variable."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, json=_capture_payload())
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    await _call_capture(hass, {"text": ITEM_TITLE}, return_response=False)

    assert len(_calls(aioclient_mock, "POST", "/api/v1/capture")) == 1


async def test_capture_schema_rejects_invalid_values(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Схема сервиса отклоняет недопустимые source, mode и hint."""
    await _setup_entry(hass, aioclient_mock)

    with pytest.raises(probatio.Invalid):
        await _call_capture(hass, {"text": ITEM_TITLE, "source": "telegram"})
    with pytest.raises(probatio.Invalid):
        await _call_capture(hass, {"text": ITEM_TITLE, "mode": "later"})
    with pytest.raises(probatio.Invalid):
        await _call_capture(hass, {"text": ITEM_TITLE, "hint": "inbox"})
    with pytest.raises(probatio.Invalid):
        await _call_capture(hass, {"text": ITEM_TITLE, "idempotency_key": "bad key"})
    with pytest.raises(probatio.Invalid):
        await _call_capture(hass, {"text": ITEM_TITLE, "idempotency_key": "x" * 129})


async def test_capture_connection_error_retries_once_same_key(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сетевая ошибка: один повтор тем же ключом и телом, затем ошибка и текст."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, exc=aiohttp.ClientConnectionError())

    with pytest.raises(HomeAssistantError) as exc_info:
        await _call_capture(hass, {"text": ITEM_TITLE})

    assert ITEM_TITLE in str(exc_info.value)
    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 2
    assert calls[0][2]["idempotency_key"] == calls[1][2]["idempotency_key"]
    assert calls[0][3]["Idempotency-Key"] == calls[1][3]["Idempotency-Key"]
    assert calls[0][2] == calls[1][2]


async def test_capture_generates_new_key_per_service_call(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Два отдельных вызова сервиса получают разные ключи идемпотентности."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, json=_capture_payload())
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    await _call_capture(hass, {"text": ITEM_TITLE})
    await _call_capture(hass, {"text": ITEM_TITLE})

    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 2
    keys = [call[2]["idempotency_key"] for call in calls]
    assert keys[0] != keys[1]


async def test_capture_network_retry_does_not_duplicate(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Повтор после обрыва переиспользует ключ; на сервере один capture."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    keys: list[str] = []
    captures: dict[str, dict[str, Any]] = {}

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        key = data["idempotency_key"]
        keys.append(key)
        if len(keys) == 1:
            raise aiohttp.ClientConnectionError()
        captures.setdefault(key, _capture_payload())
        return AiohttpClientMockResponse(method, url, json=captures[key])

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "applied"
    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert len(captures) == 1


async def test_capture_retries_busy_key_with_same_key(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """409 «ключ занят»: короткое ожидание и повтор тем же ключом."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    keys: list[str] = []

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        keys.append(data["idempotency_key"])
        if len(keys) == 1:
            return AiohttpClientMockResponse(
                method,
                url,
                status=409,
                json=_error_payload("Idempotency key is busy"),
                headers={"Retry-After": "0"},
            )
        return AiohttpClientMockResponse(method, url, json=_capture_payload())

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "applied"
    assert len(keys) == 2
    assert keys[0] == keys[1]


async def test_capture_network_error_then_busy_key_then_success(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Сетевой повтор и повтор по 409 не делят общий лимит попыток."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    keys: list[str] = []

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        keys.append(data["idempotency_key"])
        if len(keys) == 1:
            raise aiohttp.ClientConnectionError()
        if len(keys) == 2:
            return AiohttpClientMockResponse(
                method,
                url,
                status=409,
                json=_error_payload("Idempotency key is busy"),
                headers={"Retry-After": "0"},
            )
        return AiohttpClientMockResponse(method, url, json=_capture_payload())

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "applied"
    assert len(keys) == 3
    assert len(set(keys)) == 1


async def test_capture_repeats_busy_key_until_success(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Несколько 409 подряд повторяются, пока укладываются в бюджет."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    keys: list[str] = []

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        keys.append(data["idempotency_key"])
        if len(keys) <= 3:
            return AiohttpClientMockResponse(
                method,
                url,
                status=409,
                json=_error_payload("Idempotency key is busy"),
                headers={"Retry-After": "0"},
            )
        return AiohttpClientMockResponse(method, url, json=_capture_payload())

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "applied"
    assert len(keys) == 4
    assert len(set(keys)) == 1


async def test_capture_busy_key_wait_exceeds_budget(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Если Retry-After больше остатка бюджета, повтор не делается."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        status=409,
        json=_error_payload("Idempotency key is busy"),
        headers={"Retry-After": "60"},
    )

    with pytest.raises(HomeAssistantError, match="уже обрабатывает"):
        await _call_capture(hass, {"text": ITEM_TITLE, "source": "ha_voice"})

    assert len(_calls(aioclient_mock, "POST", "/api/v1/capture")) == 1


async def test_capture_retry_budget_exhausted(
    hass: HomeAssistant, aioclient_mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После ожидания, исчерпавшего бюджет, новый запрос не отправляется."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        status=409,
        json=_error_payload("Idempotency key is busy"),
        headers={"Retry-After": "2"},
    )
    fake_loop = _FakeLoop()
    monkeypatch.setattr(api_module, "asyncio", _FakeAsyncio(fake_loop))
    monkeypatch.setattr(api_module, "CAPTURE_RETRY_BUDGET_VOICE", 2.0)

    with pytest.raises(HomeAssistantError, match="недоступен"):
        await _call_capture(hass, {"text": ITEM_TITLE, "source": "ha_voice"})

    assert len(_calls(aioclient_mock, "POST", "/api/v1/capture")) == 1


async def test_capture_uses_source_specific_timeout(
    hass: HomeAssistant,
    aioclient_mock: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ha_voice использует таймаут 10 секунд, остальные — 22 секунды."""
    entry = await _setup_entry(hass, aioclient_mock)
    client = entry.runtime_data.client
    timeouts: list[float | None] = []

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        timeouts.append(kwargs["timeout"].total)
        return _capture_payload()

    monkeypatch.setattr(client, "_async_request", _fake_request)

    await client.async_capture(
        text="x", source="ha_voice", mode="auto", idempotency_key="key"
    )
    await client.async_capture(
        text="x", source="ha_automation", mode="auto", idempotency_key="key"
    )

    assert timeouts == [10.0, 22.0]


async def test_capture_uses_source_specific_retry_budget(
    hass: HomeAssistant, aioclient_mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ha_voice ограничен 20 секундами бюджета, остальные — 45 секундами."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    clock = {"loop": _FakeLoop()}

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        clock["loop"].now += 10
        return AiohttpClientMockResponse(
            method,
            url,
            status=409,
            json=_error_payload("Idempotency key is busy"),
            headers={"Retry-After": "0"},
        )

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    monkeypatch.setattr(api_module, "asyncio", _FakeAsyncio(clock["loop"]))

    with pytest.raises(HomeAssistantError):
        await _call_capture(hass, {"text": ITEM_TITLE, "source": "ha_voice"})
    voice_calls = len(_calls(aioclient_mock, "POST", "/api/v1/capture"))

    clock["loop"] = _FakeLoop()
    monkeypatch.setattr(api_module, "asyncio", _FakeAsyncio(clock["loop"]))
    with pytest.raises(HomeAssistantError):
        await _call_capture(hass, {"text": ITEM_TITLE, "source": "ha_automation"})
    default_calls = len(_calls(aioclient_mock, "POST", "/api/v1/capture")) - voice_calls

    assert voice_calls == 2
    assert default_calls == 5


async def test_capture_uses_user_idempotency_key_on_retry(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Пользовательский ключ передаётся как есть и при повторе."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    keys: list[str] = []

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        keys.append(data["idempotency_key"])
        if len(keys) == 1:
            raise aiohttp.ClientConnectionError()
        return AiohttpClientMockResponse(method, url, json=_capture_payload())

    aioclient_mock.post(CAPTURE_URL, side_effect=_side_effect)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    await _call_capture(hass, {"text": ITEM_TITLE, "idempotency_key": "user-key-123"})

    assert keys == ["user-key-123", "user-key-123"]


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (403, None, "capture:write"),
        (404, None, "выключен"),
        (413, None, "слишком длинный"),
        (422, None, "отклонил capture"),
        (429, {"Retry-After": "7"}, "через 7 сек."),
        (500, None, "недоступен"),
        (503, None, "разбор недоступен"),
    ],
)
async def test_capture_http_errors_are_russian(
    hass: HomeAssistant,
    aioclient_mock: Any,
    status: int,
    headers: dict[str, str] | None,
    expected: str,
) -> None:
    """HTTP-ошибки capture дают русский текст и исходную фразу."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        status=status,
        json=_error_payload("API error"),
        headers=headers,
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await _call_capture(hass, {"text": ITEM_TITLE})

    message = str(exc_info.value)
    assert expected in message
    assert ITEM_TITLE in message


async def test_capture_500_is_not_retried(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """5xx — HTTP-ответ, поэтому сетевой повтор не выполняется."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        status=500,
        json=_error_payload("Internal error"),
    )

    with pytest.raises(HomeAssistantError, match="недоступен"):
        await _call_capture(hass, {"text": ITEM_TITLE})

    assert len(_calls(aioclient_mock, "POST", "/api/v1/capture")) == 1


async def test_capture_fallback_true_is_returned(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Признак fallback из ответа API попадает в ответ сервиса."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        json=_capture_payload(
            speech="Записал как есть.",
            fallback=True,
        ),
    )
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["fallback"] is True


async def test_capture_rejected_is_success_without_refresh(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """status=rejected — успешный ответ без создания элементов и без refresh."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        json=_capture_payload(
            status="rejected",
            speech="Не смог добавить.",  # noqa: RUF001
            items=[],
        ),
    )

    response = await _call_capture(hass, {"text": ITEM_TITLE})

    assert response["status"] == "rejected"
    assert response["items"] == []
    assert not _calls(aioclient_mock, "GET", "/api/v1/snapshot")


async def test_capture_empty_key_generates_new_one(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Пустой idempotency_key означает «сгенерировать новый»."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CAPTURE_URL, json=_capture_payload())
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    await _call_capture(hass, {"text": ITEM_TITLE, "idempotency_key": ""})

    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 1
    key = calls[0][2]["idempotency_key"]
    assert key
    assert key == calls[0][3]["Idempotency-Key"]


@pytest.mark.parametrize(("service", "data", "url"), ACTION_CALLS)
async def test_capture_actions_401_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: Any,
    service: str,
    data: dict[str, Any],
    url: str,
) -> None:
    """401 в confirm/cancel/undo запускает reauth."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=401, json=_error_payload("Invalid token"))

    with pytest.raises(HomeAssistantError, match="авторизация"):
        await hass.services.async_call(
            DOMAIN,
            service,
            data,
            blocking=True,
            return_response=True,
        )
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


@pytest.mark.parametrize(("service", "data", "url"), ACTION_CALLS)
async def test_capture_actions_403_is_clear(
    hass: HomeAssistant,
    aioclient_mock: Any,
    service: str,
    data: dict[str, Any],
    url: str,
) -> None:
    """403 в confirm/cancel/undo говорит про scope capture:write."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=403, json=_error_payload("Forbidden"))

    with pytest.raises(HomeAssistantError, match="capture:write"):
        await hass.services.async_call(
            DOMAIN,
            service,
            data,
            blocking=True,
            return_response=True,
        )


async def test_capture_401_starts_reauth(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """401 запускает reauth и содержит исходный текст."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        CAPTURE_URL,
        status=401,
        json=_error_payload("Invalid token"),
    )

    with pytest.raises(HomeAssistantError, match=ITEM_TITLE):
        await _call_capture(hass, {"text": ITEM_TITLE})
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_capture_multiple_entries_selects_by_list_id(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """При нескольких записях list_id выбирает нужное подключение."""
    await _setup_entry(hass, aioclient_mock, lists=[list_payload(LIST_ID)])
    second = await _setup_entry(
        hass,
        aioclient_mock,
        entry_id="tasklerq_capture_second",
        base_url=SECOND_BASE_URL,
        household_id=OTHER_HOUSEHOLD_ID,
        lists=[
            list_payload(
                OTHER_LIST_ID,
                kind="checklist",
                name="Дача",
                is_default=False,
            )
        ],
    )
    aioclient_mock.clear_requests()
    aioclient_mock.post(SECOND_CAPTURE_URL, json=_capture_payload())
    aioclient_mock.get(
        f"{SECOND_BASE_URL}/api/v1/snapshot",
        json=snapshot_payload(
            lists=[
                list_payload(
                    OTHER_LIST_ID,
                    kind="checklist",
                    name="Дача",
                    is_default=False,
                )
            ],
            items=[],
        ),
    )

    response = await _call_capture(hass, {"text": ITEM_TITLE, "list_id": OTHER_LIST_ID})

    assert response["status"] == "applied"
    calls = _calls(aioclient_mock, "POST", "/api/v1/capture")
    assert len(calls) == 1
    assert calls[0][1].host == "tasklerq-two.test"
    assert second.runtime_data.coordinator.data.items == {}


async def test_capture_multiple_entries_without_list_id_is_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Без list_id при нескольких записях — ошибка неоднозначности."""
    await _setup_entry(hass, aioclient_mock, lists=[list_payload(LIST_ID)])
    await _setup_entry(
        hass,
        aioclient_mock,
        entry_id="tasklerq_capture_second",
        base_url=SECOND_BASE_URL,
        household_id=OTHER_HOUSEHOLD_ID,
        lists=[list_payload(OTHER_LIST_ID, is_default=False)],
    )
    aioclient_mock.clear_requests()

    with pytest.raises(HomeAssistantError, match="Несколько подключений"):
        await _call_capture(hass, {"text": ITEM_TITLE})

    assert not _calls(aioclient_mock, "POST", "/api/v1/capture")


async def test_capture_unknown_list_id_is_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Неизвестный list_id не даёт выбрать запись при нескольких подключениях."""
    await _setup_entry(hass, aioclient_mock, lists=[list_payload(LIST_ID)])
    await _setup_entry(
        hass,
        aioclient_mock,
        entry_id="tasklerq_capture_second",
        base_url=SECOND_BASE_URL,
        household_id=OTHER_HOUSEHOLD_ID,
        lists=[list_payload(OTHER_LIST_ID, is_default=False)],
    )
    aioclient_mock.clear_requests()

    with pytest.raises(HomeAssistantError, match="Несколько подключений"):
        await _call_capture(
            hass,
            {
                "text": ITEM_TITLE,
                "list_id": "019a1b2c-0000-7000-8000-000000000001",
            },
        )

    assert not _calls(aioclient_mock, "POST", "/api/v1/capture")


async def test_undo_last_capture_returns_count_and_refreshes(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """undo-last возвращает count из removed и обновляет координатор."""
    entry = await _setup_entry(
        hass,
        aioclient_mock,
        items=[item_payload(ITEM_ID, title="позвонить маме")],
    )
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
    aioclient_mock.get(
        SNAPSHOT_URL,
        json=snapshot_payload(items=[], deleted_item_ids=[ITEM_ID]),
    )

    response = await hass.services.async_call(
        DOMAIN,
        "undo_last_capture",
        {},
        blocking=True,
        return_response=True,
    )

    assert response == {
        "count": 2,
        "speech": "Отменил добавление: 2 элемента.",
        "capture_id": CAPTURE_ID,
    }
    assert entry.runtime_data.coordinator.data.items == {}


async def test_undo_last_capture_nothing_to_undo_is_success(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """«Нечего отменять» — успешный ответ: count=0 и speech сервера."""
    await _setup_entry(hass, aioclient_mock)
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
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_payload())

    response = cast(
        "dict[str, Any]",
        await hass.services.async_call(
            DOMAIN,
            "undo_last_capture",
            {},
            blocking=True,
            return_response=True,
        ),
    )

    assert response["count"] == 0
    assert response["capture_id"] is None
    assert response["speech"] == "Нечего отменять: голосовых добавлений не было."


async def test_undo_last_capture_conflict_is_clear_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """409 при undo-last — понятная ошибка, что ничего не удалено."""
    await _setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        UNDO_LAST_URL,
        status=409,
        json=_error_payload("Capture items were modified"),
    )

    with pytest.raises(HomeAssistantError, match="ничего не удалено"):
        await hass.services.async_call(
            DOMAIN,
            "undo_last_capture",
            {},
            blocking=True,
            return_response=True,
        )
