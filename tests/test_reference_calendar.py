import pandas as pd
import pytest

from totens.reference_calendar import expand_rules, municipal_daily
from totens.references import generated_calendar, holiday_features


def sources():
    return {"official": {"status": "archived", "url": "https://example.ro.gov.br/decreto", "sha256": "hash"}}


def rule():
    return {"rule_id": "synthetic", "source_id": "official", "valid_from": "2023-01-01", "valid_to": "2024-12-31",
        "month": 2, "day": 13, "title": "Synthetic event", "event_kind": "FERIADO", "audience": "EXECUTIVO_MUNICIPAL",
        "municipality_code": "A", "municipality_name": "Cidade A", "locator": "Art. 1", "publication_date": "2022-12-20"}


def test_expansion_respects_validity_and_requires_archived_evidence():
    frame = expand_rules([rule()], [2019, 2023, 2024, 2025], sources())
    assert frame.data.dt.year.tolist() == [2023, 2024]
    with pytest.raises(ValueError, match="archived_source"):
        expand_rules([rule()], [2023], {})
    changed = rule()
    changed["valid_to"] = "2022-12-31"
    with pytest.raises(ValueError, match="invalid_calendar_rule"):
        expand_rules([changed], [2023], sources())


def test_daily_calendar_preserves_count_timezone_scope_and_unknown_absence():
    events = expand_rules([rule(), rule()], [2023], sources())
    cv = pd.DataFrame({"codigo_municipio_ibge": ["A", "B"], "year": [2023, 2023],
                       "coverage_status": ["partial", "confirmed"], "audience": ["EXECUTIVO_MUNICIPAL"] * 2})
    dates = pd.DatetimeIndex(["2023-02-14T02:00Z", "2023-02-15T02:00Z"])
    daily = municipal_daily(dates, ["A", "B", "A"], events, cv)
    assert len(daily) == 4 and not daily.duplicated(["data", "codigo_municipio_ibge"]).any()
    a = daily[daily.codigo_municipio_ibge.eq("A")]
    assert a.iloc[0].data == pd.Timestamp("2023-02-13") and bool(a.iloc[0].holiday_municipal)
    assert pd.isna(a.iloc[1].holiday_municipal)
    assert daily[daily.codigo_municipio_ibge.eq("B")].holiday_municipal.eq(False).all()
    other = municipal_daily(dates, ["A"], events, cv, audience="EXECUTIVO_ESTADUAL")
    assert other.holiday_municipal.isna().all()
    assert other.coverage_status.eq("not_researched").all()
    with pytest.raises(ValueError, match="duplicate_calendar_coverage"):
        municipal_daily(dates, ["A"], events, pd.concat([cv, cv]))


def test_scoped_official_events_do_not_silently_change_library_experiment():
    calendar = generated_calendar([2023])
    before = holiday_features(pd.date_range("2023-02-12", periods=3), calendar)
    extra = expand_rules([rule()], [2023], sources())
    extra["tipo"] = "NACIONAL"
    after = holiday_features(pd.date_range("2023-02-12", periods=3), pd.concat([calendar, extra]))
    pd.testing.assert_frame_equal(before, after)
