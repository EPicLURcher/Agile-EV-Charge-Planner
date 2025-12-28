from __future__ import annotations

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DEFAULTS, OPT_FULL_BY


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    async_add_entities([_FullByDateTime(entry)])


class _FullByDateTime(DateTimeEntity):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{OPT_FULL_BY}"
        self._attr_name = f"{entry.title} Full-by datetime"

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
        raw = self._entry.options.get(OPT_FULL_BY, DEFAULTS[OPT_FULL_BY])
        if not raw:
            return None
        dt = dt_util.parse_datetime(str(raw))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt_util.as_local(dt)

    async def async_set_value(self, value):
        # store as ISO in options (UTC)
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        dt_utc = dt_util.as_utc(dt)
        opts = dict(self._entry.options)
        opts[OPT_FULL_BY] = dt_utc.isoformat()
        self.hass.config_entries.async_update_entry(self._entry, options=opts)
        self.async_write_ha_state()