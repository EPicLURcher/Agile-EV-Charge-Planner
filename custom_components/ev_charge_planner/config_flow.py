from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN


STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="My EV"): str,

        vol.Required("confirmed_current_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),
        vol.Required("confirmed_next_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),
        vol.Required("forecast_rates_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),

        vol.Required("current_soc_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)


class EVChargePlannerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        return self.async_create_entry(title=user_input["name"], data=user_input)

    @staticmethod
    def async_get_options_flow(config_entry):
        from .options_flow import EVChargePlannerOptionsFlowHandler
        return EVChargePlannerOptionsFlowHandler(config_entry)