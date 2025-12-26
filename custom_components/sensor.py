from __future__ import annotations

from dataclasses import asdict
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVChargePlannerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator: EVChargePlannerCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            EVPlanTonightState(coordinator, entry),
            EVPlanTonightWindow(coordinator, entry),
            EVPlanTonightReason(coordinator, entry),
        ],
        update_before_add=True,
    )


class _Base(CoordinatorEntity[EVChargePlannerCoordinator]):
    def __init__(self, coordinator: EVChargePlannerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry


class EVPlanTonightState(_Base):
    _attr_name = "EV Plan Tonight State"
    _attr_icon = "mdi:car-electric"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_plan_tonight_state"

    @property
    def native_value(self):
        t = self.coordinator.data.get("tonight")
        return getattr(t, "state", "NO_DATA")

    @property
    def extra_state_attributes(self):
        t = self.coordinator.data.get("tonight")
        if t:
            return asdict(t)
        return {}


class EVPlanTonightWindow(_Base):
    _attr_name = "EV Plan Tonight Window"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_plan_tonight_window"

    @property
    def native_value(self):
        t = self.coordinator.data.get("tonight")
        if not t or not t.start or not t.end:
            return "–"
        # Show HH:MM–HH:MM (local)
        return f"{t.start.strftime('%H:%M')}–{t.end.strftime('%H:%M')}"


class EVPlanTonightReason(_Base):
    _attr_name = "EV Plan Tonight Reason"
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_plan_tonight_reason"

    @property
    def native_value(self):
        t = self.coordinator.data.get("tonight")
        return getattr(t, "reason", "")