import copy
from datetime import date

import pandas as pd
import pytest

from totens.portfolio.data import build_daily, consolidate, project_records, source_risk, window_days


def record(protocol="SYNTHETIC_A", risk="Alto", timestamp="2024-01-02T01:00:00+00:00", latitude=-10.):
    return {"protocolo": protocol, "nivelRisco": risk, "dataCriacao": timestamp,
            "localizacao": {"latitude": latitude, "longitude": -63.}}


def prepare(records):
    return consolidate(project_records(records, "synthetic", {"Alto", "Baixo", "Médio"}))


def test_exact_and_noncore_duplicates_retain_one_protocol_without_choosing_version():
    original = record()
    changed = {**original, "classificacaoSituacao": "SYNTHETIC_OTHER"}
    cohort, summary = prepare([original, copy.deepcopy(original), changed, record("SYNTHETIC_B")])
    assert len(cohort) == 2 and cohort.high_risk.all()
    assert summary["input_rows"] == 4 and summary["excess_rows"] == 2
    assert summary["different_repeated_protocols"] == 1
    assert cohort.iloc[0].record_variants == 2
    assert sum(summary["partition"].values()) == 2


@pytest.mark.parametrize("field,value", [("nivelRisco", "Baixo"), ("dataCriacao", "2024-01-03T01:00:00+00:00")])
def test_core_conflict_is_excluded_and_order_does_not_choose_latest(field, value):
    original, changed = record(), {**record(), field: value}
    left, _ = prepare([original, changed])
    right, _ = prepare([changed, original])
    pd.testing.assert_frame_equal(left, right)
    assert not left.temporal_eligible.any()
    assert not left.high_risk.any()


def test_location_conflict_keeps_temporal_target_but_not_coordinates():
    cohort, _ = prepare([record(), record(latitude=-11.)])
    assert cohort.high_risk.all() and not cohort.coordinates_valid.any()
    assert cohort.latitude.isna().all()


def test_review_is_exact_and_unrecognized_labels_are_not_low_risk():
    assert source_risk("Alto", {"Alto"}) == 3
    assert source_risk("ALTO", {"Alto"}) is None
    assert source_risk("SYNTHETIC_UNREVIEWED_ALTO", {"SYNTHETIC_UNREVIEWED_ALTO"}) is None
    cohort, summary = prepare([record(risk="SYNTHETIC_UNREVIEWED")])
    assert summary["partition"] == {"risk_unknown": 1}
    assert cohort.risk_level.isna().all()


def test_local_day_changes_at_utc_midnight_and_naive_source_is_excluded():
    cohort, _ = prepare([record(), record("SYNTHETIC_NAIVE", timestamp="2024-01-02T01:00:00")])
    assert cohort.loc[cohort.protocolo.eq("SYNTHETIC_A"), "local_date"].iloc[0] == pd.Timestamp("2024-01-01")
    assert not cohort.loc[cohort.protocolo.eq("SYNTHETIC_NAIVE"), "date_valid"].any()


def test_daily_zero_unknown_and_extraction_gap_are_distinct():
    cohort, _ = prepare([record(), record("SYNTHETIC_UNKNOWN", risk="UNREVIEWED", timestamp="2024-01-03T12:00:00-04:00")])
    daily, _ = build_daily(cohort, {date(2024, 1, 1), date(2024, 1, 3)})
    assert daily.loc[daily.date.eq("2024-01-01"), "y"].iloc[0] == 1
    assert daily.loc[daily.date.eq("2024-01-02"), "y"].isna().all()
    assert daily.loc[daily.date.eq("2024-01-03"), "y"].iloc[0] == 0
    assert daily.loc[daily.date.eq("2024-01-03"), "risk_unknown"].iloc[0] == 1


def test_window_calendar_preserves_gaps_and_rejects_unknown_filenames():
    assert window_days(["ocorrencias_20240101_a_20240102.json"]) == {date(2024, 1, 1), date(2024, 1, 2)}
    with pytest.raises(ValueError, match="invalid_source_window_name"):
        window_days(["arbitrary.json"])


def test_monthly_changes_compare_only_adjacent_complete_months():
    dates = pd.date_range("2024-01-01", "2024-04-01")
    records = [record(f"SYNTHETIC_{day:%Y%m%d}_{j}", timestamp=f"{day:%Y-%m-%d}T12:00:00-04:00")
               for day in dates for j in range(4 if day.month == 3 else 1)]
    cohort, _ = prepare(records)
    declared = {day.date() for day in dates if day >= pd.Timestamp("2024-01-31")}
    _, summary = build_daily(cohort, declared)
    assert summary["partial_months"] == ["2024-01", "2024-04"]
    assert set(summary["large_monthly_changes"]) == {"2024-03"}
    assert summary["large_monthly_changes"]["2024-03"] == pytest.approx(124 / 29)
