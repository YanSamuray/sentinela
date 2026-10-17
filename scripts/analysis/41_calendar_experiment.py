"""Evaluate calendar features only on validation; preserve the original test."""
import argparse
import json
from importlib.metadata import version
from pathlib import Path

import pandas as pd

from totens.local_migration import publish_new, sign_document
from totens.acervo import digest
from totens.local_secrets import load_local_key
from totens.portfolio.case import load_case
from totens.portfolio.data import continuous_model_series
from totens.portfolio.forecast import evaluate_calendar_validation
from totens.references import authenticated_reference


def validated_series(daily, forecast):
    series = continuous_model_series(daily)
    if (str(series.index.min().date()) != forecast["series_start"]
            or str(series.index.max().date()) != forecast["series_end"]):
        raise ValueError("experiment_series_differs_from_original")
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reference-version", default="cadastros_v1")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    summary = load_case(args.run, project)
    baseline_hashes = {str(args.run / name): digest(args.run / name) for name in ("meta.json", "daily.csv", "summary.json")}
    key = load_local_key(project / ".secrets/local_minimized_v1.dpapi")
    calendar, inputs = authenticated_reference(project / "data", args.reference_version, "calendario.parquet", key)
    daily = pd.read_csv(args.run / "daily.csv", parse_dates=["date"])
    forecast = summary["forecast"]
    series = validated_series(daily, forecast)
    destination = project / "data/experiments" / (args.run.name + "_calendar")
    destination.mkdir(parents=True, exist_ok=False)
    parameters = summary.get("effective_parameters", {}).get("forecast", {})
    scores, predictions = evaluate_calendar_validation(series, calendar, **parameters)
    scores.to_csv(destination / "scores.csv", index=False)
    predictions.to_csv(destination / "predictions.csv", index=False)
    comparison = scores.groupby(["model", "variant"], as_index=False)[["mae", "rmse", "mase"]].mean().sort_values("mae")
    comparison.to_csv(destination / "comparison.csv", index=False)
    report = {"status": "complete", "evaluation": "exploratory_validation_only",
              "test_reexamined": False, "original_run": args.run.name, "reference_inputs": inputs,
              "baseline_inputs": baseline_hashes,
              "versions": {name: version(name) for name in ("holidays", "pandas", "scikit-learn")},
              "features": "national/state holiday, eve, following day; no municipal/optional",
              "parameters": parameters, "results": comparison.to_dict("records")}
    (destination / "report.md").write_text(
        "# Experimento de calendário\n\nComparação exploratória exclusivamente nos seis blocos de validação. "
        "O teste final original não foi reavaliado. Calendário municipal e pontos facultativos não entram nas features.\n\n"
        + "```text\n" + comparison.to_string(index=False) + "\n```\n", encoding="utf-8")
    for path, expected in {**inputs, **baseline_hashes}.items():
        if digest(Path(path)) != expected:
            raise ValueError("calendar_experiment_input_changed")
    report["artifacts"] = {p.name: digest(p) for p in destination.iterdir() if p.is_file()}
    publish_new(destination / "summary.json", sign_document(report, key))
    print(json.dumps({"status": "complete", "evaluation": report["evaluation"], "results": report["results"]}), flush=True)


if __name__ == "__main__":
    main()
