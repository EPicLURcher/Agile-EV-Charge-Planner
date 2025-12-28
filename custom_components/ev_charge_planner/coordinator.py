from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DEFAULTS,
    OPT_BATTERY_KWH,
    OPT_CHARGER_POWER_KW,
    OPT_MIN_MORNING_SOC,
    OPT_SOC_BUFFER,
    OPT_DAILY_USAGE_PCT,
    OPT_FULL_TOMORROW_ENABLED,
    OPT_FULL_TOMORROW_TARGET,
    OPT_DEADLINE_ENABLED,
    OPT_FULL_BY,
    OPT_DEADLINE_TARGET,
)
from .planner.core import PlannerInputs, plan_charging
from .planner.normalise import (
    RateSlot,
    extract_list_from_attributes,
    parse_rates_list,
    merge_confirmed_over_forecast,
)

_LOGGER = logging.getLogger(__name__)


def _opt(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Read from entry.options with fallback to DEFAULTS (or provided default)."""
    if default is None:
        default = DEFAULTS.get(key)
    return entry.options.get(key, default)


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def _state_float(hass: HomeAssistant, entity_id: str, default: float = 0.0) -> float:
    st = hass.states.get(entity_id)
    if st is None:
        return default
    val = _safe_float(st.state)
    return default if val is None else val


def _parse_iso_dt(value: Any):
    """Parse an ISO datetime string (stored in options) and return local tz-aware dt."""
    if not value:
        return None
    dt = dt_util.parse_datetime(str(value))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(dt).astimezone(dt_util.DEFAULT_TIME_ZONE)


def _coerce_items_datetime_fields(items: list[dict]) -> list[dict]:
    """Ensure datetime fields in rate items are tz-aware datetimes."""
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        item2 = dict(item)

        # Try common keys used by rate providers
        for k in ("start", "date_time", "datetime", "from"):
            if k not in item2:
                continue

            raw = item2.get(k)
            if raw is None:
                break

            if isinstance(raw, str):
                dt = dt_util.parse_datetime(raw) or dt_util.parse_datetime(raw.replace("Z", "+00:00"))
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
    st = hass.states.get(entity_id)
    if st is None:
        return []
    attrs = st.attributes or {}
    items = extract_list_from_attributes(attrs)
    if not items:
        return []
    items = _coerce_items_datetime_fields(items)
    return parse_rates_list(items, tz_hint=dt_util.DEFAULT_TIME_ZONE)


def _get_injected_confirmed_rates(hass: HomeAssistant, entry_id: str) -> list[RateSlot]:
    """Rates injected via ev_charge_planner.refresh payload (optional)."""
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


class EVChargePlannerCoordinator(DataUpdateCoordinator[dict]):
    """Passive coordinator refreshed via ev_charge_planner.refresh."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,  # must not be None
            name=f"EV Planner {entry.title}",
            update_interval=None,
        )
        self.hass = hass
        self.entry = entry

    async def _async_update_data(self) -> dict:
        data = self.entry.data
        now = dt_util.now()

        # Rates (supplier-agnostic entities chosen in config flow)
        confirmed_current = _read_rates_from_entity(self.hass, data["confirmed_current_entity"])
        confirmed_next = _read_rates_from_entity(self.hass, data["confirmed_next_entity"])
        forecast = _read_rates_from_entity(self.hass, data["forecast_rates_entity"])
        injected_confirmed = _get_injected_confirmed_rates(self.hass, self.entry.entry_id)

        confirmed_all = confirmed_current + confirmed_next + injected_confirmed
        merged = merge_confirmed_over_forecast(confirmed_all, forecast)

        # Hybrid inputs:
        # - SoC comes from external sensor selected in config flow
        # - Everything else comes from entry.options (and is backed by integration entities / options flow)
        inputs = PlannerInputs(
            now=now,
            current_soc_pct=_state_float(self.hass, data["current_soc_entity"]),
            daily_usage_pct=float(_opt(self.entry, OPT_DAILY_USAGE_PCT)),
            battery_capacity_kwh=float(_opt(self.entry, OPT_BATTERY_KWH)),
            charger_power_kw=float(_opt(self.entry, OPT_CHARGER_POWER_KW)),
            min_morning_soc_pct=float(_opt(self.entry, OPT_MIN_MORNING_SOC)),
            soc_buffer_pct=float(_opt(self.entry, OPT_SOC_BUFFER)),
            full_tomorrow_enabled=bool(_opt(self.entry, OPT_FULL_TOMORROW_ENABLED)),
            full_tomorrow_target_soc_pct=float(_opt(self.entry, OPT_FULL_TOMORROW_TARGET)),
            deadline_enabled=bool(_opt(self.entry, OPT_DEADLINE_ENABLED)),
            full_by=_parse_iso_dt(_opt(self.entry, OPT_FULL_BY)),
            deadline_target_soc_pct=float(_opt(self.entry, OPT_DEADLINE_TARGET)),
        )

        result = plan_charging(confirmed_all, forecast, inputs)

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
            "tonight": _plan_dict(result.tonight),
            "next_charge": _plan_dict(result.next_charge),
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