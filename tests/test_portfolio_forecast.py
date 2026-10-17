import numpy as np
import pandas as pd
import pytest

from totens.portfolio.forecast import MODELS, evaluate_predictions, features, forecast_splits, predict_at, select_model


def series(days=800):
    index = pd.date_range("2021-01-01", periods=days)
    return pd.Series(10 + index.dayofweek.to_numpy() + np.arange(days) % 3, index=index, dtype=float)


@pytest.mark.parametrize("model", list(MODELS))
def test_mutating_future_cannot_change_forecast_or_training_features(model):
    original = series()
    changed = original.copy()
    changed.iloc[600:] = 50000
    pd.testing.assert_frame_equal(features(original).iloc[:600], features(changed).iloc[:600])
    pd.testing.assert_frame_equal(predict_at(original, 600, model), predict_at(changed, 600, model))


def test_features_shift_rolling_means_before_computation():
    target = series(60)
    x = features(target)
    assert x.iloc[30].mean_7 == target.iloc[23:30].mean()
    assert x.iloc[30].lag_1 == target.iloc[29]
    changed = target.copy()
    changed.iloc[30] = 9000
    pd.testing.assert_series_equal(x.iloc[30], features(changed).iloc[30])


def test_chronological_blocks_have_full_test_and_minimum_training():
    target = series()
    splits = forecast_splits(target)
    assert len(splits) == 9 and splits[0]["cutoff"] - 28 >= 365
    assert [b["split"] for b in splits] == ["validation"] * 6 + ["test"] * 3
    assert splits[-1]["cutoff"] + 30 == len(target)
    assert splits[6]["cutoff"] == len(target) - 90


def test_test_scores_cannot_select_the_model():
    scores = pd.DataFrame([
        {"split": "validation", "model": "seasonal_week", "mae": 2},
        {"split": "validation", "model": "weekday_84", "mae": 3},
        {"split": "test", "model": "seasonal_week", "mae": 10000},
        {"split": "test", "model": "weekday_84", "mae": 0},
    ])
    assert select_model(scores) == "seasonal_week"


def test_tie_uses_simple_baseline_and_mase_zero_scale_is_undefined():
    scores = pd.DataFrame([{"split": "validation", "model": m, "mae": 2} for m in MODELS])
    assert select_model(scores) == "seasonal_week"
    metrics = evaluate_predictions([0, 0], [1, 1], pd.Series(np.zeros(30)))
    assert metrics["mae"] == 1 and metrics["mase"] is None


def test_missing_days_or_insufficient_history_cannot_be_silently_filled():
    with pytest.raises(ValueError, match="insufficient_forecast_history"):
        forecast_splits(series(500))
    broken = series()
    broken.iloc[10] = np.nan
    with pytest.raises(ValueError, match="invalid_forecast_series"):
        forecast_splits(broken)
