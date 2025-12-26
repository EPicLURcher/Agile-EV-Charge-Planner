DOMAIN = "ev_charge_planner"

SERVICE_REFRESH = "refresh"

PLATFORMS = ["sensor"]

CONF_NAME = "name"
CONF_SOC_ENTITY = "soc_entity"
CONF_FORECAST_ENTITY = "forecast_entity"

# Helpers (your existing HA entities)
HELPER_DAILY_USE = "input_number.ev_daily_soc_use"
HELPER_MIN_MORNING = "input_number.ev_min_morning_min_soc"
HELPER_BUFFER = "input_number.ev_soc_buffer"
HELPER_BATT_KWH = "input_number.ev_battery_kwh"
HELPER_CHARGER_KW = "input_number.ev_charger_kw"
HELPER_OVERRIDE = "input_boolean.ev_need_full_tomorrow"
HELPER_FULL_TARGET = "input_number.sbiddy_car_target_charge_limit"

# Defaults
DEFAULT_SOC_ENTITY = "sensor.sbiddy_car_battery"
DEFAULT_FORECAST_ENTITY = "sensor.agile_predict_7d"

# Forecast schema
FORECAST_ATTR = "prices"
FORECAST_TIME_KEY = "date_time"
FORECAST_PRICE_KEY = "agile_pred"