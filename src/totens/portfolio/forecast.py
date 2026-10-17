"""Recursive 30-day forecasts evaluated at chronological, frozen origins."""
from __future__ import annotations

import math
import os
from contextlib import contextmanager

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

HORIZON = 30
MODELS = {
    "seasonal_week": None,
    "weekday_84": None,
    "poisson_lr005_leaf15": {"learning_rate": .05, "max_leaf_nodes": 15},
    "poisson_lr010_leaf15": {"learning_rate": .1, "max_leaf_nodes": 15},
    "poisson_lr005_leaf31": {"learning_rate": .05, "max_leaf_nodes": 31},
    "poisson_lr010_leaf31": {"learning_rate": .1, "max_leaf_nodes": 31},
}


@contextmanager
def single_thread_budget():
    """Bound joblib/OpenMP and avoid Windows physical-core subprocess probes."""
    previous = os.environ.get("LOKY_MAX_CPU_COUNT")
    os.environ["LOKY_MAX_CPU_COUNT"] = "1"
    try:
        with threadpool_limits(limits=1):
            yield
    finally:
        if previous is None:
            os.environ.pop("LOKY_MAX_CPU_COUNT", None)
        else:
            os.environ["LOKY_MAX_CPU_COUNT"] = previous


def features(series: pd.Series, calendar: pd.DataFrame | None = None) -> pd.DataFrame:
    """Features at t never contain y(t) or any value after t."""
    result = pd.DataFrame(index=series.index)
    for lag in (1, 7, 14, 28):
        result[f"lag_{lag}"] = series.shift(lag)
    for size in (7, 28):
        result[f"mean_{size}"] = series.shift(1).rolling(size).mean()
    result["weekday"] = series.index.dayofweek
    result["month"] = series.index.month
    result["day_of_year"] = series.index.dayofyear
    if calendar is not None:
        from totens.references import holiday_features
        result = result.join(holiday_features(series.index, calendar))
    return result


def _next_features(history: list[float], date: pd.Timestamp) -> list[float]:
    return [history[-lag] for lag in (1, 7, 14, 28)] + [float(np.mean(history[-size:])) for size in (7, 28)] + [date.dayofweek, date.month, date.dayofyear]


def predict_at(series: pd.Series, cutoff: int, model_name: str, horizon: int = HORIZON,
               calendar: pd.DataFrame | None = None) -> pd.DataFrame:
    """Only the prefix is read, including when the caller supplies future y."""
    if model_name not in MODELS or horizon < 1 or cutoff < 28 or cutoff > len(series):
        raise ValueError("invalid_forecast_request")
    history = series.iloc[:cutoff].astype(float).copy()
    if not np.isfinite(history).all() or (history < 0).any():
        raise ValueError("invalid_training_series")
    if not history.index.equals(pd.date_range(history.index.min(), history.index.max(), freq="D")):
        raise ValueError("noncontiguous_training_series")
    future = pd.date_range(history.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    if model_name == "seasonal_week":
        predictions = np.tile(history.iloc[-7:].to_numpy(), math.ceil(horizon / 7))[:horizon]
    elif model_name == "weekday_84":
        recent = history.iloc[-84:]
        means = recent.groupby(recent.index.dayofweek).mean()
        predictions = np.array([means.loc[d.dayofweek] for d in future])
    elif history.sum() == 0:
        predictions = np.zeros(horizon)
    else:
        x = features(history, calendar).dropna()
        model = HistGradientBoostingRegressor(loss="poisson", max_iter=200,
                    early_stopping=False, random_state=42, **MODELS[model_name])
        with single_thread_budget():
            model.fit(x, history.loc[x.index])
            values, predictions = history.tolist(), []
            for day in future:
                next_values = _next_features(values, day)
                if calendar is not None:
                    from totens.references import holiday_features
                    next_values += holiday_features(pd.DatetimeIndex([day]), calendar).iloc[0].tolist()
                row = pd.DataFrame([next_values], columns=x.columns)
                prediction = max(0., float(model.predict(row)[0]))
                predictions.append(prediction)
                values.append(prediction)
    return pd.DataFrame({"date": future, "predicted": np.asarray(predictions, dtype=float),
                         "horizon": np.arange(1, horizon + 1), "model": model_name,
                         "origin": history.index[-1]})


def forecast_splits(series: pd.Series, horizon: int = HORIZON, validation_blocks: int = 6, test_blocks: int = 3, min_train: int = 365) -> list[dict]:
    if series.empty or series.isna().any() or not np.isfinite(series).all() or (series < 0).any():
        raise ValueError("invalid_forecast_series")
    first = len(series) - (validation_blocks + test_blocks) * horizon
    if first - 28 < min_train:
        raise ValueError("insufficient_forecast_history")
    if not series.index.equals(pd.date_range(series.index.min(), series.index.max(), freq="D")):
        raise ValueError("noncontiguous_forecast_series")
    return [{"split": "validation" if i < validation_blocks else "test",
             "fold": i + 1 if i < validation_blocks else i - validation_blocks + 1,
             "cutoff": first + i * horizon, "horizon": horizon}
            for i in range(validation_blocks + test_blocks)]


def evaluate_predictions(observed, predicted, history: pd.Series) -> dict:
    observed, predicted = np.asarray(observed, float), np.asarray(predicted, float)
    if len(observed) != len(predicted) or not len(observed) or not np.isfinite(observed).all() or not np.isfinite(predicted).all():
        raise ValueError("invalid_forecast_evaluation")
    errors = observed - predicted
    scale = float(np.abs(history.to_numpy()[7:] - history.to_numpy()[:-7]).mean())
    mae = float(np.abs(errors).mean())
    return {"mae": mae, "rmse": float(np.sqrt((errors ** 2).mean())),
            "mase": mae / scale if scale > 0 else None, "mase_scale": scale}


def select_model(scores: pd.DataFrame) -> str:
    validation = scores[scores.split.eq("validation")].groupby("model").mae.mean()
    priority = {name: i for i, name in enumerate(MODELS)}
    return min(validation.index, key=lambda name: (round(float(validation[name]), 12), priority[name]))


def evaluate_forecasts(series: pd.Series, progress=None, *, horizon: int = HORIZON,
                       validation_blocks: int = 6, test_blocks: int = 3,
                       min_train: int = 365) -> tuple[dict, dict[str, pd.DataFrame]]:
    splits = forecast_splits(series, horizon, validation_blocks, test_blocks, min_train)
    predictions, scores = [], []
    selected = None
    for block in splits:
        if block["split"] == "test" and selected is None:
            selected = select_model(pd.DataFrame(scores))
            if progress:
                progress({"event": "forecast_selection_frozen", "model": selected})
        cutoff = block["cutoff"]
        observed = series.iloc[cutoff:cutoff + horizon].to_numpy()
        for model in MODELS:
            forecast = predict_at(series, cutoff, model, horizon)
            forecast["observed"] = observed
            forecast["split"], forecast["fold"] = block["split"], block["fold"]
            forecast["absolute_error"] = np.abs(observed - forecast.predicted)
            predictions.append(forecast)
            scores.append({"model": model, "split": block["split"], "fold": block["fold"],
                           "origin": str(series.index[cutoff - 1].date()),
                           **evaluate_predictions(observed, forecast.predicted, series.iloc[:cutoff])})
        if progress:
            progress({"event": "forecast_block_complete", "split": block["split"], "fold": block["fold"]})
    scores = pd.DataFrame(scores)
    results = pd.concat(predictions, ignore_index=True)
    performance = scores.groupby(["split", "model"], as_index=False)[["mae", "rmse", "mase"]].mean()
    chosen_test = results[results.split.eq("test") & results.model.eq(selected)]
    by_horizon = chosen_test.groupby("horizon", as_index=False).agg(mae=("absolute_error", "mean"), n=("absolute_error", "size"))
    future = predict_at(series, len(series), selected, horizon)
    best_baseline = performance[performance.split.eq("test") & performance.model.isin(["seasonal_week", "weekday_84"])].mae.min()
    selected_mae = float(performance[performance.split.eq("test") & performance.model.eq(selected)].mae.iloc[0])
    summary = {
        "status": "complete", "target": "daily protocols with reviewed source risk level >= 3",
        "horizon_days": horizon, "series_start": str(series.index[0].date()), "series_end": str(series.index[-1].date()),
        "series_days": len(series), "validation_blocks": validation_blocks, "test_blocks": test_blocks, "min_train_days": min_train,
        "first_validation_date": str(series.index[splits[0]["cutoff"]].date()),
        "test_start": str(series.index[splits[validation_blocks]["cutoff"]].date()), "test_end": str(series.index[-1].date()),
        "selected_model": selected, "selection_metric": "mean validation MAE; complexity tie-break",
        "selected_test_mae": selected_mae, "best_baseline_test_mae": float(best_baseline),
        "selection_frozen_before_test": True, "internal_early_stopping": False,
        "forecast_method": "recursive; future lags use earlier predictions only",
        "historical_projection_start": str(future.date.min().date()), "historical_projection_end": str(future.date.max().date()),
        "prediction_intervals": "not estimated; former +/-1.96 standard deviations are not reused",
        "availability_limitation": "Retrospective consolidated snapshot; risk-label availability at historical origins is not proven.",
        "models": MODELS,
    }
    return summary, {"forecast_predictions": results, "forecast_scores": scores, "forecast_performance": performance,
                     "forecast_error_by_horizon": by_horizon, "historical_projection": future}


def evaluate_calendar_validation(series: pd.Series, calendar: pd.DataFrame,
                                 *, horizon=30, validation_blocks=6, test_blocks=3, min_train=365):
    """Separate exploratory experiment; never evaluate/tune on the examined test."""
    rows, predictions = [], []
    for split in forecast_splits(series, horizon, validation_blocks, test_blocks, min_train):
        if split["split"] != "validation":
            continue
        cutoff = split["cutoff"]
        for model in MODELS:
            for variant in ("original", "calendar") if MODELS[model] else ("original",):
                forecast = predict_at(series, cutoff, model, horizon, calendar if variant == "calendar" else None)
                observed = series.iloc[cutoff:cutoff + horizon].to_numpy()
                forecast["observed"], forecast["variant"], forecast["fold"] = observed, variant, split["fold"]
                predictions.append(forecast)
                rows.append({"model": model, "variant": variant, "fold": split["fold"],
                             "split": "validation", "origin": str(series.index[cutoff-1].date()),
                             **evaluate_predictions(observed, forecast.predicted, series.iloc[:cutoff])})
    return pd.DataFrame(rows), pd.concat(predictions, ignore_index=True)
