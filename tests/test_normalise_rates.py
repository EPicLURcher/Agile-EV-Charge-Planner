from __future__ import annotations

from datetime import datetime, timezone, timedelta

from custom_components.ev_charge_planner.planner.normalise import (
    extract_list_from_attributes,
    parse_rates_list,
    normalise_price_to_p_per_kwh,
    merge_confirmed_over_forecast,
    RateSlot,
)

TZ = timezone.utc


def test_extract_list_from_attributes_prefers_known_keys():
    attrs = {"foo": 1, "rates": [{"a": 1}]}
    assert extract_list_from_attributes(attrs) == [{"a": 1}]


def test_normalise_price_converts_pounds_to_pence():
    assert normalise_price_to_p_per_kwh(0.167055) == 16.7055
    assert normalise_price_to_p_per_kwh(-0.05) == -5.0


def test_parse_octopus_event_rates_value_inc_vat_pounds_per_kwh():
    # Matches your sample: attrs.rates with start/end and value_inc_vat
    items = [
        {
            "start": datetime(2025, 12, 29, 0, 0, tzinfo=TZ),
            "end": datetime(2025, 12, 29, 0, 30, tzinfo=TZ),
            "value_inc_vat": 0.167055,
            "is_capped": False,
        },
        {
            "start": datetime(2025, 12, 29, 0, 30, tzinfo=TZ),
            "end": datetime(2025, 12, 29, 1, 0, tzinfo=TZ),
            "value_inc_vat": 0.1764,
            "is_capped": False,
        },
    ]

    out = parse_rates_list(items)
    assert len(out) == 2
    assert out[0].start == datetime(2025, 12, 29, 0, 0, tzinfo=TZ)
    # £/kWh -> p/kWh
    assert abs(out[0].price_p_per_kwh - 16.7055) < 1e-6
    assert abs(out[1].price_p_per_kwh - 17.64) < 1e-6


def test_parse_forecast_prices_in_pence_passthrough():
    items = [
        {"start": datetime(2025, 12, 29, 0, 0, tzinfo=TZ), "price": 34.68},
        {"start": datetime(2025, 12, 29, 0, 30, tzinfo=TZ), "price": 35.04},
    ]
    out = parse_rates_list(items)
    assert out[0].price_p_per_kwh == 34.68
    assert out[1].price_p_per_kwh == 35.04


def test_merge_confirmed_overrides_forecast_timestamp_collision():
    t0 = datetime(2025, 12, 29, 0, 0, tzinfo=TZ)
    forecast = [
        RateSlot(start=t0, price_p_per_kwh=30.0),
        RateSlot(start=t0 + timedelta(minutes=30), price_p_per_kwh=31.0),
    ]
    confirmed = [
        RateSlot(start=t0, price_p_per_kwh=10.0),
    ]
    merged = merge_confirmed_over_forecast(confirmed, forecast)
    assert len(merged) == 2
    assert merged[0].start == t0
    assert merged[0].price_p_per_kwh == 10.0  # confirmed wins
    assert merged[1].price_p_per_kwh == 31.0
