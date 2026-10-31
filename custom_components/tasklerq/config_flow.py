"""Config flow Tasklerq."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_TOKEN, CONF_URL, DOMAIN


class TasklerqConfigFlow(ConfigFlow, domain=DOMAIN):
    """Настройка подключения к Tasklerq."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Запросить URL и API-токен Tasklerq."""
        if user_input is not None:
            return self.async_create_entry(title="Tasklerq", data=user_input)

        return self.async_show_form(
            step_id="user",
            # HA 2026.10 aliases voluptuous to probatio; the explicit ignore
            # keeps compatibility with older HA releases and their stubs.
            data_schema=vol.Schema(  # type: ignore[arg-type]
                {
                    vol.Required(CONF_URL): str,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
        )
