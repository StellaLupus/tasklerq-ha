<!-- Выдержка из tasklerq openspec/changes/0001-foundation/design.md: D4a, D5, D10 -->

### D4a. Capture без Telegram: голос, автоматизации, API

**Зачем.** Бот — основной UI, но не единственная точка входа. Задачу нужно
добавить голосом через HA Assist, автоматизацией или прямым HTTP-запросом, даже
если Telegram недоступен.

**Источники.** `capture_drafts.source` — `telegram`, `ha_voice`,
`ha_automation`, `api`, `shortcut`. `items.source` — `telegram`, `ha_todo`,
`ha_voice`, `ha_automation`, `api`, `import`. Источник неизменяем и доступен в
API/UI; по нему можно фильтровать. `ha_voice` и `ha_automation` переносятся в
элемент как есть; `shortcut` и `api` дают `items.source=api`; создание через
`todo.add_item` даёт `ha_todo`.

**API.**

```http
POST /api/v1/capture
Authorization: Bearer tlq_...
Idempotency-Key: <uuid>
Content-Type: application/json
```

```json
{
  "text": "завтра купить молоко и хлеб",
  "source": "ha_voice",
  "mode": "auto",
  "hint": "shopping",
  "list_id": null,
  "idempotency_key": "019..."
}
```

Ответ:

```json
{
  "capture_id": "019...",
  "status": "applied",
  "fallback": false,
  "speech": "Добавил 2 покупки: молоко, хлеб.",
  "items": [
    {"id": "019...", "title": "молоко", "list_id": "019...", "kind": "shopping",
     "due_date": null, "due_at": null}
  ],
  "draft_id": null,
  "expires_at": null,
  "clarifications": []
}
```

`speech` — обычный текст без Markdown, не длиннее 200 символов; его озвучивает
HA Assist. `status` — `applied`, `draft` или `rejected`. Для `draft`
возвращается `draft_id` и `expires_at`; `draft_id` совпадает с `capture_id`.
Подтверждение — `POST /api/v1/captures/{id}/confirm`, отмена —
`POST /api/v1/captures/{id}/cancel`, undo —
`POST /api/v1/captures/{id}/undo`.

**Режимы.** `auto` — валидированный разбор применяется сразу одной транзакцией
(`items` + `capture_drafts` + `item_events`), без черновика и без сообщения в
Telegram. `draft` — сохраняется черновик с TTL 15 минут; вызывающая сторона
подтверждает его сама. Telegram всегда использует `draft` + подтверждение.

**Идемпотентность.** `Idempotency-Key` (заголовок или поле) уникален в рамках
`(household_id, source)`. Повтор возвращает результат первого применения и не
создаёт дубликаты. HA-компонент генерирует UUID на каждый вызов сервиса и
переиспользует его при сетевом retry. Ключ резервируется до вызова LLM: повтор,
пока первый запрос ещё обрабатывается, получает `409` с `Retry-After` и не
запускает второй разбор; тот же ключ с другим телом запроса — `422`.

**Валидация.** Та же, что в D4, с послаблениями для `auto`: неизвестный список
заменяется списком по умолчанию, неизвестный исполнитель снимается (элемент
остаётся без исполнителя), пустые необязательные поля отбрасываются. Жёсткие
ошибки (пустое/длинное название, дата в прошлом, неверное повторение) отклоняют
весь capture; частичного создания нет.

**Fallback.** Если LLM недоступна, вернула ошибку, исчерпана квота или дважды
не прошла валидация, `auto` создаёт одну позицию из исходного текста в списке по
умолчанию (`hint` или `tasks`), без срока, с `fallback=true`. Для
`source=ha_voice` таймаут LLM — 8 секунд, чтобы голосовой pipeline успел
ответить; остальные источники — 20 секунд. Если fallback отключён, возвращается
`503`, и HA озвучивает понятную ошибку; исходный текст не теряется в пределах
retention.

**Голосовой ответ.** Короткая сводка: «Добавил 2 покупки: молоко, хлеб»,
«Записал как есть: купить молоко», «Не смог добавить: дата уже прошла». При
большом количестве — «Добавил 5 элементов». Ошибки не содержат внутренних кодов
и текста LLM.

**Без бота.** Внешний capture не создаёт запись в `outbox` и не требует
работающего Telegram. `TELEGRAM_MODE=disabled` отключает webhook, воркер и
исходящие сообщения; `TELEGRAM_BOT_TOKEN` в этом режиме не обязателен. Если
элемент назначен участнику, уведомление о назначении MAY отправляться по
настройке; подтверждение создания — нет.

**Отмена.** `items.capture_id` связывает созданные элементы с capture. Undo в
течение 10 минут мягко удаляет все элементы capture, если они не изменены
после создания; иначе — `409`. HA-сервис `tasklerq.undo_last_capture` отменяет
последний голосовой capture household за 10 минут.

**Путь Home Assistant.** Компонент регистрирует сервис `tasklerq.capture`
(`text`, `source` (`ha_voice`/`ha_automation`, по умолчанию `ha_automation`),
`mode`, `hint`, `list_id`, `idempotency_key`) и возвращает ответ через
`SupportsResponse.OPTIONAL` (`ONLY` отклонил бы вызов из автоматизации без
`response_variable`). Для HA Assist поставляется пример
`conversation.intents` + `intent_script`, который вызывает сервис и озвучивает
`response.speech`. Простой путь `todo.add_item` остаётся для команд без разбора
дат/тегов; он создаёт элемент с `source=ha_todo`.

**Почему не LLM-инструменты в HA.** AI Task не имеет Assist-инструментов, а
разбор должен быть единым для Telegram и голоса, с общей валидацией, квотой,
идемпотентностью и аудитом. Поэтому HA передаёт текст в Tasklerq, а не вызывает
`todo` из LLM.

**Альтернативы.** Отдельный голосовой сервис (лишний компонент); парсинг в HA
через conversation agent (другая модель, нет общей валидации); запись напрямую
в БД из HA (нарушает границу API).

### D5. Home Assistant: тонкий компонент и polling

**Почему custom component.** MQTT discovery не поддерживает `todo` и
`calendar`; шаблонные сенсоры не дают нативных сущностей; CalDAV даёт
календарь, но не задачи с приоритетами/тегами и требует отдельного сервера.
Компонент — единственный способ получить нативные `todo`/`calendar` в HA
Container.

**Репозиторий и структура компонента.** Компонент поставляется из отдельного
репозитория `tasklerq-ha` с собственными релизами и тегами: HACS берёт версии
из тегов/релизов, а Python-тулчейн и CI не смешиваются с тегами Rust-сервера.

```text
tasklerq-ha/custom_components/tasklerq/
  __init__.py          # setup, coordinator, service registration
  manifest.json        # domain, version, requirements, iot_class
  config_flow.py       # URL + token, reauth, options
  api.py               # aiohttp client, Bearer, errors
  coordinator.py       # DataUpdateCoordinator, snapshot since
  services.py          # capture / confirm / cancel / undo_last_capture
  services.yaml        # описания полей сервисов для UI
  todo.py              # TodoListEntity per list
  calendar.py          # CalendarEntity aggregate
  diagnostics.py       # redacted diagnostics
  const.py
  strings.json
  translations/ru.json
```

**Сервисы capture.** `tasklerq.capture` вызывает `POST /api/v1/capture` с
токеном интеграции и возвращает `status`, `speech`, `items`, `draft_id`,
`capture_id`. `tasklerq.confirm_capture` и `tasklerq.cancel_capture` работают с
`draft`, `tasklerq.undo_last_capture` отменяет последний голосовой capture.
Сервисы объявляются с `SupportsResponse.OPTIONAL`; ошибки — `HomeAssistantError` с
русским текстом. Для HA Assist в документации и примере пакета поставляются
`conversation.intents` и `intent_script`, которые вызывают сервис и озвучивают
`response.speech`; Python-код intent не нужен.

**Snapshot API.** `GET /api/v1/snapshot?since=<cursor>&calendar_end=<date>`
возвращает `lists`, `items`, `deleted_item_ids`, `calendar_events`,
`server_time`, `next_since`.
Компонент хранит курсор; при первом запуске/смене токена/раз в сутки —
полная синхронизация. Это дешевле, чем отдельный запрос на каждый список.
Курсор должен переживать конкурентные коммиты: `next_since` — это `server_time`
минус 5 секунд перекрытия, а компонент применяет изменения идемпотентно по `id`
и `version`.

**Todo mapping:**

| HA | Tasklerq |
| --- | --- |
| `summary` | `title` |
| `description` | `notes` |
| `due` (date/datetime) | `due_date`/`due_at` |
| `status` | `open`/`done` |
| create | POST item |
| update | PATCH item + version |
| delete | DELETE (soft) |
| complete | POST complete |

Одна сущность на неархивированный список; `unique_id` =
`tasklerq:<household_uuid>:<list_uuid>`. Дополнительные поля — в атрибуте
`tasklerq_items` (priority, tags, assignee, recurrence), потому что нативная
модель `todo` их не содержит.

**Calendar mapping.** Одна read-only сущность `calendar.tasklerq`; события —
задачи со сроком. Вседневные — all-day, со временем — timed. Повторяющиеся
задачи разворачиваются сервером на горизонте (по умолчанию 30 дней, максимум
90). Создание/редактирование событий не поддерживается.

**Запись и конфликты.** Компонент передаёт `version` из последнего снапшота.
`409` → `UpdateFailed`/`HomeAssistantError`, немедленный refresh, пользователь
видит конфликт. Молчаливое перезаписывание запрещено.

**Polling.** По умолчанию 60 с (30–300). При ошибке — backoff 30 с…5 мин,
сущности `unavailable`. При `401` — reauth. Запись инициирует refresh, чтобы
UI обновился быстрее.

**Установка.** HACS custom repository
`https://github.com/StellaLupus/tasklerq-ha` (категория Integration) или
копирование `custom_components/tasklerq` из репозитория `tasklerq-ha`/релиза в
`/volume2/docker/homeassistant/custom_components/tasklerq` (внутри HA —
`/config/custom_components/tasklerq`); перезапуск HA; добавить интеграцию.
В текущей среде HA работает в `network_mode: host`, поэтому URL Tasklerq —
`http://127.0.0.1:18080`; `8080` на NAS занят Zigbee2MQTT, внутренний порт
контейнера остаётся `8080`.

**Альтернативы.** MQTT (нет `todo`/`calendar`); AppDaemon (нет нативных
сущностей); CalDAV (только календарь, polling 15 минут); прямые REST-сенсоры
(нет `todo`-сервисов).


### D10. Тестирование и качество

- **Unit:** валидация, повторения (включая 31-е, последний день месяца и 29 февраля), timezone/DST,
  рендеринг RichMessage, callback-токены, парсинг LLM.
- **Integration:** testcontainers PostgreSQL; репозитории, транзакции, очередь
  `SKIP LOCKED`, миграции.
- **Contract:** mock Telegram (`wiremock`) и mock HA; проверка схемы
  `ai_task.generate_data`; формат запросов RichMessage; capture API
  (auto/draft/idempotency/undo) и HA-сервисы.
- **Python (в `tasklerq-ha`):** `pytest-homeassistant-custom-component`;
  config flow, todo, calendar, diagnostics, конфликты.
- **E2E:** docker compose + fake Telegram + fake HA; сценарии «создать задачу»,
  «подтвердить черновик», «голосовой capture без Telegram», «завершить из HA»,
  «напоминание».
- **LLM golden set:** ≥30 русских фраз, проверка извлечения элементов ≥90%;
  версия промпта обязательна.
- **CI:** `cargo fmt --check`, `clippy -D warnings`, `cargo test`,
  `sqlx prepare --check`, `cargo audit`; Python `ruff`, `mypy`, `pytest` — в
  `tasklerq-ha`; `openspec validate --strict`.
- **Нагрузка:** 10 пользователей, 10 000 задач, 50 webhook/с; проверить
  latency webhook <100 мс (без LLM), очередь не растёт бесконечно.
- **Ручной чек-лист:** Telegram iOS/Android/Desktop, HA Companion, русские
  даты, длинные списки, конфликт версий, отключение LLM.

