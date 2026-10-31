"""Config flow Tasklerq."""

from custom_components.tasklerq.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_user_form(hass: HomeAssistant) -> None:
    """Config flow показывает форму ввода URL и токена."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] is None
