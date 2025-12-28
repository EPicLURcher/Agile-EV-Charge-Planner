from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Dict, List


@dataclass(frozen=True)
class RateSlot:
    """
    Normalised electricity price slot.

    - start must be timezone-aware
    - price_p_per_kwh is ALWAYS p/kWh
    """
    start: datetime
    price_p_per_kwh: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_dt(val: Any) -> Optional[datetime]:
    """
    Accept:
    - datetime objects
    - ISO strings (with Z or +00:00)

    Returns tz-aware datetime or None.
    """
    if isinstance(val, datetime):
        return val

    if isinstance(val, str):
        try:
            # Handle Z suffix as UTC
            s = val.replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    return None


def normalise_price_to_p_per_kwh(price: float) -> float:
    """
    Normalise to p/kWh.

    - £/kWh (e.g. 0.167055) → p/kWh
    - p/kWh (e.g. 35.04) → unchanged
    - Supports negative pricing
    """
    if -1.0 < price < 1.0:
        return price * 100.0
    return price


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_list_from_attributes(attrs: dict) -> list[dict]:
    """
    Locate the list payload inside Home Assistant-style attributes.

    Supports:
      - rates   (Octopus confirmed events)
      - prices  (forecast / predictors)
      - data / slots / items (generic)
    """
    for key in ("rates", "prices", "data", "slots", "items"):
        v = attrs.get(key)
        if isinstance(v, list):
            return v
    return []


def parse_rates_list(items: list[dict], tz_hint=None) -> list[RateSlot]:
    """
    Convert a list of dicts into RateSlot objects.

    Supported datetime keys:
      - start
      - date_time
      - datetime
      - from

    Supported price keys:
      - price_p_per_kwh
      - p_per_kwh
      - agile_pred        (forecast)
      - price
      - value
      - value_inc_vat     (Octopus confirmed, £/kWh)

    Notes:
    - Accepts datetime objects OR ISO strings
    - Datetimes must be tz-aware after parsing
    """
    out: list[RateSlot] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        # --- datetime ---
        raw_dt = (
            item.get("start")
            or item.get("date_time")
            or item.get("datetime")
            or item.get("from")
        )

        dt = _parse_dt(raw_dt)
        if dt is None:
            continue

        if dt.tzinfo is None and tz_hint is not None:
            dt = dt.replace(tzinfo=tz_hint)

        # Refuse naive datetimes (safety)
        if dt.tzinfo is None:
            continue

        # --- price ---
        raw_price = (
            item.get("price_p_per_kwh")
            or item.get("p_per_kwh")
            or item.get("agile_pred")
            or item.get("price")
            or item.get("value")
            or item.get("value_inc_vat")
        )

        price = _safe_float(raw_price)
        if price is None:
            continue

        price = normalise_price_to_p_per_kwh(price)

        out.append(
            RateSlot(
                start=dt,
                price_p_per_kwh=float(price),
            )
        )

    return sorted(out, key=lambda r: r.start)


def merge_confirmed_over_forecast(
    confirmed: Iterable[RateSlot],
    forecast: Iterable[RateSlot],
) -> list[RateSlot]:
    """
    Merge two timelines where confirmed prices override forecast prices
    for matching timestamps.
    """
    out: Dict[datetime, RateSlot] = {r.start: r for r in forecast}
    for r in confirmed:
        out[r.start] = r
    return sorted(out.values(), key=lambda r: r.start)
