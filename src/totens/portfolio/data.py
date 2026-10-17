"""Consensus by protocol, explicit exclusions and a local-time daily target."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from totens.local_migration import json_bytes

TIMEZONE = "America/Porto_Velho"
RISK_LEVELS = {
    "BAIXO": 1, "MEDIO": 2, "ALTO": 3, "ALTISSIMO": 4,
    "MUITO ALTO": 4, "CRITICO": 4, "GRAVISSIMO": 4,
}


def normalize_label(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value)
                            if not unicodedata.combining(c)).upper().split())


def source_risk(value, reviewed: set[str]) -> int | None:
    """An exact reviewed source label, never a substring or a derived flag."""
    if not isinstance(value, str) or value not in reviewed:
        return None
    return RISK_LEVELS.get(normalize_label(value))


def project_records(records: list[dict], source: str, reviewed: set[str]) -> pd.DataFrame:
    rows = []
    for record in records:
        location = record.get("localizacao") or {}
        raw_date = record.get("dataCriacao")
        # A naive timestamp cannot be assigned an undocumented source timezone.
        aware = isinstance(raw_date, str) and bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})$", raw_date))
        rows.append({
            "protocolo": record["protocolo"], "date_raw": raw_date if aware else None,
            "risk_level": source_risk(record.get("nivelRisco"), reviewed),
            "latitude": location.get("latitude"), "longitude": location.get("longitude"),
            "record_hash": hashlib.sha256(json_bytes(record)).hexdigest(), "source_file": source,
        })
    frame = pd.DataFrame(rows, columns=["protocolo", "date_raw", "risk_level", "latitude", "longitude", "record_hash", "source_file"])
    frame["timestamp_utc"] = pd.to_datetime(frame.pop("date_raw"), format="mixed", utc=True, errors="coerce")
    for column in ("latitude", "longitude"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    frame["risk_level"] = pd.to_numeric(frame["risk_level"], errors="coerce").astype("Int8")
    return frame


def consolidate(source: pd.DataFrame | list[Path]) -> tuple[pd.DataFrame, dict]:
    """One protocol, with field consensus; no version selected by file order."""
    with duckdb.connect() as connection:
        connection.execute("SET threads=2")
        if isinstance(source, pd.DataFrame):
            connection.register("records", source)
        else:
            connection.read_parquet([str(p) for p in source]).create_view("records")
        cohort = connection.sql("""
            SELECT protocolo, count(*) AS source_rows,
                count(DISTINCT record_hash) AS record_variants,
                count(DISTINCT source_file) AS source_files,
                count(DISTINCT timestamp_utc) + max(CASE WHEN timestamp_utc IS NULL THEN 1 ELSE 0 END) AS date_variants,
                min(timestamp_utc) AS timestamp_utc,
                count(DISTINCT risk_level) + max(CASE WHEN risk_level IS NULL THEN 1 ELSE 0 END) AS risk_variants,
                min(risk_level) AS risk_level,
                count(DISTINCT latitude) + max(CASE WHEN latitude IS NULL THEN 1 ELSE 0 END) AS latitude_variants,
                count(DISTINCT longitude) + max(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END) AS longitude_variants,
                min(latitude) AS latitude, min(longitude) AS longitude
            FROM records GROUP BY protocolo ORDER BY protocolo
        """).df()
    cohort["timestamp_utc"] = pd.to_datetime(cohort["timestamp_utc"], utc=True)
    cohort["date_valid"] = cohort.date_variants.eq(1) & cohort.timestamp_utc.notna()
    cohort.loc[~cohort.date_valid, "timestamp_utc"] = pd.NaT
    cohort["risk_valid"] = cohort.risk_variants.eq(1) & cohort.risk_level.notna()
    cohort.loc[~cohort.risk_valid, "risk_level"] = pd.NA
    cohort["risk_level"] = cohort.risk_level.astype("Int8")
    cohort["temporal_eligible"] = cohort.date_valid & cohort.risk_valid
    cohort["high_risk"] = (cohort.risk_level.ge(3) & cohort.temporal_eligible).fillna(False).astype(bool)
    cohort["coordinates_agree"] = cohort.latitude_variants.eq(1) & cohort.longitude_variants.eq(1)
    numeric = (np.isfinite(cohort.latitude) & np.isfinite(cohort.longitude)
               & cohort.latitude.abs().le(90) & cohort.longitude.abs().le(180))
    cohort["coordinates_valid"] = cohort.coordinates_agree & numeric
    cohort.loc[~cohort.coordinates_valid, ["latitude", "longitude"]] = np.nan
    cohort["local_date"] = cohort.timestamp_utc.dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.normalize()
    reason = np.select([
        ~cohort.date_valid, cohort.risk_variants.gt(1), ~cohort.risk_valid, cohort.high_risk,
    ], ["date_invalid_or_conflicting", "risk_conflicting", "risk_unknown", "known_high"], default="known_other")
    cohort["classification"] = reason
    repeated = cohort.source_rows.gt(1)
    identical = repeated & cohort.record_variants.eq(1)
    summary = {
        "input_rows": int(cohort.source_rows.sum()), "distinct_protocols": len(cohort),
        "excess_rows": int((cohort.source_rows - 1).sum()),
        "repeated_protocols": int(repeated.sum()),
        "identical_repeated_protocols": int(identical.sum()),
        "identical_excess_rows": int((cohort.loc[identical, "source_rows"] - 1).sum()),
        "different_repeated_protocols": int((repeated & ~identical).sum()),
        "different_excess_rows": int((cohort.loc[repeated & ~identical, "source_rows"] - 1).sum()),
        "partition": {str(k): int(v) for k, v in cohort.classification.value_counts().items()},
        "coordinate_conflicting_protocols": int((~cohort.coordinates_agree).sum()),
        "temporally_eligible": int(cohort.temporal_eligible.sum()),
        "high_risk_protocols": int(cohort.high_risk.sum()),
        "high_risk_valid_coordinate_protocols": int((cohort.high_risk & cohort.coordinates_valid).sum()),
        "duplicate_policy": "consensus_per_field; no most-recent-file assumption",
        "risk_definition": "exact reviewed source label; level >= 3",
        "timezone": TIMEZONE,
    }
    assert sum(summary["partition"].values()) == len(cohort)
    assert summary["identical_excess_rows"] + summary["different_excess_rows"] == summary["excess_rows"]
    return cohort, summary


def window_days(names: list[str]) -> set[date]:
    days = set()
    for name in names:
        match = re.fullmatch(r"ocorrencias?_(\d{8})_a_(\d{8})\.json", name)
        if not match:
            raise ValueError("invalid_source_window_name")
        start, end = (pd.Timestamp(x).date() for x in match.groups())
        if end < start:
            raise ValueError("invalid_source_window_range")
        days.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    return days


def build_daily(cohort: pd.DataFrame, declared_days: set[date]) -> tuple[pd.DataFrame, dict]:
    dated = cohort[cohort.date_valid]
    observed = dated.local_date.dropna()
    if observed.empty or not declared_days:
        raise ValueError("no_daily_calendar")
    start = min(pd.Timestamp(min(declared_days)), observed.min())
    end = max(pd.Timestamp(max(declared_days)), observed.max())
    daily = pd.DataFrame(index=pd.date_range(start, end, freq="D"))
    for label in ("known_high", "known_other", "risk_unknown", "risk_conflicting"):
        daily[label] = dated.loc[dated.classification.eq(label)].groupby("local_date").size().reindex(daily.index, fill_value=0)
    daily["observed_protocols"] = daily[["known_high", "known_other", "risk_unknown", "risk_conflicting"]].sum(axis=1)
    daily["coverage_declared"] = [x.date() in declared_days for x in daily.index]
    daily["coverage_status"] = np.where(daily.coverage_declared, "declared_window_unverified", "extraction_gap_or_outside_window")
    # A zero is zero observed records, never proof of absence of events.
    daily["y"] = daily.known_high.astype(float).where(daily.coverage_declared, np.nan)
    daily["unknown_risk_upper_bound"] = daily.known_high + daily.risk_unknown + daily.risk_conflicting
    monthly_declared = daily.groupby(daily.index.to_period("M")).coverage_declared.sum()
    full_months = [period for period, count in monthly_declared.items() if count == period.days_in_month]
    daily["complete_month"] = daily.index.to_period("M").isin(full_months)
    daily.index.name = "date"
    monthly = daily.groupby(daily.index.to_period("M"))[["known_high", "observed_protocols", "risk_unknown", "risk_conflicting"]].sum()
    complete = pd.Series(monthly.index.isin(full_months), index=monthly.index)
    # Compare adjacent complete months only: an incomplete endpoint is not a shift.
    monthly["previous_month_ratio"] = monthly.known_high.div(monthly.known_high.shift(1).replace(0, np.nan)).where(complete & complete.shift(1, fill_value=False))
    summary = {
        "start_date": str(start.date()), "end_date": str(end.date()), "calendar_days": len(daily),
        "declared_days": int(daily.coverage_declared.sum()), "gap_days": int((~daily.coverage_declared).sum()),
        "zero_observed_high_risk_days": int((daily.coverage_declared & daily.known_high.eq(0)).sum()),
        "complete_months": [str(p) for p in full_months],
        "partial_months": [str(p) for p in monthly_declared.index if p not in full_months],
        "monthly_high_risk": {str(p): int(v) for p, v in monthly.known_high.items()},
        "large_monthly_changes": {str(p): float(v) for p, v in monthly.previous_month_ratio.items() if pd.notna(v) and (v > 2 or v < .5)},
        "monthly_change_scope": "adjacent complete declared months only; partial months excluded from ratios",
        "coverage_limitation": "Filename windows declare extraction scope, not verified completeness; zeros mean observed zeros only.",
    }
    assert int(daily.known_high.sum()) == int(cohort.high_risk.sum())
    return daily.reset_index(), summary


def continuous_model_series(daily: pd.DataFrame) -> pd.Series:
    """Latest contiguous declared segment ending in a complete calendar month."""
    complete = daily[daily.complete_month & daily.coverage_declared]
    if complete.empty:
        raise ValueError("no_complete_model_month")
    end = complete.date.max()
    part = daily[daily.date.le(end)].set_index("date").y
    gaps = part[part.isna()]
    if not gaps.empty:
        part = part.loc[gaps.index.max() + pd.Timedelta(days=1):]
    return part.astype(float)


def synthetic_records(seed: int = 42, days: int = 1277) -> tuple[pd.DataFrame, set[date], dict]:
    """Reproducible fake protocols, seasonal demand, clusters and conflicts."""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.date_range("2021-01-01", periods=days, freq="D")
    centers = np.array([[-10., -63.], [-10.025, -63.025], [-10.04, -62.98]])
    index = 0
    for day_index, day in enumerate(dates):
        high_count = rng.poisson(12 + (day.dayofweek < 5) * 6 + day_index / 350)
        other_count = rng.poisson(14)
        for j in range(high_count + other_count):
            index += 1
            high = j < high_count
            position = centers[rng.choice(3, p=[.5, .3, .2])] + rng.normal(0, .00045, 2)
            if rng.random() < .12:
                position = np.array([-10.025, -63.]) + rng.uniform(-.05, .05, 2)
            position = np.round(position, 4)
            record = {"protocolo": "synthetic:" + hashlib.sha256(str(index).encode()).hexdigest(),
                      "dataCriacao": (day.tz_localize(TIMEZONE) + pd.Timedelta(hours=12)).isoformat(),
                      "nivelRisco": "Alto" if high else "Baixo",
                      "localizacao": {"latitude": float(position[0]), "longitude": float(position[1])}}
            if index % 401 == 0:
                record["nivelRisco"] = "SYNTHETIC_UNREVIEWED"
            if index % 503 == 0:
                record.pop("localizacao")
            rows.append(record)
            if index % 31 == 0:
                rows.append(json.loads(json.dumps(record)))
            if index % 211 == 0:
                modified = json.loads(json.dumps(record))
                modified["classificacaoSituacao"] = "SYNTHETIC_CHANGED_NONCORE"
                rows.append(modified)
            if index % 997 == 0:
                modified = json.loads(json.dumps(record))
                modified["nivelRisco"] = "Baixo" if high else "Alto"
                rows.append(modified)
            if index % 1297 == 0:
                modified = json.loads(json.dumps(record))
                modified["dataCriacao"] = (pd.Timestamp(record["dataCriacao"]) + pd.Timedelta(days=1)).isoformat()
                rows.append(modified)
            if index % 1597 == 0 and "localizacao" in record:
                modified = json.loads(json.dumps(record))
                modified["localizacao"]["latitude"] += .03
                rows.append(modified)
    projected = project_records(rows, "synthetic", {"Alto", "Baixo"})
    return projected, {d.date() for d in dates}, {"seed": seed, "synthetic": True, "days": days}
