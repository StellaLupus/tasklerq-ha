"""Асинхронный клиент API Tasklerq."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypedDict, cast

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Сервер разбирает capture не дольше 8 секунд для ha_voice и 20 секунд
# для остальных, поэтому HTTP-таймаут увеличен на 2 секунды запаса на БД и сеть.
CAPTURE_TIMEOUT_VOICE = 10.0
CAPTURE_TIMEOUT_DEFAULT = 22.0

# Assist ждёт ответа ограниченное время. Бюджет голосового capture — 20 секунд:
# два серверных окна разбора по 8 секунд плюс запас на HTTP и БД. Это верхняя
# граница, которую должен выдерживать голосовой pipeline Assist; для остальных
# источников бюджет шире и не зависит от ответа Assist.
CAPTURE_RETRY_BUDGET_VOICE = 20.0
CAPTURE_RETRY_BUDGET_DEFAULT = 45.0

# Сетевой обрыв повторяем ровно один раз; повторы по 409 ограничены бюджетом.
CAPTURE_MAX_NETWORK_RETRIES = 1
CAPTURE_DEFAULT_RETRY_AFTER = 2


class TasklerqApiError(Exception):
    """Базовая ошибка клиента Tasklerq."""

    def __init__(
        self,
        message: str | None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        """Сохранить сообщение, конверт ошибки API и HTTP-статус."""
        fallback = (
            f"Tasklerq returned HTTP {status}"
            if status is not None
            else "Tasklerq request failed"
        )
        super().__init__(message or fallback)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.status = status
        self.retry_after = retry_after


class TasklerqConnectionError(TasklerqApiError):
    """Tasklerq недоступен: нет связи или истёк таймаут."""


class TasklerqAuthenticationError(TasklerqApiError):
    """Tasklerq отклонил токен (401)."""


class TasklerqPermissionError(TasklerqApiError):
    """Нет нужного scope (403)."""


class TasklerqNotFoundError(TasklerqApiError):
    """Объект не найден (404)."""


class TasklerqConflictError(TasklerqApiError):
    """Конфликт версии или состояния (409)."""


class TasklerqGoneError(TasklerqApiError):
    """Черновик capture устарел (410)."""


class TasklerqPayloadTooLargeError(TasklerqApiError):
    """Тело запроса больше допустимого (413)."""


class TasklerqRateLimitError(TasklerqApiError):
    """Превышен лимит частоты запросов (429)."""


class TasklerqValidationError(TasklerqApiError):
    """Tasklerq отклонил параметры запроса (422)."""


class TasklerqServerError(TasklerqApiError):
    """Tasklerq вернул ошибку сервера или неожиданный ответ."""


class TasklerqServiceUnavailableError(TasklerqServerError):
    """Разбор capture или зависимость недоступны (503)."""


_ERROR_CLASSES: dict[int, type[TasklerqApiError]] = {
    401: TasklerqAuthenticationError,
    403: TasklerqPermissionError,
    404: TasklerqNotFoundError,
    409: TasklerqConflictError,
    410: TasklerqGoneError,
    413: TasklerqPayloadTooLargeError,
    422: TasklerqValidationError,
    429: TasklerqRateLimitError,
    503: TasklerqServiceUnavailableError,
}


class TasklerqList(TypedDict):
    """Список Tasklerq (объект List)."""

    id: str
    household_id: str
    kind: str
    name: str
    is_default: bool
    is_archived: bool
    position: int
    created_at: str
    updated_at: str
    deleted_at: str | None


class TasklerqTag(TypedDict):
    """Тег позиции."""  # noqa: RUF002

    id: str
    household_id: str
    name: str
    created_at: str


class TasklerqSubtask(TypedDict):
    """Подзадача позиции."""

    id: str
    item_id: str
    title: str
    status: str
    position: int
    created_at: str
    completed_at: str | None


class TasklerqRecurrence(TypedDict):
    """Правило повторения позиции."""

    freq: str
    interval: int
    byweekday: list[int]
    until: str | None
    timezone: str
    anchor_date: str
    month_rule: str | None


class TasklerqSubtaskProgress(TypedDict):
    """Прогресс подзадач позиции."""

    done: int
    total: int


class TasklerqItem(TypedDict):
    """Позиция Tasklerq (объект ItemResponse)."""

    id: str
    household_id: str
    list_id: str
    title: str
    notes: str | None
    status: str
    priority: str
    due_at: str | None
    due_date: str | None
    assignee_user_id: str | None
    created_by_user_id: str | None
    source: str
    capture_id: str | None
    completed_at: str | None
    completed_by_user_id: str | None
    quantity: int | float | None
    unit: str | None
    position: int
    version: int
    recurrence: TasklerqRecurrence | None
    created_at: str
    updated_at: str
    deleted_at: str | None
    tags: list[TasklerqTag]
    subtasks: list[TasklerqSubtask]
    subtask_progress: TasklerqSubtaskProgress


class TasklerqCalendarEvent(TypedDict):
    """Вхождение календаря Tasklerq (объект CalendarEvent)."""

    item_id: str
    list_id: str
    occurrence_key: str
    start: str
    end: str
    all_day: bool
    title: str
    status: str


class TasklerqCaptureItem(TypedDict):
    """Элемент ответа capture: созданный или из черновика."""

    id: str | None
    title: str
    list_id: str | None
    kind: str
    due_date: str | None
    due_at: str | None


@dataclass(frozen=True, slots=True)
class TasklerqCapture:
    """Разобранный ответ POST /api/v1/capture или confirm."""

    capture_id: str
    status: str
    fallback: bool
    speech: str
    items: list[TasklerqCaptureItem]
    draft_id: str | None
    expires_at: str | None
    clarifications: list[str]


@dataclass(frozen=True, slots=True)
class TasklerqCaptureCancel:
    """Разобранный ответ отмены черновика."""

    capture_id: str
    status: str
    speech: str


@dataclass(frozen=True, slots=True)
class TasklerqCaptureUndo:
    """Разобранный ответ отмены последнего capture."""

    capture_id: str | None
    status: str
    removed: int
    speech: str


@dataclass(frozen=True, slots=True)
class TasklerqSnapshot:
    """Разобранный ответ GET /api/v1/snapshot."""

    server_time: str
    next_since: str
    lists: list[TasklerqList]
    items: list[TasklerqItem]
    deleted_item_ids: list[str]
    calendar_events: list[TasklerqCalendarEvent]
    calendar_truncated: bool


def _field(data: dict[str, Any], key: str, context: str) -> Any:
    """Взять обязательное поле ответа или сообщить о неверной форме."""  # noqa: RUF002
    try:
        return data[key]
    except KeyError as err:
        raise TasklerqServerError(
            f"Tasklerq response is missing {context}.{key}"
        ) from err


def _string(data: dict[str, Any], key: str, context: str) -> str:
    """Обязательная непустая строка."""
    value = _field(data, key, context)
    return _string_value(value, f"{context}.{key}")


def _optional_string(data: dict[str, Any], key: str, context: str) -> str | None:
    """Необязательная строка (null или строка, возможно пустая)."""
    value = _field(data, key, context)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}.{key}")
    return value


def _bool(data: dict[str, Any], key: str, context: str) -> bool:
    """Обязательное boolean-поле."""
    value = _field(data, key, context)
    if not isinstance(value, bool):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}.{key}")
    return value


def _int(data: dict[str, Any], key: str, context: str) -> int:
    """Обязательное целое поле."""
    value = _field(data, key, context)
    return _int_value(value, f"{context}.{key}")


def _optional_number(
    data: dict[str, Any], key: str, context: str
) -> int | float | None:
    """Необязательное числовое поле."""
    value = _field(data, key, context)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}.{key}")
    return value


def _list(data: dict[str, Any], key: str, context: str) -> list[Any]:
    """Обязательное поле-массив."""
    value = _field(data, key, context)
    if not isinstance(value, list):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}.{key}")
    return value


def _optional_dict(
    data: dict[str, Any], key: str, context: str
) -> dict[str, Any] | None:
    """Необязательное поле-объект."""
    value = _field(data, key, context)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}.{key}")
    return value


def _string_value(value: Any, context: str) -> str:
    """Непустая строка из значения массива."""
    if not isinstance(value, str) or not value:
        raise TasklerqServerError(f"Tasklerq response has invalid {context}")
    return value


def _int_value(value: Any, context: str) -> int:
    """Целое число из значения массива."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}")
    return value


def _dict_value(value: Any, context: str) -> dict[str, Any]:
    """Объект из значения массива."""
    if not isinstance(value, dict):
        raise TasklerqServerError(f"Tasklerq response has invalid {context}")
    return value


def _parse_list(value: Any) -> TasklerqList:
    """Разобрать объект List."""
    data = _dict_value(value, "list")
    return TasklerqList(
        id=_string(data, "id", "list"),
        household_id=_string(data, "household_id", "list"),
        kind=_string(data, "kind", "list"),
        name=_string(data, "name", "list"),
        is_default=_bool(data, "is_default", "list"),
        is_archived=_bool(data, "is_archived", "list"),
        position=_int(data, "position", "list"),
        created_at=_string(data, "created_at", "list"),
        updated_at=_string(data, "updated_at", "list"),
        deleted_at=_optional_string(data, "deleted_at", "list"),
    )


def _parse_tag(value: Any) -> TasklerqTag:
    """Разобрать тег позиции."""
    data = _dict_value(value, "tag")
    return TasklerqTag(
        id=_string(data, "id", "tag"),
        household_id=_string(data, "household_id", "tag"),
        name=_string(data, "name", "tag"),
        created_at=_string(data, "created_at", "tag"),
    )


def _parse_subtask(value: Any) -> TasklerqSubtask:
    """Разобрать подзадачу позиции."""
    data = _dict_value(value, "subtask")
    return TasklerqSubtask(
        id=_string(data, "id", "subtask"),
        item_id=_string(data, "item_id", "subtask"),
        title=_string(data, "title", "subtask"),
        status=_string(data, "status", "subtask"),
        position=_int(data, "position", "subtask"),
        created_at=_string(data, "created_at", "subtask"),
        completed_at=_optional_string(data, "completed_at", "subtask"),
    )


def _parse_recurrence(value: dict[str, Any]) -> TasklerqRecurrence:
    """Разобрать правило повторения."""
    byweekday = _list(value, "byweekday", "recurrence")
    return TasklerqRecurrence(
        freq=_string(value, "freq", "recurrence"),
        interval=_int(value, "interval", "recurrence"),
        byweekday=[_int_value(day, "recurrence.byweekday") for day in byweekday],
        until=_optional_string(value, "until", "recurrence"),
        timezone=_string(value, "timezone", "recurrence"),
        anchor_date=_string(value, "anchor_date", "recurrence"),
        month_rule=_optional_string(value, "month_rule", "recurrence"),
    )


def _parse_subtask_progress(value: dict[str, Any]) -> TasklerqSubtaskProgress:
    """Разобрать прогресс подзадач."""
    return TasklerqSubtaskProgress(
        done=_int(value, "done", "subtask_progress"),
        total=_int(value, "total", "subtask_progress"),
    )


def _parse_item(value: Any) -> TasklerqItem:
    """Разобрать объект ItemResponse."""
    data = _dict_value(value, "item")
    recurrence = _optional_dict(data, "recurrence", "item")
    subtask_progress = _optional_dict(data, "subtask_progress", "item")
    if subtask_progress is None:
        raise TasklerqServerError("Tasklerq response is missing item.subtask_progress")
    return TasklerqItem(
        id=_string(data, "id", "item"),
        household_id=_string(data, "household_id", "item"),
        list_id=_string(data, "list_id", "item"),
        title=_string(data, "title", "item"),
        notes=_optional_string(data, "notes", "item"),
        status=_string(data, "status", "item"),
        priority=_string(data, "priority", "item"),
        due_at=_optional_string(data, "due_at", "item"),
        due_date=_optional_string(data, "due_date", "item"),
        assignee_user_id=_optional_string(data, "assignee_user_id", "item"),
        created_by_user_id=_optional_string(data, "created_by_user_id", "item"),
        source=_string(data, "source", "item"),
        capture_id=_optional_string(data, "capture_id", "item"),
        completed_at=_optional_string(data, "completed_at", "item"),
        completed_by_user_id=_optional_string(data, "completed_by_user_id", "item"),
        quantity=_optional_number(data, "quantity", "item"),
        unit=_optional_string(data, "unit", "item"),
        position=_int(data, "position", "item"),
        version=_int(data, "version", "item"),
        recurrence=None if recurrence is None else _parse_recurrence(recurrence),
        created_at=_string(data, "created_at", "item"),
        updated_at=_string(data, "updated_at", "item"),
        deleted_at=_optional_string(data, "deleted_at", "item"),
        tags=[_parse_tag(tag) for tag in _list(data, "tags", "item")],
        subtasks=[
            _parse_subtask(subtask) for subtask in _list(data, "subtasks", "item")
        ],
        subtask_progress=_parse_subtask_progress(subtask_progress),
    )


def calendar_event_bounds(
    event: TasklerqCalendarEvent,
) -> tuple[date | datetime, date | datetime]:
    """Разобрать и проверить границы события в одном месте."""
    if event["all_day"]:
        try:
            start_date = date.fromisoformat(event["start"])
            end_date = date.fromisoformat(event["end"])
        except ValueError as err:
            raise TasklerqServerError(
                "Tasklerq response has invalid calendar_event dates"
            ) from err
        if end_date < start_date:
            raise TasklerqServerError(
                "Tasklerq response has invalid calendar_event dates"
            )
        return start_date, end_date
    try:
        start = dt_util.parse_datetime(event["start"])
        end = dt_util.parse_datetime(event["end"])
    except ValueError as err:
        raise TasklerqServerError(
            "Tasklerq response has invalid calendar_event dates"
        ) from err
    if (
        start is None
        or end is None
        or start.tzinfo is None
        or end.tzinfo is None
        or end < start
    ):
        raise TasklerqServerError("Tasklerq response has invalid calendar_event dates")
    return start, end


def _parse_calendar_event(value: Any) -> TasklerqCalendarEvent:
    """Разобрать объект CalendarEvent и проверить даты начала и конца."""
    data = _dict_value(value, "calendar_event")
    event = TasklerqCalendarEvent(
        item_id=_string(data, "item_id", "calendar_event"),
        list_id=_string(data, "list_id", "calendar_event"),
        occurrence_key=_string(data, "occurrence_key", "calendar_event"),
        start=_string(data, "start", "calendar_event"),
        end=_string(data, "end", "calendar_event"),
        all_day=_bool(data, "all_day", "calendar_event"),
        title=_string(data, "title", "calendar_event"),
        status=_string(data, "status", "calendar_event"),
    )
    calendar_event_bounds(event)
    return event


def _parse_capture_item(value: Any) -> TasklerqCaptureItem:
    """Разобрать элемент ответа capture."""
    data = _dict_value(value, "capture_item")
    return TasklerqCaptureItem(
        id=_optional_string(data, "id", "capture_item"),
        title=_string(data, "title", "capture_item"),
        list_id=_optional_string(data, "list_id", "capture_item"),
        kind=_string(data, "kind", "capture_item"),
        due_date=_optional_string(data, "due_date", "capture_item"),
        due_at=_optional_string(data, "due_at", "capture_item"),
    )


def _parse_capture(payload: Any) -> TasklerqCapture:
    """Разобрать ответ capture или confirm."""
    data = _dict_value(payload, "capture")
    clarifications = (
        _list(data, "clarifications", "capture") if "clarifications" in data else []
    )
    return TasklerqCapture(
        capture_id=_string(data, "capture_id", "capture"),
        status=_string(data, "status", "capture"),
        fallback=_bool(data, "fallback", "capture"),
        speech=_optional_string(data, "speech", "capture") or "",
        items=[_parse_capture_item(value) for value in _list(data, "items", "capture")],
        draft_id=_optional_string(data, "draft_id", "capture"),
        expires_at=_optional_string(data, "expires_at", "capture"),
        clarifications=[
            _string_value(value, "capture.clarifications") for value in clarifications
        ],
    )


def _parse_capture_cancel(payload: Any) -> TasklerqCaptureCancel:
    """Разобрать ответ отмены черновика."""
    data = _dict_value(payload, "capture_cancel")
    return TasklerqCaptureCancel(
        capture_id=_string(data, "capture_id", "capture_cancel"),
        status=_string(data, "status", "capture_cancel"),
        speech=_optional_string(data, "speech", "capture_cancel") or "",
    )


def _parse_capture_undo(payload: Any) -> TasklerqCaptureUndo:
    """Разобрать ответ отмены последнего capture."""
    data = _dict_value(payload, "capture_undo")
    return TasklerqCaptureUndo(
        capture_id=_optional_string(data, "capture_id", "capture_undo"),
        status=_string(data, "status", "capture_undo"),
        removed=_int(data, "removed", "capture_undo"),
        speech=_optional_string(data, "speech", "capture_undo") or "",
    )


def _parse_snapshot(payload: Any) -> TasklerqSnapshot:
    """Разобрать ответ snapshot, проверив форму всех полей."""
    if not isinstance(payload, dict):
        raise TasklerqServerError("Tasklerq snapshot response is not an object")
    return TasklerqSnapshot(
        server_time=_string(payload, "server_time", "snapshot"),
        next_since=_string(payload, "next_since", "snapshot"),
        lists=[_parse_list(value) for value in _list(payload, "lists", "snapshot")],
        items=[_parse_item(value) for value in _list(payload, "items", "snapshot")],
        deleted_item_ids=[
            _string_value(value, "snapshot.deleted_item_ids")
            for value in _list(payload, "deleted_item_ids", "snapshot")
        ],
        calendar_events=[
            _parse_calendar_event(value)
            for value in _list(payload, "calendar_events", "snapshot")
        ],
        calendar_truncated=_bool(payload, "calendar_truncated", "snapshot"),
    )


class TasklerqApiClient:
    """Минимальный клиент Tasklerq для config flow и координатора."""

    def __init__(self, hass: HomeAssistant, url: str, token: str) -> None:
        """Создать клиент с базовым URL и API-токеном."""  # noqa: RUF002
        self._hass = hass
        self._url = url.rstrip("/")
        self._token = token

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> Any:
        """Выполнить запрос и вернуть JSON, преобразовав ошибки клиента."""
        session = async_get_clientsession(self._hass)
        request_headers = {"Authorization": f"Bearer {self._token}"}
        if headers:
            request_headers.update(headers)
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        url = f"{self._url}{path}"

        try:
            async with session.request(
                method,
                url,
                headers=request_headers,
                params=params,
                json=json,
                timeout=timeout or REQUEST_TIMEOUT,
            ) as response:
                if response.status in (200, 201):
                    try:
                        return await response.json(content_type=None)
                    except ValueError as err:
                        raise TasklerqServerError(
                            "Tasklerq returned an invalid JSON response"
                        ) from err
                if response.status == 204:
                    return None
                raise await self._async_api_error(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TasklerqConnectionError("Could not connect to Tasklerq") from err

    async def _async_api_error(
        self, response: aiohttp.ClientResponse
    ) -> TasklerqApiError:
        """Собрать ошибку клиента из конверта API или «сырого» ответа."""
        code: str | None = None
        message: str | None = None
        details: dict[str, Any] | None = None
        raw_text = ""

        try:
            payload = await response.json(content_type=None)
        except ValueError:
            payload = None
            raw_text = (await response.text(errors="replace")).strip()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                raw_code = error.get("code")
                raw_message = error.get("message")
                raw_details = error.get("details")
                if isinstance(raw_code, str):
                    code = raw_code
                if isinstance(raw_message, str):
                    message = raw_message
                if isinstance(raw_details, dict):
                    details = cast("dict[str, Any]", raw_details)

        status = response.status
        if message is None and raw_text and 400 <= status < 500:
            message = raw_text[:500]
        retry_after = None
        raw_retry_after = response.headers.get("Retry-After")
        if raw_retry_after is not None and raw_retry_after.strip().isdigit():
            retry_after = int(raw_retry_after.strip())
        error_class = _ERROR_CLASSES.get(status, TasklerqServerError)
        return error_class(
            message,
            code=code,
            details=details,
            status=status,
            retry_after=retry_after,
        )

    async def _async_get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> Any:
        """Выполнить GET и вернуть JSON, преобразовав ошибки клиента."""
        return await self._async_request("GET", path, params=params)

    async def async_get_household(self) -> dict[str, Any]:
        """Получить household текущего токена."""
        household, _ = await self.async_get_household_and_members()
        return household

    async def async_get_household_and_members(
        self,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Получить household и имена участников одним запросом."""
        payload = await self._async_get("/api/v1/household")
        if not isinstance(payload, dict):
            raise TasklerqServerError("Tasklerq response is not an object")

        household = payload.get("household")
        if not isinstance(household, dict):
            raise TasklerqServerError("Tasklerq response does not contain a household")

        household_id = household.get("id")
        name = household.get("name")
        if not isinstance(household_id, str) or not isinstance(name, str):
            raise TasklerqServerError("Tasklerq response has an invalid household")

        members: dict[str, str] = {}
        for value in _list(payload, "members", "household"):
            data = _dict_value(value, "member")
            members[_string(data, "id", "member")] = _string(
                data, "display_name", "member"
            )

        return cast("dict[str, Any]", household), members

    async def async_get_snapshot(
        self,
        *,
        since: str | None = None,
        calendar_end: str | None = None,
    ) -> TasklerqSnapshot:
        """Получить снапшот; пустой since не передаётся и означает полный."""
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        if calendar_end:
            params["calendar_end"] = calendar_end

        payload = await self._async_get("/api/v1/snapshot", params=params)
        return _parse_snapshot(payload)

    async def async_get_household_members(self) -> dict[str, str]:
        """Получить отображаемые имена активных участников household."""
        _, members = await self.async_get_household_and_members()
        return members

    async def async_get_calendar(
        self, *, start: str, end: str
    ) -> list[TasklerqCalendarEvent]:
        """Получить вхождения календаря за [start, end) по датам YYYY-MM-DD."""
        payload = await self._async_get(
            "/api/v1/calendar", params={"start": start, "end": end}
        )
        if not isinstance(payload, dict):
            raise TasklerqServerError("Tasklerq calendar response is not an object")
        return [
            _parse_calendar_event(value)
            for value in _list(payload, "events", "calendar")
        ]

    async def async_create_item(
        self,
        list_id: str,
        *,
        title: str,
        notes: str | None = None,
        due_date: str | None = None,
        due_at: str | None = None,
        source: str = "ha_todo",
    ) -> TasklerqItem:
        """Создать позицию в списке."""
        body: dict[str, Any] = {"title": title, "source": source}
        if notes is not None:
            body["notes"] = notes
        if due_date is not None:
            body["due_date"] = due_date
        elif due_at is not None:
            body["due_at"] = due_at

        response = await self._async_request(
            "POST", f"/api/v1/lists/{list_id}/items", json=body
        )
        return _parse_item(response)

    async def async_update_item(
        self,
        item_id: str,
        *,
        version: int,
        fields: dict[str, Any],
    ) -> TasklerqItem:
        """Изменить позицию с оптимистичной блокировкой по version."""  # noqa: RUF002
        response = await self._async_request(
            "PATCH",
            f"/api/v1/items/{item_id}",
            json={"version": version, **fields},
        )
        return _parse_item(response)

    async def async_delete_item(self, item_id: str) -> None:
        """Мягко удалить позицию."""
        await self._async_request("DELETE", f"/api/v1/items/{item_id}")

    async def async_complete_item(
        self, item_id: str, *, version: int | None = None
    ) -> TasklerqItem:
        """Завершить позицию, при необходимости проверив version."""
        response = await self._async_request(
            "POST",
            f"/api/v1/items/{item_id}/complete",
            json=None if version is None else {"version": version},
        )
        return _parse_item(response)

    async def async_reopen_item(
        self, item_id: str, *, version: int | None = None
    ) -> TasklerqItem:
        """Открыть завершённую позицию, при необходимости проверив version."""
        response = await self._async_request(
            "POST",
            f"/api/v1/items/{item_id}/reopen",
            json=None if version is None else {"version": version},
        )
        return _parse_item(response)

    async def async_capture(
        self,
        *,
        text: str,
        source: str,
        mode: str,
        hint: str | None = None,
        list_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TasklerqCapture:
        """Отправить текст в capture; при сетевой ошибке повторить с тем же ключом."""  # noqa: RUF002
        body: dict[str, Any] = {"text": text, "source": source, "mode": mode}
        if hint is not None:
            body["hint"] = hint
        if list_id is not None:
            body["list_id"] = list_id
        headers: dict[str, str] = {}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
            headers["Idempotency-Key"] = idempotency_key

        if source == "ha_voice":
            timeout_seconds = CAPTURE_TIMEOUT_VOICE
            budget = CAPTURE_RETRY_BUDGET_VOICE
        else:
            timeout_seconds = CAPTURE_TIMEOUT_DEFAULT
            budget = CAPTURE_RETRY_BUDGET_DEFAULT

        loop = asyncio.get_running_loop()
        started = loop.time()
        network_retries = 0
        while True:
            remaining = budget - (loop.time() - started)
            if remaining <= 0:
                raise TasklerqConnectionError("Tasklerq capture retry budget exhausted")
            try:
                response = await self._async_request(
                    "POST",
                    "/api/v1/capture",
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(
                        total=min(timeout_seconds, remaining)
                    ),
                )
            except TasklerqConflictError as err:
                # 409 на capture означает занятый Idempotency-Key: повторяем
                # тем же ключом и телом, пока ожидание укладывается в бюджет.
                delay = (
                    err.retry_after
                    if err.retry_after is not None
                    else CAPTURE_DEFAULT_RETRY_AFTER
                )
                if delay > budget - (loop.time() - started):
                    raise
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            except TasklerqConnectionError:
                if network_retries >= CAPTURE_MAX_NETWORK_RETRIES:
                    raise
                network_retries += 1
                continue
            return _parse_capture(response)

    async def async_confirm_capture(self, capture_id: str) -> TasklerqCapture:
        """Подтвердить черновик capture."""
        response = await self._async_request(
            "POST", f"/api/v1/captures/{capture_id}/confirm"
        )
        return _parse_capture(response)

    async def async_cancel_capture(self, capture_id: str) -> TasklerqCaptureCancel:
        """Отменить черновик capture."""
        response = await self._async_request(
            "POST", f"/api/v1/captures/{capture_id}/cancel"
        )
        return _parse_capture_cancel(response)

    async def async_undo_last_capture(self) -> TasklerqCaptureUndo:
        """Отменить последний голосовой capture."""
        response = await self._async_request("POST", "/api/v1/captures/undo-last")
        return _parse_capture_undo(response)
