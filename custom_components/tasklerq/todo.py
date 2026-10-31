"""Todo-платформа Tasklerq: одна сущность на неархивированный список."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import date, datetime
from typing import Any, TypeVar

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TasklerqConfigEntry
from .api import (
    TasklerqApiError,
    TasklerqAuthenticationError,
    TasklerqConflictError,
    TasklerqConnectionError,
    TasklerqItem,
    TasklerqList,
    TasklerqNotFoundError,
    TasklerqPermissionError,
    TasklerqServerError,
    TasklerqValidationError,
)
from .const import DOMAIN
from .coordinator import TasklerqCoordinator, TasklerqData

_T = TypeVar("_T")

_SUPPORTED_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
)


def _unique_id(household_id: str, list_id: str) -> str:
    """Стабильный unique_id сущности списка."""
    return f"{DOMAIN}:{household_id}:{list_id}"


def _validation_reason(err: TasklerqApiError) -> str:
    """Причина отказа из сообщения клиента или простых полей details."""
    if err.message:
        return err.message
    return "; ".join(
        f"{key}: {value}"
        for key, value in err.details.items()
        if isinstance(value, (str, int, float, bool))
    )


def _validation_error(err: TasklerqApiError) -> HomeAssistantError:
    """Ошибка отклонённого запроса с причиной в тексте и переводе."""  # noqa: RUF002
    reason = _validation_reason(err)
    reason_text = f": {reason}" if reason else ""
    return HomeAssistantError(
        f"Tasklerq отклонил изменения задачи{reason_text}",
        translation_domain=DOMAIN,
        translation_key="validation_error",
        translation_placeholders={"reason": reason_text},
    )


def _write_error(err: TasklerqApiError) -> HomeAssistantError:
    """Русская ошибка записи по типу ответа клиента."""
    if isinstance(err, TasklerqConflictError):
        return HomeAssistantError(
            "Tasklerq: позиция уже изменена в Tasklerq. Обновите и повторите действие.",
            translation_domain=DOMAIN,
            translation_key="conflict",
        )
    if isinstance(err, TasklerqAuthenticationError):
        return HomeAssistantError(
            "Tasklerq: нужна повторная авторизация. Обновите токен интеграции.",
            translation_domain=DOMAIN,
            translation_key="reauth_required",
        )
    if isinstance(err, TasklerqPermissionError):
        return HomeAssistantError(
            "У токена Tasklerq нет прав на запись: нужен scope ha:write.",  # noqa: RUF001
            translation_domain=DOMAIN,
            translation_key="write_forbidden",
        )
    if isinstance(err, TasklerqNotFoundError):
        return HomeAssistantError(
            "Список или задача не найдены в Tasklerq. "
            "Возможно, они удалены; список обновлён.",
            translation_domain=DOMAIN,
            translation_key="not_found",
        )
    if isinstance(err, TasklerqValidationError):
        return _validation_error(err)
    if err.status is not None and 400 <= err.status < 500:
        return _validation_error(err)
    if isinstance(err, (TasklerqConnectionError, TasklerqServerError)):
        return HomeAssistantError(
            "Tasklerq недоступен. Проверьте подключение и повторите попытку.",
            translation_domain=DOMAIN,
            translation_key="unavailable",
        )
    return HomeAssistantError(
        "Не удалось сохранить изменения в Tasklerq. "  # noqa: RUF001
        "Проверьте подключение и повторите попытку.",
        translation_domain=DOMAIN,
        translation_key="write_failed",
    )


def _parse_due(item: TasklerqItem) -> date | datetime | None:
    """Срок позиции в терминах TodoItem."""
    if due_at := item.get("due_at"):
        return dt_util.parse_datetime(due_at)
    if due_date := item.get("due_date"):
        return date.fromisoformat(due_date)
    return None


def _format_due(value: date | datetime) -> dict[str, str]:
    """Поле PATCH для установки срока."""
    if isinstance(value, datetime):
        return {"due_at": dt_util.as_utc(value).isoformat()}
    return {"due_date": value.isoformat()}


def _item_status(item: TasklerqItem) -> TodoItemStatus:
    """Статус Tasklerq в терминах HA."""
    if item.get("status") == "done":
        return TodoItemStatus.COMPLETED
    return TodoItemStatus.NEEDS_ACTION


def _item_due(item: TasklerqItem) -> str | None:
    """Срок для атрибута tasklerq_items."""
    return item.get("due_at") or item.get("due_date")


class TasklerqTodoListEntity(TodoListEntity):
    """Список Tasklerq как сущность todo."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = _SUPPORTED_FEATURES
    _unrecorded_attributes = frozenset({"tasklerq_items"})

    def __init__(
        self,
        coordinator: TasklerqCoordinator,
        household_id: str,
        list_id: str,
        name: str,
    ) -> None:
        """Создать сущность для списка."""
        self._coordinator = coordinator
        self._household_id = household_id
        self._list_id = list_id
        self._attr_unique_id = _unique_id(household_id, list_id)
        self._attr_name = name
        self._refresh_data()

    @property
    def coordinator(self) -> TasklerqCoordinator:
        """Координатор сущности."""
        return self._coordinator

    async def async_added_to_hass(self) -> None:
        """Подписаться на обновления координатора."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Обновить состояние из координатора."""
        self._refresh_data()
        self.async_write_ha_state()

    def _list_data(self) -> TasklerqList | None:
        """Текущие данные списка, если он ещё виден."""
        return self._coordinator.data.lists.get(self._list_id)

    def _refresh_data(self) -> None:
        """Пересобрать имя, позиции и tasklerq_items."""
        data = self._coordinator.data
        list_data = self._list_data()
        if list_data is None or list_data["is_archived"]:
            self._attr_available = False
            self._attr_todo_items = []
            self._attr_extra_state_attributes = {"tasklerq_items": []}
            return

        self._attr_name = list_data["name"]
        self._attr_available = self._coordinator.last_update_success
        self._attr_todo_items = self._build_todo_items(data)
        self._attr_extra_state_attributes = {
            "tasklerq_items": self._build_tasklerq_items(data)
        }

    def _items(self, data: TasklerqData) -> list[TasklerqItem]:
        """Позиции этого списка в порядке API."""
        items = [
            item for item in data.items.values() if item["list_id"] == self._list_id
        ]
        items.sort(
            key=lambda item: (
                item["status"] == "done",
                item["position"],
                item["created_at"],
                item["id"],
            )
        )
        return items

    def _build_todo_items(self, data: TasklerqData) -> list[TodoItem]:
        """Позиции Tasklerq в нативной модели todo."""
        result: list[TodoItem] = []
        for item in self._items(data):
            completed = (
                dt_util.parse_datetime(item["completed_at"])
                if item["completed_at"]
                else None
            )
            result.append(
                TodoItem(
                    uid=item["id"],
                    summary=item["title"],
                    status=_item_status(item),
                    due=_parse_due(item),
                    description=item["notes"],
                    completed=completed,
                )
            )
        return result

    def _build_tasklerq_items(self, data: TasklerqData) -> list[dict[str, Any]]:
        """Дополнительные поля позиций для атрибута tasklerq_items."""
        result: list[dict[str, Any]] = []
        for item in self._items(data):
            assignee_id = item["assignee_user_id"]
            result.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "status": item["status"],
                    "priority": item["priority"],
                    "due": _item_due(item),
                    "assignee": (
                        data.members.get(assignee_id) if assignee_id else None
                    ),
                    "tags": [tag["name"] for tag in item["tags"]],
                    "recurrence": item["recurrence"],
                    "source": item["source"],
                }
            )
        return result

    def _current_item(self, uid: str) -> TasklerqItem:
        """Текущая позиция из координатора или понятная ошибка."""
        item = self._coordinator.data.items.get(uid)
        if item is None or item["list_id"] != self._list_id:
            raise HomeAssistantError(
                "Задача не найдена в Tasklerq. Обновите список и повторите действие."
            )
        return item

    async def _async_run_write(
        self, awaitable: Awaitable[_T], *, refresh_on_error: bool = True
    ) -> _T:
        """Выполнить запись, превратив ошибку клиента в HomeAssistantError."""
        try:
            return await awaitable
        except TasklerqApiError as err:
            await self._async_handle_write_error(err, refresh=refresh_on_error)
            raise _write_error(err) from err

    async def _async_handle_write_error(
        self, err: TasklerqApiError, *, refresh: bool
    ) -> None:
        """Обновить состояние или запустить reauth перед показом ошибки."""
        if refresh and isinstance(err, (TasklerqConflictError, TasklerqNotFoundError)):
            await self._coordinator.async_refresh()
        elif isinstance(err, TasklerqAuthenticationError):
            entry = self._coordinator.config_entry
            if entry is not None:
                entry.async_start_reauth(self._coordinator.hass)

    async def _async_refresh(self) -> None:
        """Принудительно обновить координатор после успешной записи."""
        await self._coordinator.async_refresh()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Создать позицию в списке."""
        notes = item.description
        due_date: str | None = None
        due_at: str | None = None
        if isinstance(item.due, datetime):
            due_at = dt_util.as_utc(item.due).isoformat()
        elif isinstance(item.due, date):
            due_date = item.due.isoformat()

        await self._async_run_write(
            self._coordinator.client.async_create_item(
                self._list_id,
                title=item.summary or "",
                notes=notes,
                due_date=due_date,
                due_at=due_at,
            )
        )
        await self._async_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Обновить нативные поля и статус позиции."""
        uid = item.uid
        if not uid:
            raise HomeAssistantError(
                "У задачи нет идентификатора Tasklerq."  # noqa: RUF001
            )
        current = self._current_item(uid)

        fields: dict[str, Any] = {}
        if item.summary is not None and item.summary != current["title"]:
            fields["title"] = item.summary
        if item.description != current["notes"]:
            fields["notes"] = item.description

        current_due = _parse_due(current)
        if item.due != current_due:
            if item.due is None:
                if current["due_at"] is not None:
                    fields["due_at"] = None
                if current["due_date"] is not None:
                    fields["due_date"] = None
            else:
                fields.update(_format_due(item.due))

        status_changed = (
            item.status is not None and _item_status(current) != item.status
        )
        version = current["version"]
        wrote = False
        try:
            if fields:
                updated = await self._async_run_write(
                    self._coordinator.client.async_update_item(
                        uid, version=version, fields=fields
                    )
                )
                wrote = True
                version = updated["version"]

            if status_changed:
                refresh_on_error = not wrote
                if item.status == TodoItemStatus.COMPLETED:
                    await self._async_run_write(
                        self._coordinator.client.async_complete_item(
                            uid, version=version
                        ),
                        refresh_on_error=refresh_on_error,
                    )
                else:
                    await self._async_run_write(
                        self._coordinator.client.async_reopen_item(
                            uid, version=version
                        ),
                        refresh_on_error=refresh_on_error,
                    )
                wrote = True
        finally:
            if wrote:
                await self._async_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Мягко удалить позиции по одному uid."""
        wrote = False
        try:
            for uid in uids:
                await self._async_run_write(
                    self._coordinator.client.async_delete_item(uid),
                    refresh_on_error=not wrote,
                )
                wrote = True
        finally:
            if wrote:
                await self._async_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TasklerqConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Создать сущности по неархивированным спискам."""
    coordinator = entry.runtime_data.coordinator
    household_id = entry.runtime_data.household["id"]
    registry = er.async_get(hass)
    entities: dict[str, TasklerqTodoListEntity] = {}
    reconcile_lock = asyncio.Lock()

    desired_items = {
        list_id: list_data
        for list_id, list_data in coordinator.data.lists.items()
        if not list_data["is_archived"]
    }

    # Убрать из реестра сущности списков, которые архивированы или исчезли,
    # пока интеграция не была загружена.
    desired_unique_ids = {
        _unique_id(household_id, list_id) for list_id in desired_items
    }
    prefix = f"{DOMAIN}:{household_id}:"
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.domain != "todo":
            continue
        if not registry_entry.unique_id.startswith(prefix):
            continue
        if registry_entry.unique_id not in desired_unique_ids:
            registry.async_remove(registry_entry.entity_id)

    async def _async_remove_entity(entity: TasklerqTodoListEntity) -> None:
        """Удалить сущность из состояния и реестра."""
        entity_id = entity.entity_id
        await entity.async_remove(force_remove=True)
        if entity_id:
            registry.async_remove(entity_id)

    async def _async_reconcile_entities() -> None:
        """Добавить новые списки и убрать архивированные или исчезнувшие."""
        async with reconcile_lock:
            current = {
                list_id: list_data
                for list_id, list_data in coordinator.data.lists.items()
                if not list_data["is_archived"]
            }

            new_entities: list[TasklerqTodoListEntity] = []
            for list_id, list_data in current.items():
                if list_id in entities:
                    continue
                entity = TasklerqTodoListEntity(
                    coordinator, household_id, list_id, list_data["name"]
                )
                entities[list_id] = entity
                new_entities.append(entity)
            if new_entities:
                async_add_entities(new_entities)

            for list_id in list(entities):
                if list_id in current:
                    continue
                await _async_remove_entity(entities.pop(list_id))

    await _async_reconcile_entities()

    @callback
    def _handle_coordinator_update() -> None:
        """Согласовать набор сущностей после обновления координатора."""
        hass.async_create_task(_async_reconcile_entities())

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))
