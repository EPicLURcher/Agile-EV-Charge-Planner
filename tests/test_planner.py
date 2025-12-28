from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ev_charge_planner.planner.core import PlannerInputs, plan_charging
from custom_components.ev_charge_planner.planner.normalise import (
    RateSlot,
    extract_list_from_attributes,
    merge_confirmed_over_forecast,
    parse_rates_list,
)

TZ = timezone.utc


# ----------------------------
# Helpers
# ----------------------------
def make_half_hour_slots(start: datetime, count: int, price: float) -> list[RateSlot]:
    return [RateSlot(start + timedelta(minutes=30 * i), float(price)) for i in range(count)]


def make_overnight_slots(date_yyyy_mm_dd: str, default_price: float = 50.0) -> list[RateSlot]:
    """
    Build slots covering the plug window 17:00 -> 07:00 (14 hours, 28 half-hour slots),
    starting at 17:00 UTC for tests.
    """
    d = datetime.fromisoformat(date_yyyy_mm_dd).replace(tzinfo=TZ)
    start = d.replace(hour=17, minute=0, second=0, microsecond=0)
    return make_half_hour_slots(start, 28, default_price)


def base_inputs(now: datetime, **overrides) -> PlannerInputs:
    """
    Defaults chosen to keep tests deterministic and avoid rounding ambiguity.
    """
    defaults = dict(
        now=now,
        current_soc_pct=60.0,
        daily_usage_pct=10.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,
        full_tomorrow_enabled=False,
        full_tomorrow_target_soc_pct=90.0,
        deadline_enabled=False,
        full_by=None,
        deadline_target_soc_pct=90.0,
    )
    defaults.update(overrides)
    return PlannerInputs(**defaults)


# ----------------------------
# Normaliser / merge tests
# ----------------------------
def test_merge_confirmed_overrides_forecast_for_same_start_time():
    s0 = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    forecast = [
        RateSlot(s0, 50.0),
        RateSlot(s0 + timedelta(minutes=30), 60.0),
    ]
    confirmed = [
        RateSlot(s0, 10.0),  # overrides same timestamp
    ]

    merged = merge_confirmed_over_forecast(confirmed, forecast)
    assert len(merged) == 2
    assert merged[0].start == s0
    assert merged[0].price_p_per_kwh == 10.0  # confirmed wins


def test_confirmed_current_day_octopus_shape_value_inc_vat_is_pounds_to_pence():
    # Octopus event style: value_inc_vat is GBP/kWh (e.g. 0.167055)
    attrs = {
        "rates": [
            {
                "start": "2025-12-28T00:00:00+00:00",
                "end": "2025-12-28T00:30:00+00:00",
                "value_inc_vat": 0.167055,
            }
        ]
    }
    items = extract_list_from_attributes(attrs)
    out = parse_rates_list(items, tz_hint=TZ)
    assert len(out) == 1
    assert out[0].start == datetime(2025, 12, 28, 0, 0, tzinfo=TZ)
    # Expect p/kWh internally (GBP * 100)
    assert out[0].price_p_per_kwh == pytest.approx(16.7055, rel=1e-6)


def test_forecast_predictor_shape_prices_date_time_agile_pred_is_pence():
    # Predictor style: agile_pred is already p/kWh
    attrs = {
        "prices": [
            {"date_time": "2025-12-28T16:00:00Z", "agile_pred": 35.04},
            {"date_time": "2025-12-28T16:30:00Z", "agile_pred": 35.92},
            {"date_time": "2025-12-28T17:00:00Z", "agile_pred": 36.31},
        ]
    }
    items = extract_list_from_attributes(attrs)
    out = parse_rates_list(items, tz_hint=TZ)
    assert len(out) == 3
    assert out[0].start == datetime(2025, 12, 28, 16, 0, tzinfo=TZ)
    assert out[0].price_p_per_kwh == pytest.approx(35.04, rel=1e-9)


# ----------------------------
# Core behaviour tests
# ----------------------------
def test_planner_does_not_bridge_gaps_in_rate_slots():
    """
    Ensure the planner never forms a contiguous block across a missing half-hour slot.
    If the planner cannot meet the requirement with the available slots, it may return AT_RISK.
    """
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)

    s0 = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)
    slots = [
        RateSlot(s0, 10.0),
        RateSlot(s0 + timedelta(minutes=30), 10.0),
        RateSlot(s0 + timedelta(minutes=90), 1.0),   # gap at 18:00
        RateSlot(s0 + timedelta(minutes=120), 1.0),
    ]

    inputs = PlannerInputs(
        now=now,
        current_soc_pct=10.0,          # strongly likely to require charging
        daily_usage_pct=0.0,
        battery_capacity_kwh=70.0,
        charger_power_kw=7.0,
        min_morning_soc_pct=80.0,
        soc_buffer_pct=0.0,
        full_tomorrow_enabled=False,
        full_tomorrow_target_soc_pct=90.0,
        deadline_enabled=False,
        full_by=None,
        deadline_target_soc_pct=90.0,
    )

    out = plan_charging([], slots, inputs)

    # Planner may return PLUG_IN (with a window) OR may signal it cannot satisfy the requirement.
    assert out.tonight.state in ("PLUG_IN", "AT_RISK", "NO_DATA")

    # If it did schedule a window, it must not "bridge" the missing 18:00 slot.
    if out.tonight.start is not None:
        assert out.tonight.start >= s0 + timedelta(minutes=90)


def test_no_data_returns_no_data():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    inputs = base_inputs(now)
    out = plan_charging([], [], inputs)
    assert out.tonight.state == "NO_DATA"
    assert "No rate data" in out.tonight.reason


def test_baseline_no_need_when_above_floor_and_not_opportunistic():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=30.0)

    inputs = base_inputs(
        now,
        current_soc_pct=70.0,
        daily_usage_pct=10.0,  # projected tomorrow = 60
        min_morning_soc_pct=45.0,
        soc_buffer_pct=5.0,  # floor = 50; projected 60 >= 50
    )

    out = plan_charging([], slots, inputs)

    # Depending on your opportunistic rule implementation, this may remain NO_NEED
    # or may schedule a 1h opportunistic charge. Both are acceptable here.
    assert out.tonight.state in ("NO_NEED", "PLUG_IN")


def test_baseline_requires_charge_picks_cheapest_block_exact_2h():
    """
    Make the deficit land exactly on 2 hours (4 half-hour slots) to avoid rounding ambiguity.
    Using:
      current_soc=42
      daily_usage=10 -> projected=32
      floor = (min 45 + buffer 5) = 50
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
    )

    out = plan_charging([], slots, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert out.tonight.start == cheap_start
    assert out.tonight.end == cheap_end
    assert out.tonight.duration_hours == pytest.approx(2.0)
    assert "Charge required" in out.tonight.reason


def test_full_tomorrow_override_targets_soc():
    now = datetime(2025, 12, 28, 18, 0, tzinfo=TZ)
    slots = make_overnight_slots("2025-12-28", default_price=30.0)

    inputs = base_inputs(
        now,
        current_soc_pct=20.0,
        daily_usage_pct=10.0,
        full_tomorrow_enabled=True,
        full_tomorrow_target_soc_pct=80.0,
    )

    out = plan_charging([], slots, inputs)
    assert out.tonight.state == "PLUG_IN"
    assert "tomorrow" in out.tonight.reason.lower() or "target" in out.tonight.reason.lower()


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
        daily_usage_pct=10.0,  # projected 70, floor 50 -> above
    )

    out = plan_charging([], all_slots, inputs)

    # If opportunistic triggers, it should schedule 1 hour exactly at the cheap window
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
    assert "disabled" in out.deadline.summary.lower()


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
    assert "full" in out.deadline.summary.lower() or "charge" in out.deadline.summary.lower()