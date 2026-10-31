## 6. Home Assistant custom component

Компонент реализуется в отдельном репозитории `tasklerq-ha` (решение
2026-10-31); контракт API — `docs/ha-api.md`.

- [ ] 6.1 Создать скелет `custom_components/tasklerq`: `manifest.json`, `__init__.py`, `const.py`, `services.py`, `services.yaml`, `strings.json`, `translations/ru.json`; проверить загрузку через `pytest-homeassistant-custom-component`.
- [ ] 6.2 Реализовать config flow (URL + token), проверку токена и household через `GET /api/v1/household`, unique_id household, reauth и options; проверить pytest-тестами.
- [ ] 6.3 Реализовать API-клиент и координатор со snapshot `since`, полной синхронизацией и backoff; проверить тестами курсор, восстановление и daily resync.
- [ ] 6.4 Реализовать `TodoListEntity` на каждый список: create/update/delete/complete, due date/datetime, description и стабильный unique_id; проверить pytest-тестами.
- [ ] 6.5 Реализовать атрибут `tasklerq_items` с priority/tags/assignee/recurrence; проверить, что нативные обновления не теряют поля.
- [ ] 6.6 Реализовать `CalendarEntity`: all-day/timed события, повторения на горизонте, read-only; проверить pytest-тестами.
- [ ] 6.7 Реализовать обработку `409`, `401`, `5xx`, `unavailable` и `HomeAssistantError`; проверить тестами конфликт, reauth и восстановление.
- [ ] 6.8 Реализовать diagnostics с redaction токена и без контента задач; проверить pytest-тестом.
- [ ] 6.9 Проверить установку на HA Container (HACS и вручную), появление сущностей и синхронизацию в обе стороны; зафиксировать результат в чек-листе.
- [ ] 6.10 Реализовать сервисы `tasklerq.capture` (`text`, `source`, `mode`, `hint`, `list_id`, `idempotency_key`), `confirm_capture`, `cancel_capture`, `undo_last_capture` с `SupportsResponse.OPTIONAL`, русскими ошибками и ответом `status`/`speech`/`items`/`draft_id`/`capture_id`; проверить pytest-тестами.
- [ ] 6.11 Реализовать генерацию `idempotency_key` на вызов сервиса и повторное использование при сетевом retry; проверить тестом отсутствия дубликатов.
- [ ] 6.12 Подготовить пример пакета `conversation.intents` + `intent_script` для HA Assist и проверить голосовые сценарии «купи …», «добавь задачу …», «отмени последнее добавление».
- [ ] 6.13 Проверить работу компонента при `TELEGRAM_MODE=disabled` и без токена бота; зафиксировать, что `todo`/`calendar` и capture доступны.

