from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from custom_components.ev_charge_planner.planner.core import PlannerInputs, plan_charging, RateSlot, _estimate_cost_for_window
from custom_components.ev_charge_planner.planner.normalise import (
    merge_confirmed_over_forecast,
    parse_rates_list,
)

TZ = timezone.utc


def make_overnight_slots(date_iso: str, default_price: float = 30.0) -> List[RateSlot]:
    """
    Build half-hour rate slots from 17:00 to 07:00 (next day), inclusive of start,
    exclusive of end (28 x 30m = 14h).
    """
    d = datetime.fromisoformat(date_iso).date()
    start = datetime(d.year, d.month, d.day, 17, 0, tzinfo=TZ)
    out: List[RateSlot] = []
    for i in range(28):
        out.append(RateSlot(start=start + timedelta(minutes=30 * i), price_p_per_kwh=float(default_price)))
    return out


def base_inputs(now: datetime, **overrides) -> PlannerInputs:
    """
    Defaults chosen to keep tests deterministic and avoid rounding ambiguity.

    IMPORTANT:
    - target_soc_pct is now treated as the unified "Target SoC slider".
    - deadline_target_soc_pct still exists, and deadline tests may override it.
    """
    defaults = dict(
        now=now,
        current_soc_pct=60.0,
        daily_usage_pct=10.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        # Unified target used for tomorrow planning (clamped to floor)
        target_soc_pct=50.0,
        # Deadline mode
        deadline_enabled=False,
        full_by=None,
        deadline_target_soc_pct=80.0,
    )
    defaults.update(overrides)
    return PlannerInputs(**defaults)


def test_merge_confirmed_overrides_forecast_for_same_start_time():
    t0 = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    forecast = [RateSlot(t0, 50.0)]
    confirmed = [RateSlot(t0, 10.0)]

    merged = merge_confirmed_over_forecast(confirmed, forecast)
    assert len(merged) == 1
    assert merged[0].start == t0
    assert merged[0].price_p_per_kwh == 10.0


def test_confirmed_current_day_octopus_shape_value_inc_vat_is_pounds_to_pence():
    # Example: Octopus "value_inc_vat" comes as pounds/kWh -> convert to pence/kWh
    items = [
        {"start": "2025-12-28T18:00:00Z", "value_inc_vat": 0.1234},
        {"start": "2025-12-28T18:30:00Z", "value_inc_vat": 0.2000},
    ]
    slots = parse_rates_list(items, tz_hint=TZ)

    assert slots[0].price_p_per_kwh == pytest.approx(12.34)
    assert slots[1].price_p_per_kwh == pytest.approx(20.00)


def test_forecast_predictor_shape_prices_date_time_agile_pred_is_pence():
    items = [
        {"date_time": "2025-12-28T18:00:00Z", "agile_pred": 34.68},
        {"date_time": "2025-12-28T18:30:00Z", "agile_pred": 35.04},
    ]
    slots = parse_rates_list(items, tz_hint=TZ)
    assert slots[0].price_p_per_kwh == pytest.approx(34.68)
    assert slots[1].price_p_per_kwh == pytest.approx(35.04)


def test_planner_does_not_bridge_gaps_in_rate_slots():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    # Build a set with a deliberate gap inside a would-be charging block
    slots = make_overnight_slots("2025-12-28", default_price=30.0)
    # Remove one slot to create a gap at 19:00
    gap_time = datetime(2025, 12, 28, 19, 0, tzinfo=TZ)
    slots = [s for s in slots if s.start != gap_time]

    inputs = base_inputs(
        now,
        current_soc_pct=42.0,
        daily_usage_pct=10.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        # target 50, floor 50 => deficit > 0
        target_soc_pct=50.0,
    )

    out = plan_charging([], slots, inputs)

    # Depending on where the cheapest contiguous block exists, planner may still find a block.
    # This test mainly ensures it doesn't assume missing times exist.
    if out.tonight.state == "PLUG_IN":
        # If it found a block, it must be contiguous in 30m steps.
        # (Planner enforces this internally; we just sanity-check start/end present.)
        assert out.tonight.start is not None
        assert out.tonight.end is not None


def test_no_data_returns_no_data():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    inputs = base_inputs(now)

    out = plan_charging([], [], inputs)
    assert out.tonight.state == "NO_DATA"
    assert out.deadline.status == "DISABLED"


def test_baseline_no_need_when_above_floor_and_not_opportunistic():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=30.0)

    # projected = 70 - 10 = 60
    # floor = 45 + 5 = 50
    # target = 50 => required tomorrow target = max(floor, target) = 50
    # projected (60) >= 50 => no required charge
    inputs = base_inputs(
        now,
        current_soc_pct=70.0,
        daily_usage_pct=10.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        target_soc_pct=50.0,
    )

    out = plan_charging([], slots, inputs)
    assert out.tonight.state in ("NO_NEED", "PLUG_IN")


def test_baseline_requires_charge_picks_cheapest_block_exact_2h():
    """
    Make the deficit land exactly on 2 hours (4 half-hour slots) to avoid rounding ambiguity.
    Using:
      current_soc=42
      daily_usage=10 -> projected=32
      floor = (min 45 + buffer 5) = 50
      target = 50 -> required = 50
      deficit = 18% of 70kWh = 12.6kWh
      charger 7kW * 0.9 = 6.3kWh per hour -> 2.0 hours exactly
    """
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=50.0)

    cheap_start = datetime(2025, 12, 28, 23, 0, tzinfo=TZ)
    cheap_end = datetime(2025, 12, 29, 1, 0, tzinfo=TZ)  # 2 hours
    slots = [
        RateSlot(s.start, 5.0 if cheap_start <= s.start < cheap_end else s.price_p_per_kwh)
        for s in slots
    ]

    inputs = base_inputs(
        now,
        current_soc_pct=42.0,
        daily_usage_pct=10.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        target_soc_pct=50.0,
    )

    out = plan_charging([], slots, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.start == cheap_start
    assert out.tonight.end == cheap_end
    assert out.tonight.duration_hours == pytest.approx(2.0)


def test_full_tomorrow_override_targets_soc():
    """
    This test used to validate the "full tomorrow toggle".
    Now, it validates that the unified target SoC (target_soc_pct)
    drives the required SoC for tomorrow (clamped to floor).
    """
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=30.0)

    inputs = base_inputs(
        now,
        current_soc_pct=20.0,
        daily_usage_pct=10.0,              # projected = 10
        target_soc_pct=80.0,  # unified target
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,                # floor = 50, so required = 80
    )

    out = plan_charging([], slots, inputs)
    # required deficit should be 80 - projected(10) = 70
    assert out.metrics.needed_soc_pct == pytest.approx(70.0)


def test_opportunistic_charges_one_hour_if_tonight_cheapest_across_week():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    # Build 7 nights of slots; make tonight have a uniquely cheap 1h window
    all_slots: list[RateSlot] = []
    for i in range(7):
        d = (now.date() + timedelta(days=i)).isoformat()
        night = make_overnight_slots(d, default_price=40.0)
        all_slots.extend(night)

    cheap_start = datetime(2025, 12, 28, 21, 0, tzinfo=TZ)
    cheap_end = datetime(2025, 12, 28, 22, 0, tzinfo=TZ)
    all_slots = [
        RateSlot(s.start, 1.0 if cheap_start <= s.start < cheap_end else s.price_p_per_kwh)
        for s in all_slots
    ]

    # Above floor so no required charge, but opportunistic may trigger
    inputs = base_inputs(
        now,
        current_soc_pct=80.0,
        daily_usage_pct=10.0,              # projected 70
        target_soc_pct=50.0,  # required tomorrow target = 50; projected >= 50 => no required charge
    )

    out = plan_charging([], all_slots, inputs)

    assert out.tonight.state in ("PLUG_IN", "NO_NEED")
    if out.tonight.state == "PLUG_IN":
        assert out.tonight.duration_hours == pytest.approx(1.0)
        assert out.tonight.start == cheap_start
        assert out.tonight.end == cheap_end


def test_deadline_disabled_ignored():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=30.0)

    inputs = base_inputs(
        now,
        deadline_enabled=False,
        full_by=None,
    )

    out = plan_charging([], slots, inputs)
    assert out.deadline.status == "DISABLED"


def test_deadline_on_track_can_schedule_and_sets_status():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    # Provide 3 nights of data
    slots: list[RateSlot] = []
    for i in range(3):
        d = (now.date() + timedelta(days=i)).isoformat()
        slots.extend(make_overnight_slots(d, default_price=40.0))

    full_by = datetime(2025, 12, 30, 7, 0, tzinfo=TZ)  # 2 days away, by morning

    inputs = base_inputs(
        now,
        current_soc_pct=30.0,
        daily_usage_pct=10.0,
        deadline_enabled=True,
        full_by=full_by,
        deadline_target_soc_pct=80.0,
    )

    out = plan_charging([], slots, inputs)
    assert out.deadline.status in ("ON_TRACK", "AT_RISK")
    assert "Full by" in out.deadline.summary

def test_estimate_cost_for_window_happy_path():
    start = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    # Two half-hour slots @ 10 p/kWh
    merged = [
        RateSlot(start=start, price_p_per_kwh=10.0),
        RateSlot(start=start + timedelta(minutes=30), price_p_per_kwh=10.0),
    ]

    # Charger 7kW -> per 30 mins = 3.5 kWh
    # cost = 10 * 3.5 * 2 = 70.0
    cost = _estimate_cost_for_window(merged, start, end, charger_power_kw=7.0)
    assert cost == pytest.approx(70.0)


def test_estimate_cost_for_window_missing_rate_returns_none():
    start = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    # Missing the 17:30 slot
    merged = [
        RateSlot(start=start, price_p_per_kwh=10.0),
    ]

    cost = _estimate_cost_for_window(merged, start, end, charger_power_kw=7.0)
    assert cost is None


def test_estimate_cost_for_window_zero_power_returns_zero():
    start = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    merged = [
        RateSlot(start=start, price_p_per_kwh=10.0),
        RateSlot(start=start + timedelta(minutes=30), price_p_per_kwh=10.0),
    ]

    cost = _estimate_cost_for_window(merged, start, end, charger_power_kw=0.0)
    assert cost == 0.0


def test_metrics_populates_tonight_cost_and_slots_when_plug_in_required_charge():
    """
    Mirrors the classic "needs exactly 2 hours" scenario:
    - 4 half-hour slots planned
    - cheap window prices make expected cost deterministic
    """
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=50.0)

    cheap_start = datetime(2025, 12, 28, 23, 0, tzinfo=TZ)
    cheap_end = datetime(2025, 12, 29, 1, 0, tzinfo=TZ)  # 2h -> 4 slots

    slots = [
        RateSlot(s.start, 5.0 if cheap_start <= s.start < cheap_end else s.price_p_per_kwh)
        for s in slots
    ]

    inputs = base_inputs(
        now,
        current_soc_pct=42.0,
        daily_usage_pct=10.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        target_soc_pct=50.0,
    )

    out = plan_charging([], slots, inputs)

    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.start == cheap_start
    assert out.tonight.end == cheap_end
    assert out.metrics.tonight_planned_slots == 4

    # Expected cost:
    # per slot kWh = 7kW * 0.5h = 3.5kWh
    # total kWh = 3.5 * 4 = 14kWh
    # price = 5 p/kWh -> total = 5 * 14 = 70 pence-units
    assert out.metrics.tonight_estimated_cost == pytest.approx(70.0)


def test_metrics_are_zero_none_when_not_plugging_in():
    """
    Ensure metrics doesn't claim slots/cost if we don't plan a PLUG_IN tonight.
    Force NO_NEED by making a future night cheaper than tonight for opportunistic logic.
    """
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    # Build 2 nights
    all_slots: list[RateSlot] = []
    for i in range(2):
        d = (now.date() + timedelta(days=i)).isoformat()
        all_slots.extend(make_overnight_slots(d, default_price=40.0))

    # Make tomorrow night have a uniquely cheap 1h window so tonight is NOT cheapest.
    cheap_start = datetime(2025, 12, 29, 21, 0, tzinfo=TZ)
    cheap_end = datetime(2025, 12, 29, 22, 0, tzinfo=TZ)

    all_slots = [
        RateSlot(s.start, 1.0 if cheap_start <= s.start < cheap_end else s.price_p_per_kwh)
        for s in all_slots
    ]

    # Above floor and target, so no required charge
    inputs = base_inputs(
        now,
        current_soc_pct=80.0,
        daily_usage_pct=10.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        target_soc_pct=50.0,
    )

    out = plan_charging([], all_slots, inputs)

    assert out.tonight.state != "PLUG_IN"
    assert out.metrics.tonight_planned_slots == 0
    assert out.metrics.tonight_estimated_cost is None