from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .planner.core import PlannerInputs, plan_charging
from .planner.normalise import (
    RateSlot,
    extract_list_from_attributes,
    parse_rates_list,
    merge_confirmed_over_forecast,
)

_LOGGER = logging.getLogger(__name__)


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def _coerce_items_datetime_fields(items: list[dict]) -> list[dict]:
    """Ensure datetime fields are tz-aware datetimes."""
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        item2 = dict(item)

        for k in ("start", "date_time", "datetime", "from"):
            if k not in item2:
                continue

            raw = item2.get(k)
            if raw is None:
                break

            if isinstance(raw, str):
                dt = dt_util.parse_datetime(raw)
                if dt is None:
                    dt = dt_util.parse_datetime(raw.replace("Z", "+00:00"))
                if dt is None:
                    break
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                item2[k] = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)

            elif hasattr(raw, "tzinfo"):
                dt = raw
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                item2[k] = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)

            break

        out.append(item2)

    return out


def _read_rates_from_entity(hass: HomeAssistant, entity_id: str) -> list[RateSlot]:
    state = hass.states.get(entity_id)
    if state is None:
        return []

    attrs = state.attributes or {}
    items = extract_list_from_attributes(attrs)
    if not items:
        return []

    items = _coerce_items_datetime_fields(items)
    return parse_rates_list(items, tz_hint=dt_util.DEFAULT_TIME_ZONE)


def _get_injected_confirmed_rates(hass: HomeAssistant, entry_id: str) -> list[RateSlot]:
    store = hass.data.get(DOMAIN, {}).get("confirmed_rates", {}).get(entry_id, {})
    out: list[RateSlot] = []

    for iso, price in store.items():
        dt = dt_util.parse_datetime(str(iso))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)
        out.append(RateSlot(start=dt, price_p_per_kwh=float(price)))

    return sorted(out, key=lambda r: r.start)


def _state_bool(hass: HomeAssistant, entity_id: str) -> bool:
    st = hass.states.get(entity_id)
    return st is not None and str(st.state).lower() in ("on", "true", "1")


def _state_float(hass: HomeAssistant, entity_id: str, default: float = 0.0) -> float:
    st = hass.states.get(entity_id)
    if st is None:
        return default
    val = _safe_float(st.state)
    return default if val is None else val


def _state_datetime(hass: HomeAssistant, entity_id: str):
    st = hass.states.get(entity_id)
    if st is None:
        return None
    dt = dt_util.parse_datetime(str(st.state))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)


class EVChargePlannerCoordinator(DataUpdateCoordinator[dict]):
    """Passive coordinator refreshed via service calls or automations."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,  # ✅ FIX: must not be None
            name=f"EV Planner {entry.title}",
            update_interval=None,
        )
        self.hass = hass
        self.entry = entry

    async def _async_update_data(self) -> dict:
        data = self.entry.data
        now = dt_util.now()

        confirmed_current = _read_rates_from_entity(self.hass, data["confirmed_current_entity"])
        confirmed_next = _read_rates_from_entity(self.hass, data["confirmed_next_entity"])
        forecast = _read_rates_from_entity(self.hass, data["forecast_rates_entity"])
        injected_confirmed = _get_injected_confirmed_rates(self.hass, self.entry.entry_id)

        confirmed_all = confirmed_current + confirmed_next + injected_confirmed
        merged = merge_confirmed_over_forecast(confirmed_all, forecast)

        inputs = PlannerInputs(
            now=now,
            current_soc_pct=_state_float(self.hass, data["current_soc_entity"]),
            daily_usage_pct=_state_float(self.hass, data["daily_usage_entity"]),
            battery_capacity_kwh=_state_float(self.hass, data["battery_kwh_entity"]),
            charger_power_kw=float(data["charger_power_kw"]),
            min_morning_soc_pct=float(data["min_morning_soc"]),
            soc_buffer_pct=float(data["soc_buffer"]),
            full_tomorrow_enabled=_state_bool(self.hass, data["full_tomorrow_enabled_entity"]),
            full_tomorrow_target_soc_pct=_state_float(
                self.hass, data["full_tomorrow_target_entity"], 100.0
            ),
            deadline_enabled=_state_bool(self.hass, data["deadline_enabled_entity"]),
            full_by=_state_datetime(self.hass, data["full_by_entity"]),
            deadline_target_soc_pct=_state_float(
                self.hass, data["deadline_target_entity"], 100.0
            ),
        )

        result = plan_charging(confirmed_all, forecast, inputs)

        def plan_dict(p):
            if p is None:
                return None
            return {
                "state": p.state,
                "start": p.start.isoformat() if p.start else None,
                "end": p.end.isoformat() if p.end else None,
                "duration_hours": p.duration_hours,
                "reason": p.reason,
            }

        return {
            "tonight": plan_dict(result.tonight),
            "next_charge": plan_dict(result.next_charge),
            "deadline": {
                "status": result.deadline.status,
                "summary": result.deadline.summary,
            },
            "debug": {
                "confirmed_current_slots": len(confirmed_current),
                "confirmed_next_slots": len(confirmed_next),
                "injected_confirmed_slots": len(injected_confirmed),
                "forecast_slots": len(forecast),
                "merged_slots": len(merged),
            },
        }