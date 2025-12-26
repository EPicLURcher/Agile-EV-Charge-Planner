from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal

PlanState = Literal["PLUG_IN", "NO_NEED", "NO_DATA", "ERROR"]


@dataclass(frozen=True)
class Slot:
    start: datetime          # tz-aware
    p_per_kwh: float         # p/kWh
    is_confirmed: bool = False


@dataclass(frozen=True)
class PlannerConfig:
    plug_start_hour: int = 17
    plug_end_hour: int = 7
    charger_kw: float = 7.2
    efficiency: float = 0.9


@dataclass(frozen=True)
class UserInputs:
    soc_now: float
    daily_soc_use: float
    min_morning_soc: float
    soc_buffer: float
    battery_kwh: float
    need_full_tomorrow: bool
    full_target_soc: float


@dataclass(frozen=True)
class PlanTonight:
    state: PlanState
    start: Optional[datetime]
    end: Optional[datetime]
    hours: float
    reason: str