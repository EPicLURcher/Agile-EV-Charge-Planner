from __future__ import annotations

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


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def _coerce_items_datetime_fields(items: list[dict]) -> list[dict]:
    """
    Home Assistant attributes often contain ISO strings (e.g. '2025-12-28T16:00:00Z').
    The pure normaliser can accept strings, but we ensure they parse consistently and
    are timezone-aware.

    We convert any known datetime field present into a tz-aware datetime object.
    """
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        item2 = dict(item)

        # Coerce in priority order. Normaliser checks these keys too.
        for k in ("start", "date_time", "datetime", "from"):
            if k not in item2:
                continue
            raw = item2.get(k)
            if raw is None:
                continue

            if isinstance(raw, str):
                dt = dt_util.parse_datetime(raw)
                if dt is None:
                    # try handling "Z" explicitly (dt_util.parse_datetime usually does, but safe)
                    dt = dt_util.parse_datetime(raw.replace("Z", "+00:00"))
                if dt is None:
                    break  # leave as-is; normaliser will skip
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)
                item2[k] = dt

            elif hasattr(raw, "tzinfo"):
                # datetime object already (some tests or custom entities)
                dt = raw
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)
                item2[k] = dt

            break

        out.append(item2)

    return out


def _read_rates_from_entity(hass: HomeAssistant, entity_id: str) -> list[RateSlot]:
    st = hass.states.get(entity_id)
    if st is None:
        return []

    attrs = st.attributes or {}
    items = extract_list_from_attributes(attrs)
    if not items:
        return []

    items = _coerce_items_datetime_fields(items)

    # parse_rates_list expects tz-aware datetimes; will also accept strings if any remain
    return parse_rates_list(items, tz_hint=dt_util.DEFAULT_TIME_ZONE)


def _get_injected_confirmed_rates(hass: HomeAssistant, entry_id: str) -> list[RateSlot]:
    """
    Optional confirmed rate injection store:
      hass.data[DOMAIN]["confirmed_rates"][entry_id][iso] = price_p_per_kwh
    """
    store = hass.data.get(DOMAIN, {}).get("confirmed_rates", {}).get(entry_id, {})
    out: list[RateSlot] = []

    for iso, price in store.items():
        dt = dt_util.parse_datetime(iso)
        if dt is None:
            dt = dt_util.parse_datetime(str(iso).replace("Z", "+00:00"))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)

        out.append(RateSlot(start=dt, price_p_per_kwh=float(price)))

    return sorted(out, key=lambda r: r.start)


def _state_bool(hass: HomeAssistant, entity_id: str) -> bool:
    st = hass.states.get(entity_id)
    if st is None:
        return False
    return str(st.state).lower() in ("on", "true", "1")


def _state_float(hass: HomeAssistant, entity_id: str, default: float = 0.0) -> float:
    st = hass.states.get(entity_id)
    if st is None:
        return default
    v = _safe_float(st.state)
    return default if v is None else v


def _state_datetime(hass: HomeAssistant, entity_id: str) -> Optional[dt_util.dt.datetime]:
    st = hass.states.get(entity_id)
    if st is None:
        return None
    raw = st.state
    dt = dt_util.parse_datetime(raw)
    if dt is None:
        dt = dt_util.parse_datetime(str(raw).replace("Z", "+00:00"))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)


class EVChargePlannerCoordinator(DataUpdateCoordinator[dict]):
    """
    Passive coordinator: refreshes only when manually requested (service call)
    or when you call coordinator.async_request_refresh() from automations.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=None,
            name=f"EV Planner {entry.title}",
            update_interval=None,
        )
        self.hass = hass
        self.entry = entry

    async def _async_update_data(self) -> dict:
        d = self.entry.data
        now = dt_util.now()

        # --- rates: 3 streams (current confirmed + next confirmed + forecast) ---
        confirmed_current = _read_rates_from_entity(self.hass, d["confirmed_current_entity"])
        confirmed_next = _read_rates_from_entity(self.hass, d["confirmed_next_entity"])
        forecast = _read_rates_from_entity(self.hass, d["forecast_rates_entity"])

        injected_confirmed = _get_injected_confirmed_rates(self.hass, self.entry.entry_id)

        confirmed_all = confirmed_current + confirmed_next + injected_confirmed

        # merged timeline is useful for debugging counts; planner still gets both lists
        merged = merge_confirmed_over_forecast(confirmed_all, forecast)

        # --- vehicle inputs ---
        inputs = PlannerInputs(
            now=now,
            current_soc_pct=_state_float(self.hass, d["current_soc_entity"], 0.0),
            daily_usage_pct=_state_float(self.hass, d["daily_usage_entity"], 0.0),
            battery_capacity_kwh=_state_float(self.hass, d["battery_kwh_entity"], 0.0),
            charger_power_kw=float(d["charger_power_kw"]),
            min_morning_soc_pct=float(d["min_morning_soc"]),
            soc_buffer_pct=float(d["soc_buffer"]),
            full_tomorrow_enabled=_state_bool(self.hass, d["full_tomorrow_enabled_entity"]),
            full_tomorrow_target_soc_pct=_state_float(self.hass, d["full_tomorrow_target_entity"], 100.0),
            deadline_enabled=_state_bool(self.hass, d["deadline_enabled_entity"]),
            full_by=_state_datetime(self.hass, d["full_by_entity"]),
            deadline_target_soc_pct=_state_float(self.hass, d["deadline_target_entity"], 100.0),
        )

        out = plan_charging(confirmed_all, forecast, inputs)

        def _plan_dict(p):
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
            "tonight": _plan_dict(out.tonight),
            "next_charge": _plan_dict(out.next_charge),
            "deadline": {"status": out.deadline.status, "summary": out.deadline.summary},
            "debug": {
                "confirmed_current_slots": len(confirmed_current),
                "confirmed_next_slots": len(confirmed_next),
                "injected_confirmed_slots": len(injected_confirmed),
                "confirmed_total_slots": len(confirmed_all),
                "forecast_slots": len(forecast),
                "merged_slots": len(merged),
            },
        }
