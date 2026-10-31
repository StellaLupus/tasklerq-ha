# Tasklerq в Assist

Пример подключения голосового capture Tasklerq к Home Assistant Assist.

## Файлы

- `packages/tasklerq_assist.yaml` — `intent_script` для трёх сценариев:
  задача (`hint: tasks`), покупки (`hint: shopping`) и отмена последнего
  голосового добавления.
- `custom_sentences/ru/tasklerq.yaml` — фразы для встроенного распознавателя
  Assist. Список `tasklerq_text` объявлен как `wildcard: true`, поэтому хвост
  фразы целиком попадает в параметр `tasklerq_text`. Префикс в имени списка
  защищает от конфликта с чужими `lists: text:` в других файлах
  `custom_sentences`.

Фразы вынесены в `custom_sentences`, а не в `conversation: intents:`, потому
что в конфигурации `conversation` нельзя объявить слот-лист. Ссылка
`{tasklerq_text}` в `conversation: intents:` остаётся без списка, и Home
Assistant при разборе завершается ошибкой `MissingListError` (проверено на
hassil 3.12.1 из HA 2026.10.4). `custom_sentences` поддерживает `lists` и
wildcard.

## Подключение

1. Скопируйте `custom_sentences/ru/tasklerq.yaml` в
   `/config/custom_sentences/ru/tasklerq.yaml`.
2. Скопируйте `packages/tasklerq_assist.yaml` в `/config/packages/` и
   подключите пакеты в `configuration.yaml`, если они ещё не подключены:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Перезапустите Home Assistant и проверьте фразы в
   **Developer Tools → Assist** (язык — русский).

## Фразы для проверки

- «купи молоко и хлеб» → две позиции в списке покупок, Assist отвечает
  «Добавил две покупки» (фразу формирует Tasklerq).
- «добавь задачу позвонить маме завтра» → задача со сроком «завтра».
- «отмени последнее добавление» → отмена последнего голосового добавления за
  последние 10 минут; если отменять нечего, Assist так и скажет.

## Поведение

- Все вызовы capture идут с `source: ha_voice` и `mode: auto`.
- Assist озвучивает `speech` из ответа сервиса.
- Если сервис Tasklerq вернул ошибку, сценарий не падает: `continue_on_error`
  пропускает неудачный вызов, и Assist произносит нейтральную фразу. Причина
  ошибки во фразе не называется — она остаётся в журнале и трассировке Home
  Assistant. Для capture фраза предупреждает, что позиция могла не сохраниться.
- `continue_on_error` не спасает от ошибок конфигурации: если сервис
  `tasklerq.capture` не зарегистрирован (интеграция не установлена) или
  подставлено неверное значение, интент завершится ошибкой.
- Чисто числовой хвост фразы («добавь задачу 15») шаблонизатор Home Assistant
  может превратить в число, а схема сервиса ожидает строку. Формулируйте такие
  числа словами.
- Ответ сервиса в `intent_script` доступен через `action_response`: его
  возвращает завершающий шаг `stop` с `response_variable`. Шаблон
  `speech` рендерится после действий, поэтому `response` из
  `response_variable` напрямую в нём не виден. Если сервис упал, `stop` не
  находит `response`, завершает скрипт как `aborted`, и шаблон берёт запасную
  фразу.

## LLM-агент в Assist

Сценарий выше использует встроенный распознаватель фраз Home Assistant и
работает, когда в Assist выбран агент Home Assistant. Если выбран другой
LLM-агент, который не использует `conversation.intents`, создайте отдельный
pipeline с агентом Home Assistant для этих фраз либо добавьте вызов
`tasklerq.capture` как явное действие агента.

Assist-инструменты (`llm_hass_api`) AI Task для разбора текста не нужны:
Tasklerq разбирает фразу сам, агент получает только готовый текст.
