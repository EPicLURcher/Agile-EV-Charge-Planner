from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities(
        [
            TonightStateSensor(coordinator, entry),
            TonightWindowSensor(coordinator, entry),
            TonightReasonSensor(coordinator, entry),
            NextChargeSensor(coordinator, entry),
            DeadlineStatusSensor(coordinator, entry),
            DeadlineSummarySensor(coordinator, entry),
            ChargeToAddPctSensor(coordinator, entry),
            ChargeHoursRequiredSensor(coordinator, entry),
            TonightEstimatedCostSensor(coordinator, entry),
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
            "debug_confirmed_current_slots": dbg.get("confirmed_current_slots"),
            "debug_confirmed_next_slots": dbg.get("confirmed_next_slots"),
            "debug_injected_confirmed_slots": dbg.get("injected_confirmed_slots"),
            "debug_forecast_slots": dbg.get("forecast_slots"),
            "debug_merged_slots": dbg.get("merged_slots"),
            "debug_target_soc_pct": dbg.get("target_soc_pct"),
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


class ChargeToAddPctSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "charge_to_add_pct", "Charge to add (%)")
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        m = (self.coordinator.data or {}).get("metrics") or {}
        v = m.get("needed_soc_pct")
        if v is None:
            return None
        return round(float(v), 1)

    @property
    def extra_state_attributes(self):
        m = (self.coordinator.data or {}).get("metrics") or {}
        return {
            "needed_energy_kwh": m.get("needed_energy_kwh"),
            "needed_hours": m.get("needed_hours"),
            "needed_slots": m.get("needed_slots"),
            "effective_target_soc_pct": m.get("effective_target_soc_pct"),
        }


class ChargeHoursRequiredSensor(_BasePlannerSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "charge_hours_required", "Charge hours required")
        self._attr_native_unit_of_measurement = "h"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        m = (self.coordinator.data or {}).get("metrics") or {}
        v = m.get("needed_hours")
        if v is None:
            return None
        return round(float(v), 2)

    @property
    def extra_state_attributes(self):
        m = (self.coordinator.data or {}).get("metrics") or {}
        return {
            "needed_soc_pct": m.get("needed_soc_pct"),
            "needed_energy_kwh": m.get("needed_energy_kwh"),
            "needed_slots": m.get("needed_slots"),
            "effective_target_soc_pct": m.get("effective_target_soc_pct"),
        }


class TonightEstimatedCostSensor(_BasePlannerSensor):
    """Estimated total cost for tonight's chosen charging window (if any).

    Assumes planner metric `tonight_estimated_cost` is in *pence* (because rates are p/kWh).
    Exposes value as GBP for nicer HA handling.
    """

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "tonight_estimated_cost", "Tonight estimated cost")
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:cash"

    @property
    def native_value(self):
        m = (self.coordinator.data or {}).get("metrics") or {}
        v_pence = m.get("tonight_estimated_cost")
        if v_pence is None:
            return None
        return round(float(v_pence) / 100.0, 2)

    @property
    def extra_state_attributes(self):
        t = (self.coordinator.data or {}).get("tonight") or {}
        m = (self.coordinator.data or {}).get("metrics") or {}
        return {
            "tonight_state": t.get("state"),
            "start": t.get("start"),
            "end": t.get("end"),
            "planned_slots": m.get("tonight_planned_slots"),
            "estimated_cost_pence": m.get("tonight_estimated_cost"),
        }