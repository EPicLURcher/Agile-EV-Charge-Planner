from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    DEFAULTS,
    OPT_DAILY_USAGE_PCT,
    OPT_TARGET_SOC
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    async_add_entities(
        [
            _PctNumber(entry, OPT_DAILY_USAGE_PCT, "Daily usage"),
            _PctNumber(entry, OPT_TARGET_SOC, "Full tomorrow target"),
        ]
    )


class _PctNumber(NumberEntity):
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    def __init__(self, entry: ConfigEntry, opt_key: str, name_suffix: str) -> None:
        self._entry = entry
        self._opt_key = opt_key
        self._attr_unique_id = f"{entry.entry_id}_{opt_key}"
        self._attr_name = f"{entry.title} {name_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="EV Charge Planner",
            model="Vehicle-agnostic",
        )

    @property
    def native_value(self):
        return float(self._entry.options.get(self._opt_key, DEFAULTS[self._opt_key]))

    async def async_set_native_value(self, value: float) -> None:
        opts = dict(self._entry.options)
        opts[self._opt_key] = float(value)
        self.hass.config_entries.async_update_entry(self._entry, options=opts)
        self.async_write_ha_state()