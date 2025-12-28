
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ev_charge_planner.planner.core import (
    RateSlot,
    PlannerInputs,
    plan_charging,
    _merge_confirmed_over_forecast,
    _contiguous_block_cheapest,
)

TZ = timezone.utc


def make_night_slots(date_yyyy_mm_dd: str, default_price: float = 30.0):
    # Build full 17:00 -> 07:00 (28 half-hour slots) for the given date (night start date).
    y, m, d = map(int, date_yyyy_mm_dd.split("-"))
    start = datetime(y, m, d, 17, 0, tzinfo=TZ)
    out = []
    for i in range(28):
        out.append(RateSlot(start=start + timedelta(minutes=30 * i), price_p_per_kwh=default_price))
    return out


def base_inputs(now: datetime, **kw):
    defaults = dict(
        now=now,
        current_soc_pct=50.0,
        daily_usage_pct=10.0,
        battery_capacity_kwh=75.0,
        charger_power_kw=7.0,
        min_morning_soc_pct=40.0,
        soc_buffer_pct=5.0,
        full_tomorrow_enabled=False,
        full_tomorrow_target_soc_pct=90.0,
        deadline_enabled=False,
        full_by=None,
        deadline_target_soc_pct=90.0,
    )
    defaults.update(kw)
    return PlannerInputs(**defaults)


def test_merge_confirmed_overrides_forecast():
    dt = datetime(2025, 12, 28, 23, 0, tzinfo=TZ)
    forecast = [RateSlot(dt, 50.0)]
    confirmed = [RateSlot(dt, 10.0)]
    merged = _merge_confirmed_over_forecast(confirmed, forecast)
    assert merged[0].price_p_per_kwh == 10.0


def test_contiguous_block_cheapest_skips_gaps():
    s0 = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    slots = [
        RateSlot(s0, 10.0),
        RateSlot(s0 + timedelta(minutes=30), 10.0),
        RateSlot(s0 + timedelta(minutes=90), 1.0),  # gap
        RateSlot(s0 + timedelta(minutes=120), 1.0),
    ]
    res = _contiguous_block_cheapest(slots, 2)
    assert res is not None
    start, end, avg = res

    # Cheapest contiguous block is the later one (18:30–19:30)
    assert start == s0 + timedelta(minutes=90)
    assert end == s0 + timedelta(minutes=150)
    assert avg == 1.0


def test_no_data_returns_no_data():
    now = datetime(2025, 12, 28, 16, 0, tzinfo=TZ)
    out = plan_charging([], [], base_inputs(now))
    assert out.tonight.state == "NO_DATA"
    assert out.deadline.status == "DISABLED"


def test_baseline_no_need_when_above_floor_and_not_opportunistic():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    tonight = make_night_slots("2025-12-28", default_price=20.0)
    tomorrow = make_night_slots("2025-12-29", default_price=10.0)
    rates = tonight + tomorrow

    inputs = base_inputs(now, current_soc_pct=60.0, daily_usage_pct=5.0)
    out = plan_charging([], rates, inputs)
    assert out.tonight.state == "NO_NEED"


def test_baseline_requires_charge_picks_cheapest_block():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_night_slots("2025-12-28", default_price=50.0)

    # Make 23:00-01:00 cheap
    cheap_start = datetime(2025, 12, 28, 23, 0, tzinfo=TZ)
    cheap_end = datetime(2025, 12, 29, 1, 0, tzinfo=TZ)
    slots = [RateSlot(s.start, 5.0 if cheap_start <= s.start < cheap_end else s.price_p_per_kwh) for s in slots]

    inputs = base_inputs(now, current_soc_pct=40.0, daily_usage_pct=10.0)
    out = plan_charging([], slots, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.start == cheap_start
    assert out.tonight.duration_hours == 2.0
    assert out.tonight.end == cheap_end


def test_full_tomorrow_override_targets_soc():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_night_slots("2025-12-28", default_price=20.0)

    inputs = base_inputs(
        now,
        current_soc_pct=50.0,
        full_tomorrow_enabled=True,
        full_tomorrow_target_soc_pct=90.0,
        daily_usage_pct=0.0,
    )
    out = plan_charging([], slots, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.duration_hours == 5.0


def test_opportunistic_charges_one_hour_if_tonight_cheapest_across_week():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    tonight = make_night_slots("2025-12-28", default_price=30.0)
    very_cheap_start = datetime(2025, 12, 29, 2, 0, tzinfo=TZ)
    very_cheap_end = datetime(2025, 12, 29, 3, 0, tzinfo=TZ)
    tonight = [RateSlot(s.start, 1.0 if very_cheap_start <= s.start < very_cheap_end else s.price_p_per_kwh) for s in tonight]

    future = make_night_slots("2025-12-29", default_price=10.0)
    rates = tonight + future

    inputs = base_inputs(now, current_soc_pct=80.0, daily_usage_pct=10.0)
    out = plan_charging([], rates, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.duration_hours == 1.0


def test_confirmed_preferred_over_forecast_in_planning():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    base = make_night_slots("2025-12-28", default_price=50.0)

    forecast = [RateSlot(s.start, 1.0 if s.start == datetime(2025, 12, 28, 23, 0, tzinfo=TZ) else s.price_p_per_kwh) for s in base]
    confirmed = [
        RateSlot(datetime(2025, 12, 28, 23, 0, tzinfo=TZ), 99.0),
        RateSlot(datetime(2025, 12, 29, 1, 0, tzinfo=TZ), 1.0),
    ]

    inputs = base_inputs(now, current_soc_pct=30.0, daily_usage_pct=10.0)
    out = plan_charging(confirmed, forecast, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.start != datetime(2025, 12, 28, 23, 0, tzinfo=TZ)


def test_deadline_disabled_ignored():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_night_slots("2025-12-28", default_price=20.0)
    inputs = base_inputs(now, deadline_enabled=False, full_by=None)
    out = plan_charging([], slots, inputs)
    assert out.deadline.status == "DISABLED"


def test_deadline_on_track_can_schedule_and_may_set_next_charge():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    n1 = make_night_slots("2025-12-28", default_price=20.0)
    n2 = make_night_slots("2025-12-29", default_price=5.0)
    rates = n1 + n2

    inputs = base_inputs(
        now,
        current_soc_pct=50.0,
        deadline_enabled=True,
        full_by=datetime(2025, 12, 30, 6, 0, tzinfo=TZ),
        deadline_target_soc_pct=60.0,
    )
    out = plan_charging([], rates, inputs)
    assert out.deadline.status == "ON_TRACK"
    if out.tonight.state != "PLUG_IN":
        assert out.next_charge is not None
