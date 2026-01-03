
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Iterable, List, Optional, Dict, Tuple
from ..const import DEFAULT_CHARGING_EFFICIENCY


@dataclass(frozen=True)
class RateSlot:
    start: datetime  # timezone-aware
    price_p_per_kwh: float


@dataclass(frozen=True)
class PlannerInputs:
    now: datetime  # timezone-aware

    # Vehicle & usage
    current_soc_pct: float
    daily_usage_pct: float
    battery_capacity_kwh: float

    # Charger
    charger_power_kw: float

    # Baseline objective
    min_morning_soc_pct: float
    soc_buffer_pct: float

    # Overrides
    full_tomorrow_enabled: bool
    full_tomorrow_target_soc_pct: float

    deadline_enabled: bool
    full_by: Optional[datetime]  # timezone-aware if provided
    deadline_target_soc_pct: float


@dataclass(frozen=True)
class TonightPlan:
    state: str  # PLUG_IN | NO_NEED | NO_DATA | AT_RISK
    start: Optional[datetime]
    end: Optional[datetime]
    duration_hours: float
    reason: str


@dataclass(frozen=True)
class DeadlineStatus:
    status: str  # ON_TRACK | AT_RISK | DISABLED
    summary: str

@dataclass(frozen=True)
class ChargeMetrics:
    needed_soc_pct: float          # % to add
    needed_energy_kwh: float
    needed_hours: float
    needed_slots: int

@dataclass(frozen=True)
class PlannerOutputs:
    tonight: TonightPlan
    next_charge: Optional[TonightPlan]
    deadline: DeadlineStatus
    metrics: ChargeMetrics

def _is_tz_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _ceil_slots(hours: float, slot_hours: float = 0.5) -> int:
    if hours <= 0:
        return 0
    slots = int(hours / slot_hours)
    if slots * slot_hours < hours:
        slots += 1
    return slots


def _night_window_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), time(17, 0), tzinfo=now.tzinfo)


def _night_window_end(window_start: datetime) -> datetime:
    end_date = window_start.date() + timedelta(days=1)
    return datetime.combine(end_date, time(7, 0), tzinfo=window_start.tzinfo)


def _filter_slots_in_range(slots: Iterable[RateSlot], start: datetime, end: datetime) -> List[RateSlot]:
    return sorted([s for s in slots if start <= s.start < end], key=lambda s: s.start)


def _merge_confirmed_over_forecast(
    confirmed: Iterable[RateSlot],
    forecast: Iterable[RateSlot],
) -> List[RateSlot]:
    conf_map: Dict[datetime, RateSlot] = {s.start: s for s in confirmed}
    out: Dict[datetime, RateSlot] = {}
    for s in forecast:
        out[s.start] = s
    for k, v in conf_map.items():
        out[k] = v
    return sorted(out.values(), key=lambda s: s.start)


def _contiguous_block_cheapest(slots: List[RateSlot], needed_slots: int) -> Optional[Tuple[datetime, datetime, float]]:
    if needed_slots <= 0:
        return None
    if len(slots) < needed_slots:
        return None

    best = None  # (total_cost, start_idx)
    for i in range(0, len(slots) - needed_slots + 1):
        ok = True
        total = 0.0
        for j in range(needed_slots):
            if j > 0:
                expected = slots[i + j - 1].start + timedelta(minutes=30)
                if slots[i + j].start != expected:
                    ok = False
                    break
            total += slots[i + j].price_p_per_kwh
        if not ok:
            continue
        if best is None or total < best[0]:
            best = (total, i)

    if best is None:
        return None

    total, i = best
    start = slots[i].start
    end = slots[i + needed_slots - 1].start + timedelta(minutes=30)
    avg = total / needed_slots
    return start, end, avg


def _energy_kwh_from_soc_pct(battery_kwh: float, soc_pct: float) -> float:
    return max(0.0, battery_kwh * (soc_pct / 100.0))


def _required_soc_for_tomorrow(inputs: PlannerInputs) -> float:
    floor = inputs.min_morning_soc_pct + inputs.soc_buffer_pct
    if inputs.full_tomorrow_enabled:
        return inputs.full_tomorrow_target_soc_pct
    return floor


def _projected_soc_tomorrow_morning(inputs: PlannerInputs) -> float:
    return max(0.0, inputs.current_soc_pct - inputs.daily_usage_pct)


def _needed_charge_soc_pct_for_tomorrow(inputs: PlannerInputs) -> float:
    target = _required_soc_for_tomorrow(inputs)
    projected = _projected_soc_tomorrow_morning(inputs)
    return max(0.0, target - projected)


def _needed_energy_kwh(inputs: PlannerInputs, needed_soc_pct: float) -> float:
    return _energy_kwh_from_soc_pct(inputs.battery_capacity_kwh, needed_soc_pct)


def _slots_needed_for_energy(inputs: PlannerInputs, energy_kwh: float) -> int:
    hours = hours = _hours_needed_for_energy(inputs, energy_kwh)
    return _ceil_slots(hours, 0.5)

def _hours_needed_for_energy(inputs: PlannerInputs, energy_kwh: float, efficiency: float = DEFAULT_CHARGING_EFFICIENCY) -> float:
    if inputs.charger_power_kw <= 0:
        return 0.0
    wall_kwh = energy_kwh / efficiency
    return wall_kwh / inputs.charger_power_kw


def _build_night_windows(now: datetime, days: int) -> List[Tuple[datetime, datetime, datetime]]:
    windows: List[Tuple[datetime, datetime, datetime]] = []
    first_start = _night_window_start(now)
    for d in range(days):
        start = first_start + timedelta(days=d)
        end = _night_window_end(start)
        nid = start
        windows.append((nid, start, end))
    return windows


def plan_charging(
    confirmed_rates: List[RateSlot],
    forecast_rates: List[RateSlot],
    inputs: PlannerInputs,
) -> PlannerOutputs:
    if not _is_tz_aware(inputs.now):
        raise ValueError("inputs.now must be timezone-aware")
    if inputs.full_by is not None and not _is_tz_aware(inputs.full_by):
        raise ValueError("inputs.full_by must be timezone-aware")

    needed_soc = _needed_charge_soc_pct_for_tomorrow(inputs)
    needed_kwh = _needed_energy_kwh(inputs, needed_soc)
    needed_slots = _slots_needed_for_energy(inputs, needed_kwh)
    needed_hours = _hours_needed_for_energy(inputs, needed_kwh)

    metrics = ChargeMetrics(
        needed_soc_pct=needed_soc,
        needed_energy_kwh=needed_kwh,
        needed_hours=needed_hours,
        needed_slots=needed_slots,
    )
    horizon_nights = 7
    windows = _build_night_windows(inputs.now, horizon_nights)

    merged = _merge_confirmed_over_forecast(confirmed_rates, forecast_rates)

    if len(merged) == 0:
        tonight = TonightPlan(
            state="NO_DATA",
            start=None, end=None, duration_hours=0.0,
            reason="No rate data available (confirmed or forecast)."
        )
        return PlannerOutputs(
            tonight=tonight,
            next_charge=None,
            deadline=DeadlineStatus(status="DISABLED", summary="Deadline mode disabled."),
            metrics=metrics
        )

    night_slots: Dict[datetime, List[RateSlot]] = {}
    for nid, start, end in windows:
        night_slots[nid] = _filter_slots_in_range(merged, start, end)

    tonight_id, _, _ = windows[0]
    tonight_slots = night_slots.get(tonight_id, [])

    deadline_plan_blocks: Dict[datetime, Tuple[datetime, datetime, int]] = {}

    def _format_deadline_summary(
        blocks: Dict[datetime, Tuple[datetime, datetime, int]],
        full_by: datetime,
        at_risk: bool,
    ) -> str:
        if not blocks:
            return "No charging blocks planned before deadline."
        parts = []
        for nid in sorted(blocks.keys()):
            s, e, slots = blocks[nid]
            hrs = slots * 0.5
            parts.append(f"{hrs:.1f}h on {s.strftime('%a')} {s.strftime('%H:%M')}–{e.strftime('%H:%M')}")
        status_txt = "AT RISK" if at_risk else "ON TRACK"
        return f"{status_txt}: " + ", ".join(parts) + f". Full by {full_by.strftime('%a %H:%M')}."

    def _compute_deadline_plan() -> DeadlineStatus:
        if not inputs.deadline_enabled or inputs.full_by is None:
            return DeadlineStatus(status="DISABLED", summary="Deadline mode disabled.")
        if inputs.deadline_target_soc_pct <= 0:
            return DeadlineStatus(status="DISABLED", summary="Deadline target SoC invalid.")
        if inputs.full_by <= inputs.now:
            return DeadlineStatus(status="AT_RISK", summary="Deadline is in the past or now.")

        eligible_windows = []
        for nid, start, end in windows:
            if start >= inputs.full_by:
                continue
            eligible_windows.append((nid, start, end))
        if not eligible_windows:
            return DeadlineStatus(status="AT_RISK", summary="No eligible nights before deadline.")

        needed_soc = max(0.0, inputs.deadline_target_soc_pct - inputs.current_soc_pct)
        needed_kwh = _needed_energy_kwh(inputs, needed_soc)
        total_needed_slots = _slots_needed_for_energy(inputs, needed_kwh)

        if total_needed_slots == 0:
            return DeadlineStatus(status="ON_TRACK", summary="Already at or above deadline target SoC.")

        night_candidates: List[Tuple[float, datetime, datetime, datetime, int]] = []
        for nid, start, end in eligible_windows:
            slots = night_slots.get(nid, [])
            if not slots:
                continue
            max_slots = min(28, len(slots))
            for s_needed in range(2, max_slots + 1, 2):
                res = _contiguous_block_cheapest(slots, s_needed)
                if res is None:
                    continue
                b_start, b_end, avg = res
                night_candidates.append((avg, nid, b_start, b_end, s_needed))

        if not night_candidates:
            return DeadlineStatus(status="AT_RISK", summary="No rate coverage for eligible nights before deadline.")

        night_candidates.sort(key=lambda x: x[0])

        remaining = total_needed_slots
        used_nights = set()

        for avg, nid, b_start, b_end, s_needed in night_candidates:
            if remaining <= 0:
                break
            if nid in used_nights:
                continue
            take = min(s_needed, remaining)
            slots = night_slots.get(nid, [])
            res = _contiguous_block_cheapest(slots, take)
            if res is None:
                continue
            s2, e2, _ = res
            deadline_plan_blocks[nid] = (s2, e2, take)
            used_nights.add(nid)
            remaining -= take

        if remaining > 0:
            summary = _format_deadline_summary(deadline_plan_blocks, inputs.full_by, at_risk=True)
            return DeadlineStatus(status="AT_RISK", summary=summary)

        summary = _format_deadline_summary(deadline_plan_blocks, inputs.full_by, at_risk=False)
        return DeadlineStatus(status="ON_TRACK", summary=summary)

    deadline_status = _compute_deadline_plan()

    if tonight_id in deadline_plan_blocks:
        s, e, slots = deadline_plan_blocks[tonight_id]
        hrs = slots * 0.5
        tonight = TonightPlan(
            state="PLUG_IN",
            start=s, end=e,
            duration_hours=hrs,
            reason="Deadline mode: charging scheduled tonight as part of full-by plan.",
        )
    else:
        needed_soc = metrics.needed_soc_pct
        needed_kwh = metrics.needed_energy_kwh
        needed_slots = metrics.needed_slots

        if not tonight_slots:
            tonight = TonightPlan(
                state="NO_DATA",
                start=None, end=None,
                duration_hours=0.0,
                reason="No rate coverage in tonight's plug window (17:00–07:00).",
            )
        elif needed_slots > 0:
            res = _contiguous_block_cheapest(tonight_slots, needed_slots)
            if res is None:
                tonight = TonightPlan(
                    state="AT_RISK",
                    start=None, end=None,
                    duration_hours=0.0,
                    reason="Not enough contiguous rate slots tonight to meet required target.",
                )
            else:
                s, e, _avg = res
                tonight = TonightPlan(
                    state="PLUG_IN",
                    start=s, end=e,
                    duration_hours=needed_slots * 0.5,
                    reason="Charge required to meet tomorrow's SoC target.",
                )
        else:
            one_hour_slots = 2
            best_by_night: List[Tuple[float, datetime, datetime, datetime]] = []
            for nid, start, end in windows:
                slots = night_slots.get(nid, [])
                if not slots:
                    continue
                r = _contiguous_block_cheapest(slots, one_hour_slots)
                if r is None:
                    continue
                s, e, avg = r
                best_by_night.append((avg, nid, s, e))

            if not best_by_night:
                tonight = TonightPlan(
                    state="NO_NEED",
                    start=None, end=None,
                    duration_hours=0.0,
                    reason="Above morning floor; no opportunistic data for next 7 nights.",
                )
            else:
                best_by_night.sort(key=lambda x: x[0])
                best_avg, best_nid, best_s, best_e = best_by_night[0]
                if best_nid == tonight_id:
                    tonight = TonightPlan(
                        state="PLUG_IN",
                        start=best_s, end=best_e,
                        duration_hours=1.0,
                        reason="Opportunistic: tonight has the cheapest 1-hour window across next 7 nights.",
                    )
                else:
                    tonight = TonightPlan(
                        state="NO_NEED",
                        start=None, end=None,
                        duration_hours=0.0,
                        reason="Above morning floor; tonight is not the cheapest 1-hour window in next 7 nights.",
                    )

    next_charge = None
    if tonight.state != "PLUG_IN":
        future_nights = [nid for nid in sorted(deadline_plan_blocks.keys()) if nid > tonight_id]
        if future_nights:
            nid = future_nights[0]
            s, e, slots = deadline_plan_blocks[nid]
            next_charge = TonightPlan(
                state="PLUG_IN",
                start=s, end=e,
                duration_hours=slots * 0.5,
                reason="Next scheduled charge from deadline plan.",
            )

    return PlannerOutputs(tonight=tonight, next_charge=next_charge, deadline=deadline_status, metrics=metrics)
