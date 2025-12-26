from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest

from src.models import PlannerConfig, UserInputs, Slot
from src.planner import plan_tonight


# ----------------------------
# Helpers
# ----------------------------

def make_halfhour_slots(start: datetime, count: int, price: float) -> list[Slot]:
    """Create contiguous 30-minute slots."""
    return [
        Slot(start=start + timedelta(minutes=30 * i), p_per_kwh=price)
        for i in range(count)
    ]


def make_night(day_offset: int, prices: list[float]) -> list[Slot]:
    """Create a night starting at 17:00 for a given day offset."""
    base_date = datetime(2025, 12, 26, 17, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return [
        Slot(start=base_date + timedelta(minutes=30 * i), p_per_kwh=prices[i])
        for i in range(len(prices))
    ]


# ----------------------------
# Fixtures
# ----------------------------

@pytest.fixture
def now() -> datetime:
    return datetime(2025, 12, 26, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg() -> PlannerConfig:
    return PlannerConfig(
        plug_start_hour=17,
        plug_end_hour=7,
        charger_kw=7.2,
        efficiency=0.9,
    )


# ----------------------------
# Tests
# ----------------------------

def test_no_data_when_forecast_empty(now, cfg):
    inp = UserInputs(
        soc_now=50,
        daily_soc_use=5,
        min_morning_soc=35,
        soc_buffer=3,
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    out = plan_tonight(now, cfg, inp, [])
    assert out.state == "NO_DATA"
    assert out.hours == 0.0


def test_no_need_when_above_floor(now, cfg):
    inp = UserInputs(
        soc_now=60,
        daily_soc_use=5,     # morning = 55
        min_morning_soc=35,
        soc_buffer=3,        # floor = 38
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    # Tonight: flat 10p (not cheapest of week)
    tonight = make_halfhour_slots(
        datetime(2025, 12, 26, 17, 0, tzinfo=timezone.utc),
        28,
        10.0,
    )

    # Tomorrow night: cheaper 1-hour block exists => opportunistic should NOT trigger
    tomorrow = make_halfhour_slots(
        datetime(2025, 12, 27, 17, 0, tzinfo=timezone.utc),
        28,
        1.0,
    )

    out = plan_tonight(now, cfg, inp, tonight + tomorrow)

    assert out.state == "NO_NEED"
    assert out.hours == 0.0


def test_plug_in_when_below_floor(now, cfg):
    inp = UserInputs(
        soc_now=35,
        daily_soc_use=5,     # morning = 30
        min_morning_soc=35,
        soc_buffer=3,        # floor = 38
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    slots = make_halfhour_slots(
        datetime(2025, 12, 26, 17, 0, tzinfo=timezone.utc),
        28,
        12.0,
    )

    out = plan_tonight(now, cfg, inp, slots)
    assert out.state == "PLUG_IN"
    assert out.start is not None
    assert out.end is not None
    assert out.hours > 0


def test_full_override_forces_charge(now, cfg):
    inp = UserInputs(
        soc_now=60,
        daily_soc_use=5,
        min_morning_soc=35,
        soc_buffer=3,
        battery_kwh=75,
        need_full_tomorrow=True,
        full_target_soc=80,
    )

    slots = make_halfhour_slots(
        datetime(2025, 12, 26, 17, 0, tzinfo=timezone.utc),
        28,
        15.0,
    )

    out = plan_tonight(now, cfg, inp, slots)
    assert out.state == "PLUG_IN"
    assert "override" in out.reason.lower()


def test_opportunistic_when_tonight_cheapest(now, cfg):
    inp = UserInputs(
        soc_now=70,
        daily_soc_use=5,
        min_morning_soc=35,
        soc_buffer=3,
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    slots = []
    slots += make_night(0, [1.0, 1.0] + [20.0] * 26)  # tonight cheapest
    for d in range(1, 7):
        slots += make_night(d, [5.0, 5.0] + [20.0] * 26)

    out = plan_tonight(now, cfg, inp, slots)
    assert out.state == "PLUG_IN"
    assert out.hours == 1.0


def test_no_opportunistic_if_future_cheaper(now, cfg):
    inp = UserInputs(
        soc_now=70,
        daily_soc_use=5,
        min_morning_soc=35,
        soc_buffer=3,
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    slots = []
    slots += make_night(0, [5.0, 5.0] + [20.0] * 26)
    slots += make_night(1, [1.0, 1.0] + [20.0] * 26)

    out = plan_tonight(now, cfg, inp, slots)
    assert out.state == "NO_NEED"


def test_ignores_slots_outside_window(now, cfg):
    inp = UserInputs(
        soc_now=20,
        daily_soc_use=5,
        min_morning_soc=35,
        soc_buffer=3,
        battery_kwh=75,
        need_full_tomorrow=False,
        full_target_soc=80,
    )

    outside = make_halfhour_slots(
        datetime(2025, 12, 26, 10, 0, tzinfo=timezone.utc),
        4,
        0.1,
    )
    inside = make_halfhour_slots(
        datetime(2025, 12, 26, 17, 0, tzinfo=timezone.utc),
        28,
        50.0,
    )

    slots = outside + inside

    out = plan_tonight(now, cfg, inp, slots)

    # Must never choose outside-window slots
    assert out.state in ("PLUG_IN", "NO_DATA")
    if out.start:
        assert out.start.hour >= 17 or out.start.hour < 7