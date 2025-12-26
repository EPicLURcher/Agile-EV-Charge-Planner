from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Iterable, List, Tuple, Optional

from .models import Slot, PlannerConfig, UserInputs, PlanTonight

HALF_HOUR = timedelta(minutes=30)


def _night_window(now: datetime, cfg: PlannerConfig, day_offset: int) -> Tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    d = now.date() + timedelta(days=day_offset)
    ws = datetime.combine(d, time(cfg.plug_start_hour, 0), tzinfo=now.tzinfo)
    we = datetime.combine(d + timedelta(days=1), time(cfg.plug_end_hour, 0), tzinfo=now.tzinfo)
    return ws, we


def _contiguous_best(slots: List[Slot], ws: datetime, we: datetime, n: int) -> Optional[Tuple[datetime, datetime, float]]:
    cand = [s for s in slots if ws <= s.start < we]
    cand.sort(key=lambda s: s.start)
    if len(cand) < n:
        return None

    best: Optional[Tuple[datetime, datetime, float]] = None
    for i in range(0, len(cand) - n + 1):
        window = cand[i:i + n]
        ok = all(window[j].start == window[0].start + j * HALF_HOUR for j in range(n))
        if not ok:
            continue
        s = sum(x.p_per_kwh for x in window)
        if best is None or s < best[2]:
            best = (window[0].start, window[0].start + n * HALF_HOUR, s)
    return best


def plan_tonight(
    now: datetime,
    cfg: PlannerConfig,
    inp: UserInputs,
    forecast_slots: Iterable[Slot],
) -> PlanTonight:
    slots = list(forecast_slots)
    if not slots:
        return PlanTonight("NO_DATA", None, None, 0.0, "Waiting for pricing data…")

    floor = inp.min_morning_soc + inp.soc_buffer
    target = inp.full_target_soc if inp.need_full_tomorrow else floor

    soc_morning_nocharge = inp.soc_now - inp.daily_soc_use
    needed_soc = max(0.0, target - soc_morning_nocharge)

    kwh_needed = (needed_soc / 100.0) * inp.battery_kwh
    kwh_per_slot = cfg.charger_kw * 0.5 * cfg.efficiency
    slots_needed = int((kwh_needed / kwh_per_slot) + 0.999999) if kwh_per_slot > 0 else 0

    ws0, we0 = _night_window(now, cfg, 0)

    # Opportunistic 1h if tonight is cheapest 1h across next 7 nights (and no required charge)
    tonight_best_1h = _contiguous_best(slots, ws0, we0, 2)

    best_week = None  # (day, start, end, sum)
    for d in range(0, 7):
        ws, we = _night_window(now, cfg, d)
        b = _contiguous_best(slots, ws, we, 2)  # (start,end,sum)
        if b and (best_week is None or b[2] < best_week[3]):
            best_week = (d, b[0], b[1], b[2])

    is_cheapest_week = bool(
        tonight_best_1h and best_week and best_week[0] == 0 and abs(best_week[3] - tonight_best_1h[2]) < 1e-9
    )

    if slots_needed == 0 and is_cheapest_week:
        slots_needed = 2  # 1 hour opportunistic

    if slots_needed == 0:
        reason = "Full tomorrow override ON but target already met" if inp.need_full_tomorrow else "Above minimum floor for tomorrow morning"
        return PlanTonight("NO_NEED", None, None, 0.0, reason)

    best = _contiguous_best(slots, ws0, we0, slots_needed)
    if not best:
        return PlanTonight("NO_DATA", None, None, 0.0, "Not enough contiguous slots in 17:00–07:00 window")

    start, end, _ = best
    hours = slots_needed * 0.5

    if inp.need_full_tomorrow:
        reason = f"Full tomorrow override: charge to {target:.0f}%"
    else:
        reason = f"Charge to wake above {floor:.0f}%"

    return PlanTonight("PLUG_IN", start, end, hours, reason)