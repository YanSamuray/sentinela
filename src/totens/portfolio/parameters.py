"""Validated parameters consumed by the case, with explicit defaults."""
from copy import deepcopy
import math

DEFAULTS = {
    "spatial": {"eps": 150, "min_samples": 50, "grid_size": 1000,
                "eps_values": [100, 150, 300], "min_values": [25, 50, 100], "radii": [150, 300, 500]},
    "forecast": {"horizon": 30, "validation_blocks": 6, "test_blocks": 3, "min_train": 365},
}


def case_parameters(settings):
    values = deepcopy(DEFAULTS)
    for section, defaults in values.items():
        given = settings.get("portfolio", {}).get(section, {})
        if set(given) - set(defaults):
            raise ValueError("unknown_case_parameter")
        defaults.update(given)
        for name, value in defaults.items():
            elements = value if isinstance(value, list) else [value]
            if not elements or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in elements):
                raise ValueError("invalid_case_parameter")
            if name not in ("eps", "eps_values", "radii") and any(int(v) != v for v in elements):
                raise ValueError("integer_case_parameter_required")
            if name not in ("eps", "eps_values", "radii"):
                defaults[name] = [int(v) for v in elements] if isinstance(value, list) else int(value)
    spatial = values["spatial"]
    if spatial["eps"] not in spatial["eps_values"] or spatial["min_samples"] not in spatial["min_values"]:
        raise ValueError("default_cluster_missing_from_sensitivity")
    return values
