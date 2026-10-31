# Tasklerq for Home Assistant — инструкции для агентов

Custom component Home Assistant (домен `tasklerq`) для сервиса
[Tasklerq](https://github.com/StellaLupus/tasklerq): списки как `todo`-сущности,
задачи со сроком в `calendar`, сервисы capture для Assist и автоматизаций.
Устанавливается через HACS (custom repository) или копированием
`custom_components/tasklerq`.

## Источники

- Контракт API сервера — `docs/upstream/ha-api.md`. Других эндпоинтов и полей нет;
  не придумывай их.
- Требования — `docs/upstream/home-assistant-bridge.spec.md`, решения —
  `docs/upstream/design-ha.md`, пользовательская документация —
  `docs/upstream/home-assistant.md`, capture — `docs/upstream/llm-capture.md`.
- `docs/upstream/` — копии из основного репозитория, их не правь. Если контракт
  неполон или противоречит требованиям, сообщи об этом, а не обходи молча.

## Соглашения

- Целевая версия Home Assistant — 2026.10.x; тесты на
  `pytest-homeassistant-custom-component` той же версии HA.
- Только асинхронный код и `aiohttp`-сессия Home Assistant
  (`async_get_clientsession`); никаких блокирующих вызовов в event loop.
- Пользовательские тексты (ошибки, `strings.json`, `translations/ru.json`) —
  на русском; идентификаторы и логи — на английском.
- Токен и содержимое задач не попадают в логи и diagnostics.
- Поведение меняется только вместе с тестом, который его проверяет.
