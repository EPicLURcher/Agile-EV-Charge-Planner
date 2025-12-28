from __future__ import annotations

from typing import Any, Optional, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN

from .planner.core import RateSlot, PlannerInputs, plan_charging


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_rate_list_to_rateslots(rate_list: list[dict]) -> list[RateSlot]:
    """
    Supplier-agnostic-ish normaliser that supports your Octopus next_day_rates format:
      - list key: rates
      - item keys: start, value_inc_vat (in £/kWh)
    Also supports common alternatives: start/date_time/from + price/value/price_p_per_kwh/p_per_kwh.
    Returns RateSlot list in p/kWh with tz-aware datetimes.
    """
    out: list[RateSlot] = []
    for item in rate_list:
        if not isinstance(item, dict):
            continue

        raw_dt = item.get("start") or item.get("date_time") or item.get("from")
        if raw_dt is None:
            continue
        dt = dt_util.parse_datetime(str(raw_dt))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

        raw_price = (
            item.get("price_p_per_kwh")
            or item.get("p_per_kwh")
            or item.get("price")
            or item.get("value")
            or item.get("value_inc_vat")  # Octopus events
        )
        price = _safe_float(raw_price)
        if price is None:
            continue

        # Unit normalisation:
        # - Octopus "value_inc_vat" is usually £/kWh e.g. 0.167055 -> 16.7055 p/kWh
        # - If already in p/kWh it's typically > 1.0
        if -1.0 < price < 1.0:
            price = price * 100.0

        # Normalise datetime to local tz
        dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)

        out.append(RateSlot(start=dt, price_p_per_kwh=float(price)))

    return sorted(out, key=lambda r: r.start)


def _read_rates_from_entity(hass: HomeAssistant, entity_id: str) -> list[RateSlot]:
    st = hass.states.get(entity_id)
    if st is None:
        return []

    attrs = st.attributes or {}

    # Common containers for list payloads
    rate_list = None
    for key in ("rates", "prices", "data", "slots", "items"):
        if isinstance(attrs.get(key), list):
            rate_list = attrs.get(key)
            break

    if not isinstance(rate_list, list):
        return []

    return _parse_rate_list_to_rateslots(rate_list)


def _get_confirmed_rates_for_entry(hass: HomeAssistant, entry_id: str) -> list[RateSlot]:
    store = hass.data.get(DOMAIN, {}).get("confirmed_rates", {}).get(entry_id, {})
    out: list[RateSlot] = []
    for iso, price in store.items():
        dt = dt_util.parse_datetime(iso)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        dt = dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)

        p = float(price)
        # assume confirmed already sent in p/kWh
        out.append(RateSlot(start=dt, price_p_per_kwh=p))
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
    dt = dt_util.parse_datetime(st.state)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)


class EVChargePlannerCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=None,
            name=f"EV Planner {entry.title}",
            update_interval=None,  # passive: refresh only when service is called
        )
        self.hass = hass
        self.entry = entry

    async def _async_update_data(self) -> dict:
        data = self.entry.data

        # If config flow hasn't been wired yet, return safe NO_DATA.
        required_keys = [
            "forecast_rates_entity",
            "current_soc_entity",
            "daily_usage_entity",
            "battery_kwh_entity",
            "full_tomorrow_enabled_entity",
            "full_tomorrow_target_entity",
            "deadline_enabled_entity",
            "full_by_entity",
            "deadline_target_entity",
            "charger_power_kw",
            "min_morning_soc",
            "soc_buffer",
        ]
        if not all(k in data for k in required_keys):
            return {
                "tonight": {
                    "state": "NO_DATA",
                    "start": None,
                    "end": None,
                    "duration_hours": 0.0,
                    "reason": "Integration not fully configured yet (config flow missing fields).",
                },
                "next_charge": None,
                "deadline": {"status": "DISABLED", "summary": "Deadline mode disabled."},
            }

        now = dt_util.now()

        confirmed = _get_confirmed_rates_for_entry(self.hass, self.entry.entry_id)
        forecast = _read_rates_from_entity(self.hass, data["forecast_rates_entity"])

        inputs = PlannerInputs(
            now=now,
            current_soc_pct=_state_float(self.hass, data["current_soc_entity"], 0.0),
            daily_usage_pct=_state_float(self.hass, data["daily_usage_entity"], 0.0),
            battery_capacity_kwh=_state_float(self.hass, data["battery_kwh_entity"], 0.0),
            charger_power_kw=float(data["charger_power_kw"]),
            min_morning_soc_pct=float(data["min_morning_soc"]),
            soc_buffer_pct=float(data["soc_buffer"]),
            full_tomorrow_enabled=_state_bool(self.hass, data["full_tomorrow_enabled_entity"]),
            full_tomorrow_target_soc_pct=_state_float(self.hass, data["full_tomorrow_target_entity"], 100.0),
            deadline_enabled=_state_bool(self.hass, data["deadline_enabled_entity"]),
            full_by=_state_datetime(self.hass, data["full_by_entity"]),
            deadline_target_soc_pct=_state_float(self.hass, data["deadline_target_entity"], 100.0),
        )

        out = plan_charging(confirmed, forecast, inputs)

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
        }
