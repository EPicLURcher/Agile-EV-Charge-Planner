from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    DEFAULTS,
    OPT_DEADLINE_ENABLED,
    OPT_FULL_TOMORROW_ENABLED,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    async_add_entities(
        [
            _OptionSwitch(entry, OPT_FULL_TOMORROW_ENABLED, "Full tomorrow"),
            _OptionSwitch(entry, OPT_DEADLINE_ENABLED, "Full by date/time"),
        ]
    )


class _OptionSwitch(SwitchEntity):
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
    def is_on(self) -> bool:
        return bool(self._entry.options.get(self._opt_key, DEFAULTS[self._opt_key]))

    async def async_turn_on(self, **kwargs) -> None:
        opts = dict(self._entry.options)
        opts[self._opt_key] = True
        self.hass.config_entries.async_update_entry(self._entry, options=opts)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        opts = dict(self._entry.options)
        opts[self._opt_key] = False
        self.hass.config_entries.async_update_entry(self._entry, options=opts)
        self.async_write_ha_state()