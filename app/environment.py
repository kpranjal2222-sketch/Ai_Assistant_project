"""Environmental calculations and scenario assumptions."""

import math

_CPCB_PM_BREAKPOINTS = {
    "pm25": (
        (0, 30, 0, 50),
        (31, 60, 51, 100),
        (61, 90, 101, 200),
        (91, 120, 201, 300),
        (121, 250, 301, 400),
    ),
    "pm10": (
        (0, 50, 0, 50),
        (51, 100, 51, 100),
        (101, 250, 101, 200),
        (251, 350, 201, 300),
        (351, 430, 301, 400),
    ),
}

_CPCB_AQI_CATEGORIES = (
    (50, "Good"),
    (100, "Satisfactory"),
    (200, "Moderate"),
    (300, "Poor"),
    (400, "Very Poor"),
    (500, "Severe"),
)


def cpcb_pm_subindex(concentration: float, pollutant: str) -> int:
    """Calculate a CPCB PM2.5 or PM10 sub-index for a rounded demo value."""
    if pollutant not in _CPCB_PM_BREAKPOINTS:
        raise ValueError(f"Unsupported CPCB particulate pollutant: {pollutant}")
    if not math.isfinite(concentration) or concentration < 0:
        raise ValueError("Particulate concentration must be a finite, non-negative value.")

    value = int(concentration + 0.5)
    breakpoints = _CPCB_PM_BREAKPOINTS[pollutant]
    for concentration_low, concentration_high, index_low, index_high in breakpoints:
        if concentration_low <= value <= concentration_high:
            fraction = (value - concentration_low) / (concentration_high - concentration_low)
            return int(index_low + fraction * (index_high - index_low) + 0.5)
    return 500


def cpcb_pm_index(pm25: float, pm10: float) -> dict:
    """Return a PM-only index using CPCB breakpoints, not an official CPCB AQI."""
    subindices = {
        "pm25": cpcb_pm_subindex(pm25, "pm25"),
        "pm10": cpcb_pm_subindex(pm10, "pm10"),
    }
    index = max(subindices.values())
    category = next(
        label for maximum, label in _CPCB_AQI_CATEGORIES if index <= maximum
    )
    return {
        "index": index,
        "category": category,
        "subindices": subindices,
        "scope": "SIMULATED PM-ONLY DEMO — NOT OFFICIAL CPCB AQI",
    }


_SITE_ACTIVITY_ADDITIONS = {
    "light": (4.0, 10.0),
    "typical": (12.0, 35.0),
    "dust_intensive": (30.0, 90.0),
}

_DUST_CONTROL_EFFICIENCY = {
    "none": 0.0,
    "basic": 0.35,
    "strong": 0.60,
}


def validate_site_scenario_options(activity: str, mitigation: str) -> None:
    if activity not in _SITE_ACTIVITY_ADDITIONS:
        raise ValueError("Choose a supported construction activity.")
    if mitigation not in _DUST_CONTROL_EFFICIENCY:
        raise ValueError("Choose a supported dust-control level.")


def construction_site_scenario(
    ambient_pm25: float,
    ambient_pm10: float,
    activity: str,
    mitigation: str,
) -> dict:
    """Build a configurable what-if estimate from ambient model values.

    Added concentrations and control efficiencies are project-defined scenario
    assumptions. They are not emissions factors or a validated exposure model.
    """
    for value in (ambient_pm25, ambient_pm10):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Particulate concentrations must be finite and non-negative.")
    validate_site_scenario_options(activity, mitigation)

    activity_additions = _SITE_ACTIVITY_ADDITIONS[activity]
    efficiency = _DUST_CONTROL_EFFICIENCY[mitigation]
    pm25 = round(ambient_pm25 + activity_additions[0] * (1 - efficiency), 1)
    pm10 = round(ambient_pm10 + activity_additions[1] * (1 - efficiency), 1)
    index = cpcb_pm_index(pm25, pm10)
    return {
        "pm25": pm25,
        "pm10": pm10,
        "index": index["index"],
        "category": index["category"],
        "activity": activity,
        "mitigation": mitigation,
        "scope": "WHAT-IF SCENARIO — NOT MEASURED OR A VALIDATED AQI",
    }
