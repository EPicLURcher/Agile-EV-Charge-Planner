
from homeassistant.core import HomeAssistant, ServiceCall
from .const import DOMAIN, SERVICE_REFRESH

def async_register_services(hass: HomeAssistant):
    async def handle_refresh(call: ServiceCall):
        # placeholder for confirmed rate injection
        pass

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
