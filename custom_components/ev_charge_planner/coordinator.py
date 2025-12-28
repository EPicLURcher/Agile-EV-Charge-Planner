
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

class EVChargePlannerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            logger=None,
            name=f"EV Planner {entry.title}",
            update_interval=None,
        )

    async def _async_update_data(self):
        return {}
