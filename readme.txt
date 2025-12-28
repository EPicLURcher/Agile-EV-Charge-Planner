*** if you find this, please dont use in current version, still in testing***

# EV Charge Planner (Home Assistant)

A vehicle-agnostic, supplier-agnostic Home Assistant integration that decides when to charge your electric vehicle on dynamic half-hourly electricity tariffs, minimising cost while ensuring the vehicle is ready when you need it.

Designed for UK Octopus Agile–style pricing, but works with any supplier that exposes rates in Home Assistant.

Built with some ChatGPT assistance....
---

## Key Features

- Vehicle-agnostic — works with any EV
- Supplier-agnostic — no hard-coded energy providers
- Prefers confirmed rates over forecasts
- Falls back to multi-day forecasts when needed
- Overnight charging constraints (17:00 → 07:00) (will be fixed...)
- Single contiguous charging block per night
- “Full tomorrow” and “Full by date/time” override modes
- Passive design — recalculates only when triggered
- Clean architecture with unit-tested planner logic

---

## Architecture Overview

custom_components/ev_charge_planner/
- planner/          Pure Python planning logic (no Home Assistant imports)
- coordinator.py   Reads HA state and invokes planner
- sensor.py        Exposes planner outputs as sensors
- services.py      Refresh service and confirmed-rate injection
- config_flow.py   UI configuration
- translations/    User-facing text

Confirmed rates always override forecast rates.
Forecasts are used only when confirmed data is unavailable.

---

## Installation

### Option A — HACS (recommended)

1. Add it as a custom repository in HACS (category: Integration)
2. Install and restart Home Assistant

### Option B — Manual

Copy the ev_charge_planner folder into:

/config/custom_components/

Restart Home Assistant.

---

## Configuration

Add the integration via:

Settings → Devices & Services → Add Integration → EV Charge Planner

Create one config entry per vehicle.

---

## Required Helpers & Inputs

You should create the following helpers or equivalent sensors before setup.

### Vehicle Inputs

- Current SoC (%)
  Sensor from your EV integration

- Daily usage (% per day)
  input_number (e.g. 10)

- Battery capacity (kWh)
  input_number (e.g. 75)

---

### Electricity Rates (Supplier-agnostic)

You must provide three entities.

1) Confirmed rates — current day
Usually an event entity with attributes like:
- rates:
  - start
  - value_inc_vat (GBP)

2) Confirmed rates — next day
Same structure as current day, typically published mid-afternoon.

3) Forecast rates (multi-day)
Sensor with attributes like:

prices:
- date_time: 2025-12-28T16:00:00Z
  agile_pred: 35.04

Forecast prices must be in pence per kWh.

The integration does not depend on which supplier provides the data.

---

## Charger Settings

- Charger power (kW), e.g. 7.0
- Charging efficiency is fixed internally at 0.9

---

## Charging Logic

### Normal Mode

- Ensures tomorrow morning SoC is at least:
  min_morning_soc + safety buffer
- If already above the floor, charges 1 hour only if tonight is the cheapest night across the next 7 days

### Full Tomorrow Mode

- Enabled via boolean helper
- Uses a target SoC helper
- Ignores the morning floor

### Full by Date/Time Mode

- Enabled via boolean helper
- Uses a datetime helper and target SoC helper
- Planner may split charging across multiple nights
- Status exposed as ON_TRACK or AT_RISK

---

## Refresh Model (Important)

The integration is currently passive.

Recalculation happens only when you call the service:

ev_charge_planner.refresh

Example automation:

trigger:
- platform: state
  entity_id: event.octopus_energy_electricity_next_day_rates
action:
- service: ev_charge_planner.refresh

You may also trigger on forecast updates or a daily schedule.

---

## Exposed Sensors

For each configured vehicle:

- *_tonight_plan
  PLUG_IN / NO_NEED / NO_DATA

- *_tonight_window
  Charging start → end

- *_tonight_reason
  Human-readable explanation

- *_next_planned_charge
  Next future charging window (if any)

- *_deadline_status
  DISABLED / ON_TRACK / AT_RISK

- *_deadline_summary
  Human-readable deadline plan

Debug slot counts are included as attributes on the tonight_plan sensor.

---

## Known Limitations

- Single charger per vehicle
- Fixed overnight window (17:00 → 07:00)
- No direct charger control (decision-only)

---

## Roadmap / TODO

- Add estimated cost (£) per plan
- Add energy required (kWh) sensor
- Add expected charge after next charging session sensor
- Make charging window configurable
- Add a Lovelace card template
- Add diagnostics panel