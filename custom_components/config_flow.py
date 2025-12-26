from __future__ import annotations

from homeassistant import config_entries
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_SOC_ENTITY,
    CONF_FORECAST_ENTITY,
    DEFAULT_SOC_ENTITY,
    DEFAULT_FORECAST_ENTITY,
)


class EVChargePlannerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(f"{DOMAIN}_{user_input[CONF_NAME].strip().lower()}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_SOC_ENTITY: user_input[CONF_SOC_ENTITY],
                    CONF_FORECAST_ENTITY: user_input[CONF_FORECAST_ENTITY],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="My EV"): str,
                vol.Required(
                    CONF_SOC_ENTITY, default=DEFAULT_SOC_ENTITY
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor"])
                ),
                vol.Required(
                    CONF_FORECAST_ENTITY, default=DEFAULT_FORECAST_ENTITY
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor"])
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)