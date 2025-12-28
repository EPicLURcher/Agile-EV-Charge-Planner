from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities(
        [
            TonightStateSensor(coordinator, entry),
            TonightWindowSensor(coordinator, entry),
            TonightReasonSensor(coordinator, entry),
            NextChargeSensor(coordinator, entry),
            DeadlineStatusSensor(coordinator, entry),
            DeadlineSummarySensor(coordinator, entry),
        ]
    )


class _BasePlannerSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, key: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="EV Charge Planner",
            model="Vehicle-agnostic",
        )


class TonightStateSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "tonight_state", "Tonight plan")

    @property
    def native_value(self):
        t = (self.coordinator.data or {}).get("tonight") or {}
        return t.get("state")

    @property
    def extra_state_attributes(self):
        t = (self.coordinator.data or {}).get("tonight") or {}
        dbg = (self.coordinator.data or {}).get("debug") or {}
        return {
            "start": t.get("start"),
            "end": t.get("end"),
            "duration_hours": t.get("duration_hours"),
            "reason": t.get("reason"),
            "debug_confirmed_slots": dbg.get("confirmed_slots"),
            "debug_forecast_slots": dbg.get("forecast_slots"),
            "debug_merged_slots": dbg.get("merged_slots"),
        }


class TonightWindowSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "tonight_window", "Tonight window")

    @property
    def native_value(self):
        t = (self.coordinator.data or {}).get("tonight") or {}
        s, e = t.get("start"), t.get("end")
        if not s or not e:
            return None
        return f"{s} → {e}"


class TonightReasonSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "tonight_reason", "Tonight reason")

    @property
    def native_value(self):
        t = (self.coordinator.data or {}).get("tonight") or {}
        return t.get("reason")


class NextChargeSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "next_charge", "Next planned charge")

    @property
    def native_value(self):
        n = (self.coordinator.data or {}).get("next_charge")
        if not n:
            return None
        s, e = n.get("start"), n.get("end")
        if not s or not e:
            return None
        return f"{s} → {e}"

    @property
    def extra_state_attributes(self):
        n = (self.coordinator.data or {}).get("next_charge") or {}
        return {
            "state": n.get("state"),
            "start": n.get("start"),
            "end": n.get("end"),
            "duration_hours": n.get("duration_hours"),
            "reason": n.get("reason"),
        }


class DeadlineStatusSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "deadline_status", "Deadline status")

    @property
    def native_value(self):
        d = (self.coordinator.data or {}).get("deadline") or {}
        return d.get("status")


class DeadlineSummarySensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "deadline_summary", "Deadline summary")

    @property
    def native_value(self):
        d = (self.coordinator.data or {}).get("deadline") or {}
        return d.get("summary")
