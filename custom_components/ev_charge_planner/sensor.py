
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data["ev_charge_planner"][entry.entry_id]
    async_add_entities([EVPlannerSensor(coordinator, entry)])

class EVPlannerSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Tonight Plan"

    @property
    def native_value(self):
        return "NO_DATA"
