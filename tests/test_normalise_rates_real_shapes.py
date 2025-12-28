from __future__ import annotations

from datetime import datetime, timezone

from custom_components.ev_charge_planner.planner.normalise import (
    extract_list_from_attributes,
    parse_rates_list,
    merge_confirmed_over_forecast,
    RateSlot,
)

TZ = timezone.utc


def test_confirmed_current_day_octopus_shape_value_inc_vat_is_pounds():
    attrs = {
        "event_type": "octopus_energy_electricity_current_day_rates",
        "rates": [
            {
                "start": datetime(2025, 12, 28, 0, 0, tzinfo=TZ),
                "end": datetime(2025, 12, 28, 0, 30, tzinfo=TZ),
                "value_inc_vat": 0.16233,
                "is_capped": False,
            },
            {
                "start": datetime(2025, 12, 28, 0, 30, tzinfo=TZ),
                "end": datetime(2025, 12, 28, 1, 0, tzinfo=TZ),
                "value_inc_vat": 0.16653,
                "is_capped": False,
            },
        ],
    }

    items = extract_list_from_attributes(attrs)
    out = parse_rates_list(items)

    assert len(out) == 2
    assert out[0].start == datetime(2025, 12, 28, 0, 0, tzinfo=TZ)
    # £/kWh -> p/kWh
    assert abs(out[0].price_p_per_kwh - 16.233) < 1e-6
    assert abs(out[1].price_p_per_kwh - 16.653) < 1e-6


def test_forecast_predictor_shape_prices_date_time_agile_pred_is_pence():
    attrs = {
        "prices": [
            {"date_time": datetime(2025, 12, 28, 16, 0, tzinfo=TZ), "agile_pred": 35.04},
            {"date_time": datetime(2025, 12, 28, 16, 30, tzinfo=TZ), "agile_pred": 35.92},
            {"date_time": datetime(2025, 12, 28, 17, 0, tzinfo=TZ), "agile_pred": 36.31},
        ]
    }

    items = extract_list_from_attributes(attrs)
    out = parse_rates_list(items)

    assert len(out) == 3
    assert out[0].price_p_per_kwh == 35.04
    assert out[1].price_p_per_kwh == 35.92
    assert out[2].price_p_per_kwh == 36.31


def test_merge_confirmed_overrides_forecast_for_same_start_time():
    t0 = datetime(2025, 12, 28, 17, 0, tzinfo=TZ)

    forecast = [
        RateSlot(start=t0, price_p_per_kwh=36.31),
        RateSlot(start=datetime(2025, 12, 28, 17, 30, tzinfo=TZ), price_p_per_kwh=36.50),
    ]

    confirmed = [
        RateSlot(start=t0, price_p_per_kwh=16.233),  # confirmed wins at 17:00
    ]

    merged = merge_confirmed_over_forecast(confirmed, forecast)

    assert len(merged) == 2
    assert merged[0].start == t0
    assert merged[0].price_p_per_kwh == 16.233
    assert merged[1].price_p_per_kwh == 36.50
