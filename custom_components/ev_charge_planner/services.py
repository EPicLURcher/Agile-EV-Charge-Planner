from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_REFRESH

RATE_SCHEMA = vol.Schema(
    {
        vol.Required("start"): vol.Coerce(str),
        vol.Required("price_p_per_kwh"): vol.Coerce(float),
    }
)

SERVICE_SCHEMA = vol.Schema(
    {
        # target a single config entry
        vol.Optional("entry_id"): vol.Coerce(str),
        # optional list of confirmed rates
        vol.Optional("rates"): vol.All(list, [RATE_SCHEMA]),
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    async def _handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        rates = call.data.get("rates", [])

        hass.data.setdefault(DOMAIN, {})
        store = hass.data[DOMAIN].setdefault("confirmed_rates", {})  # entry_id -> {iso: price}

        def _apply_rates(target_entry_id: str) -> None:
            per = store.setdefault(target_entry_id, {})
            for r in rates:
                dt = dt_util.parse_datetime(r["start"])
                if dt is None:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)
                per[dt.isoformat()] = float(r["price_p_per_kwh"])

        if entry_id:
            _apply_rates(entry_id)
            # Refresh that entry's coordinator if loaded
            entry_obj = hass.data.get(DOMAIN, {}).get(entry_id)
            if isinstance(entry_obj, dict) and "coordinator" in entry_obj:
                await entry_obj["coordinator"].async_request_refresh()
        else:
            # Apply to all loaded entries
            for k, obj in hass.data.get(DOMAIN, {}).items():
                if not isinstance(obj, dict) or "coordinator" not in obj:
                    continue
                _apply_rates(k)
                await obj["coordinator"].async_request_refresh()

    # Register once
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=SERVICE_SCHEMA)
