from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, SERVICE_REFRESH, PLATFORMS
from .coordinator import EVChargePlannerCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = EVChargePlannerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")  # optional
        rates = call.data.get("rates")        # optional Octopus rates payload

        async def _refresh_one(coord: EVChargePlannerCoordinator) -> None:
            if rates:
                coord.set_confirmed_rates(rates)
            await coord.async_request_refresh()

        if entry_id:
            coord = hass.data.get(DOMAIN, {}).get(entry_id)
            if coord:
                await _refresh_one(coord)
            return

        # Refresh all instances
        for coord in hass.data.get(DOMAIN, {}).values():
            await _refresh_one(coord)

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Remove service when last instance removed
    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)

    return True