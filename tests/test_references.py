import copy

import pandas as pd
import pytest

from totens.references import generated_calendar, holiday_features, read_institutional_csv, register_entities
from totens.portfolio.forecast import features, predict_at


def entity_rows():
    return pd.DataFrame([{"origin": "pm.csv", "id_legado": "1", "latitude": -10., "longitude": -63.},
                         {"origin": "pc.csv", "id_legado": "1", "latitude": -11., "longitude": -62.}])


def test_entity_id_stable_after_reorder_and_coordinate_correction():
    registry = {}
    first = register_entities(entity_rows(), registry, "unit")
    from totens.local_migration import sign_document
    sign_document({"registry": registry}, b"synthetic-signing-key-for-test-only")
    changed = entity_rows().iloc[::-1].copy()
    changed.loc[0, "latitude"] = -10.5
    second = register_entities(changed, registry, "unit")
    assert first.set_index("origin").entity_id.to_dict() == second.set_index("origin").entity_id.to_dict()
    assert first.entity_id.nunique() == 2


def test_shared_id_preserves_points_and_correction_requires_review():
    source = entity_rows()
    source["origin"] = "cameras.csv"
    registry = {}
    first = register_entities(source, registry, "equipment")
    assert first.entity_id.nunique() == 2
    assert first.identity_status.eq("legacy_id_ambiguous").all()
    second = register_entities(source.iloc[::-1], registry, "equipment")
    assert set(first.entity_id) == set(second.entity_id)
    changed = source.copy()
    changed.loc[0, "latitude"] = -10.5
    with pytest.raises(ValueError, match="requires_review"):
        register_entities(changed, copy.deepcopy(registry), "equipment")


def test_missing_coordinates_do_not_remove_units_or_copy_personal_fields(tmp_path):
    path = tmp_path / "pc.csv"
    path.write_text("ID;Unidade;Município;Latitude;Longitude;CPF\n1;UNIDADE SINTETICA;CIDADE;;-63;DO_NOT_COPY\n", encoding="utf8")
    cities = pd.DataFrame({"nm_mun": ["CIDADE"], "cd_mun": ["SYNTHETIC"]})
    result = read_institutional_csv(path, "PC", cities)
    assert len(result) == 1 and not result.coordinates_valid.iloc[0]
    assert result.codigo_municipio_ibge.iloc[0] == "SYNTHETIC"
    assert "CPF" not in result and "DO_NOT_COPY" not in result.to_json()


def test_calendar_obeys_historical_rules_and_optional_scope():
    calendar = generated_calendar([2023, 2024])
    selected = calendar[calendar.data.eq(pd.Timestamp("2023-11-20"))]
    assert not selected.tipo.eq("NACIONAL").any()
    assert calendar[calendar.data.eq(pd.Timestamp("2024-11-20"))].tipo.eq("NACIONAL").any()
    assert calendar[calendar.tipo.eq("FACULTATIVO")].verified.eq(False).all()
    assert calendar[calendar.data.eq(pd.Timestamp("2024-01-04"))].tipo.eq("ESTADUAL").any()
    x = holiday_features(pd.date_range("2024-01-03", periods=3), calendar)
    assert x.iloc[0].eve_estadual == 1 and x.iloc[1].holiday_estadual == 1 and x.iloc[2].after_estadual == 1
    with pytest.raises(ValueError, match="coverage_missing"):
        holiday_features(pd.date_range("2025-01-01", periods=2), calendar)


def test_calendar_features_and_recursive_forecast_do_not_read_future_target():
    index = pd.date_range("2022-01-01", periods=800)
    series = pd.Series(10 + index.dayofweek, index=index, dtype=float)
    calendar = generated_calendar([2022, 2023, 2024])
    changed = series.copy()
    changed.iloc[600:] = 10000
    pd.testing.assert_frame_equal(features(series, calendar).iloc[:600], features(changed, calendar).iloc[:600])
    pd.testing.assert_frame_equal(predict_at(series, 600, "poisson_lr005_leaf15", calendar=calendar),
                                  predict_at(changed, 600, "poisson_lr005_leaf15", calendar=calendar))
