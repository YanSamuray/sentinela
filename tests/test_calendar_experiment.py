import importlib.util
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest


def test_experiment_reuses_exported_target_and_requires_original_window():
    script = Path(__file__).resolve().parents[1] / "scripts/analysis/41_calendar_experiment.py"
    spec = importlib.util.spec_from_file_location("calendar_experiment_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dates = pd.date_range("2021-01-01", periods=800)
    daily = pd.DataFrame({"date": dates, "y": 10 + dates.dayofweek,
                          "complete_month": True, "coverage_declared": True})
    daily = pd.read_csv(StringIO(daily.to_csv(index=False)), parse_dates=["date"])
    forecast = {"series_start": "2021-01-01", "series_end": str(dates[-1].date())}
    series = module.validated_series(daily, forecast)
    assert series.tolist() == daily.y.tolist()
    with pytest.raises(ValueError, match="differs_from_original"):
        module.validated_series(daily, {**forecast, "series_start": "2021-01-02"})
