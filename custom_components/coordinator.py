from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import as_local, now as ha_now

from .const import (
    DOMAIN,
    CONF_SOC_ENTITY,
    CONF_FORECAST_ENTITY,
    DEFAULT_SOC_ENTITY,
    DEFAULT_FORECAST_ENTITY,
    FORECAST_ATTR,
    FORECAST_TIME_KEY,
    FORECAST_PRICE_KEY,
    HELPER_DAILY_USE,
    HELPER_MIN_MORNING,
    HELPER_BUFFER,
    HELPER_BATT_KWH,
    HELPER_CHARGER_KW,
    HELPER_OVERRIDE,
    HELPER_FULL_TARGET,
)

from .plannerlib import PlannerConfig, UserInputs, Slot, plan_tonight

_LOGGER = logging.getLogger(__name__)


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return as_local(dt)
    except Exception:
        return None


def _get_float(hass: HomeAssistant, entity_id: str, default: float) -> float:
    st = hass.states.get(entity_id)
    if st is None:
        return default
    v = st.state
    if v in (None, "unknown", "unavailable", ""):
        return default
    try:
        return float(v)
    except Exception:
        return default


def _get_bool(hass: HomeAssistant, entity_id: str, default: bool = False) -> bool:
    st = hass.states.get(entity_id)
    if st is None:
        return default
    if st.state in ("unknown", "unavailable", ""):
        return default
    return st.state == "on"


class EVChargePlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.soc_entity = entry.data.get(CONF_SOC_ENTITY, DEFAULT_SOC_ENTITY)
        self.forecast_entity = entry.data.get(CONF_FORECAST_ENTITY, DEFAULT_FORECAST_ENTITY)

        # confirmed slots cache: start_dt -> Slot
        self._confirmed: dict[datetime, Slot] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.title}",
            update_interval=timedelta(minutes=10),  # calm by default; your automation triggers refresh when needed
        )

    def set_confirmed_rates(self, rates: list[dict[str, Any]]) -> None:
        """
        Accept Octopus rates list items like:
        { start, end, value_inc_vat } where value_inc_vat is £/kWh.
        Convert to p/kWh and cache by start time.
        """
        new_map: dict[datetime, Slot] = {}
        for r in rates or []:
            dt = _as_dt(r.get("start"))
            v = r.get("value_inc_vat")
            if dt is None or v is None:
                continue
            try:
                p_per_kwh = float(v) * 100.0
            except Exception:
                continue
            new_map[dt] = Slot(start=dt, p_per_kwh=p_per_kwh, is_confirmed=True)

        if new_map:
            self._confirmed.update(new_map)
            _LOGGER.debug("Stored %d confirmed slots (total %d)", len(new_map), len(self._confirmed))

    def _read_forecast_slots(self) -> list[Slot]:
        st = self.hass.states.get(self.forecast_entity)
        prices = (st.attributes.get(FORECAST_ATTR) if st else None) or []
        out: list[Slot] = []

        for item in prices:
            if not isinstance(item, dict):
                continue
            dt = _as_dt(item.get(FORECAST_TIME_KEY))
            if dt is None:
                continue
            try:
                p = float(item.get(FORECAST_PRICE_KEY))
            except Exception:
                continue
            out.append(Slot(start=dt, p_per_kwh=p, is_confirmed=False))

        out.sort(key=lambda s: s.start)
        return out

    def _combine_slots(self, forecast: list[Slot]) -> list[Slot]:
        by_start = {s.start: s for s in forecast}
        for dt, cs in self._confirmed.items():
            by_start[dt] = cs
        combined = list(by_start.values())
        combined.sort(key=lambda s: s.start)
        return combined

    async def _async_update_data(self) -> dict[str, Any]:
        soc_now = _get_float(self.hass, self.soc_entity, 0.0)
        daily_use = _get_float(self.hass, HELPER_DAILY_USE, 0.0)
        min_morning = _get_float(self.hass, HELPER_MIN_MORNING, 35.0)
        buffer = _get_float(self.hass, HELPER_BUFFER, 3.0)
        battery_kwh = _get_float(self.hass, HELPER_BATT_KWH, 75.0)
        charger_kw = _get_float(self.hass, HELPER_CHARGER_KW, 7.2)

        need_full = _get_bool(self.hass, HELPER_OVERRIDE, False)
        full_target = _get_float(self.hass, HELPER_FULL_TARGET, min_morning)

        cfg = PlannerConfig(
            plug_start_hour=17,
            plug_end_hour=7,
            charger_kw=charger_kw,
            efficiency=0.9,
        )

        inp = UserInputs(
            soc_now=soc_now,
            daily_soc_use=daily_use,
            min_morning_soc=min_morning,
            soc_buffer=buffer,
            battery_kwh=battery_kwh,
            need_full_tomorrow=need_full,
            full_target_soc=full_target,
        )

        forecast_slots = self._read_forecast_slots()
        slots = self._combine_slots(forecast_slots)

        result = plan_tonight(ha_now(), cfg, inp, slots)

        return {
            "tonight": result,
            "tonight_dict": asdict(result),
            "meta": {
                "soc_now": soc_now,
                "need_full_tomorrow": need_full,
                "confirmed_slots": len(self._confirmed),
                "forecast_slots": len(forecast_slots),
            },
        }