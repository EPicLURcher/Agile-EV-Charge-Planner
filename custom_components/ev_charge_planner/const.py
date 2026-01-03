DOMAIN = "ev_charge_planner"

PLATFORMS = ["sensor", "switch", "number", "datetime"]

SERVICE_REFRESH = "refresh"
ATTR_RATES = "rates"

# Option keys
OPT_CHARGER_POWER_KW = "charger_power_kw"
OPT_MIN_MORNING_SOC = "min_morning_soc"
OPT_SOC_BUFFER = "soc_buffer"
OPT_BATTERY_KWH = "battery_kwh"

# Dynamic (entity-backed) option keys
OPT_DAILY_USAGE_PCT = "daily_usage_pct"
OPT_FULL_TOMORROW_ENABLED = "full_tomorrow_enabled"
OPT_FULL_TOMORROW_TARGET = "full_tomorrow_target_soc"
OPT_DEADLINE_ENABLED = "deadline_enabled"
OPT_FULL_BY = "full_by"  # ISO string
OPT_DEADLINE_TARGET = "deadline_target_soc"

DEFAULT_CHARGING_EFFICIENCY = 0.90

DEFAULTS = {
    OPT_CHARGER_POWER_KW: 7.0,
    OPT_MIN_MORNING_SOC: 40.0,
    OPT_SOC_BUFFER: 5.0,
    OPT_BATTERY_KWH: 75.0,
    OPT_DAILY_USAGE_PCT: 10.0,
    OPT_FULL_TOMORROW_ENABLED: False,
    OPT_FULL_TOMORROW_TARGET: 90.0,
    OPT_DEADLINE_ENABLED: False,
    OPT_FULL_BY: None,
    OPT_DEADLINE_TARGET: 90.0,
}