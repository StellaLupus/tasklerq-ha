# Tasklerq for Home Assistant

Custom component Home Assistant для [Tasklerq](https://github.com/StellaLupus/tasklerq):
списки как `todo`-сущности, задачи со сроком в календаре, сервисы capture для
Assist и автоматизаций.

Минимальная версия Home Assistant — 2026.10.0, текущая целевая — 2026.10.x.

Готовый пример для голосового capture в Assist: [`examples/assist/`](examples/assist/).

## Установка

### HACS

1. HACS → **Integrations** → меню → **Custom repositories**.
2. Добавьте `https://github.com/StellaLupus/tasklerq-ha`, категория
   **Integration**.
3. Установите **Tasklerq** и перезапустите Home Assistant.
4. **Настройки → Устройства и службы → Добавить интеграцию → Tasklerq**.

### Вручную

Скопируйте каталог `custom_components/tasklerq` из репозитория или релиза в
`/config/custom_components/tasklerq`, перезапустите Home Assistant и добавьте
интеграцию через UI.

Для Tasklerq по адресу вне локальной сети используйте `https://`: `http://`
принимается только для локальных хостов. HTTPS также нужен для адресов
VPN-оверлеев вроде Tailscale (`100.64.0.0/10`): они не относятся к локальной
сети.

## Разработка

Проект использует [uv](https://docs.astral.sh/uv/) и Python 3.14.2+.

```bash
uv sync
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked mypy
uv run --locked pytest -q
```

Тесты используют `pytest-homeassistant-custom-component` версии, соответствующей
Home Assistant 2026.10.x.
