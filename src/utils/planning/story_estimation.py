from typing import Any, Dict, Tuple


T_SHIRT_SIZE_RANGES: Dict[str, Dict[str, float]] = {
    "XS": {"min": 2.0, "max": 4.0},
    "S": {"min": 4.0, "max": 8.0},
    "M": {"min": 8.0, "max": 16.0},
    "L": {"min": 16.0, "max": 32.0},
    "XL": {"min": 32.0, "max": 60.0},
}


def normalize_tshirt_size(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in T_SHIRT_SIZE_RANGES else ""


def parse_effort_hours(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric >= 0 else 0.0


def get_tshirt_size_bounds(size: Any) -> Tuple[float, float]:
    normalized = normalize_tshirt_size(size)
    if not normalized:
        return 0.0, 0.0
    config = T_SHIRT_SIZE_RANGES[normalized]
    return config["min"], config["max"]


def infer_tshirt_size_from_effort(value: Any) -> str:
    hours = parse_effort_hours(value)
    if hours <= 0:
        return ""
    for size, config in T_SHIRT_SIZE_RANGES.items():
        if config["min"] <= hours <= config["max"]:
            return size
    return "XS" if hours < T_SHIRT_SIZE_RANGES["XS"]["min"] else "XL"


def get_default_effort_for_size(size: Any) -> float:
    minimum, maximum = get_tshirt_size_bounds(size)
    if minimum <= 0 or maximum <= 0:
        return 0.0
    return round((minimum + maximum) / 2, 1)


def resolve_story_estimation(
    size_value: Any,
    effort_value: Any,
) -> Tuple[str, float]:
    hours = parse_effort_hours(effort_value)
    size = normalize_tshirt_size(size_value)

    if not size:
        inferred_size = infer_tshirt_size_from_effort(hours)
        return inferred_size, hours

    minimum, maximum = get_tshirt_size_bounds(size)
    if hours <= 0:
        hours = get_default_effort_for_size(size)
    else:
        hours = min(max(hours, minimum), maximum)

    return size, round(hours, 1)
