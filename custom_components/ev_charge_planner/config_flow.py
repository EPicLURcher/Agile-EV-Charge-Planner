from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN


STEP_USER_SCHEMA = vol.Schema(
    {
        # Friendly name for this EV instance
        vol.Required("name", default="My EV"): str,

        # --- Rate sources (supplier-agnostic) ---
        vol.Required("confirmed_current_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),
        vol.Required("confirmed_next_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),
        vol.Required("forecast_rates_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig()
        ),

        # --- Vehicle inputs ---
        vol.Required("current_soc_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),

        # Daily usage (% per day) + battery capacity (kWh)
        vol.Required("daily_usage_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
        ),
        vol.Required("battery_kwh_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
        ),

        # --- Charger characteristics ---
        vol.Required("charger_power_kw", default=7.0): vol.Coerce(float),

        # --- Baseline objective (normal mode) ---
        vol.Required("min_morning_soc", default=40.0): vol.Coerce(float),
        vol.Required("soc_buffer", default=5.0): vol.Coerce(float),

        # --- Full tomorrow override ---
        vol.Required("full_tomorrow_enabled_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor"])
        ),
        vol.Required("full_tomorrow_target_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
        ),

        # --- Full by datetime override ---
        vol.Required("deadline_enabled_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor"])
        ),
        vol.Required("full_by_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_datetime", "sensor"])
        ),
        vol.Required("deadline_target_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
        ),
    }
)


class EVChargePlannerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        # Create one config entry per EV.
        title = user_input["name"]
        return self.async_create_entry(title=title, data=user_input)
