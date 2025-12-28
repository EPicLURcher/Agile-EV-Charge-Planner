from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import (
    DEFAULTS,
    OPT_BATTERY_KWH,
    OPT_CHARGER_POWER_KW,
    OPT_MIN_MORNING_SOC,
    OPT_SOC_BUFFER,
)


class EVChargePlannerOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input=None):
        opts = dict(self.entry.options)

        if user_input is not None:
            opts.update(user_input)
            return self.async_create_entry(title="", data=opts)

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_CHARGER_POWER_KW,
                    default=opts.get(OPT_CHARGER_POWER_KW, DEFAULTS[OPT_CHARGER_POWER_KW]),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_BATTERY_KWH,
                    default=opts.get(OPT_BATTERY_KWH, DEFAULTS[OPT_BATTERY_KWH]),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_MIN_MORNING_SOC,
                    default=opts.get(OPT_MIN_MORNING_SOC, DEFAULTS[OPT_MIN_MORNING_SOC]),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_SOC_BUFFER,
                    default=opts.get(OPT_SOC_BUFFER, DEFAULTS[OPT_SOC_BUFFER]),
                ): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)