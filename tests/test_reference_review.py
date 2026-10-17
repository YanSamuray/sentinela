import copy
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from totens.reference_review import apply_corrections, classify_units, locate_register
from totens.reference_review import revise_references, verify_evidence
from totens.acervo import digest
from totens.local_migration import sign_document, verify_document


def cities():
    return gpd.GeoDataFrame({"cd_mun": ["A", "B"], "nm_mun": ["Cidade A", "Cidade B"],
        "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]}, crs=4326)


def units():
    return pd.DataFrame({"unidade_id": ["uuid-a", "uuid-b", "uuid-c", "uuid-d"],
        "nome_unidade": ["Delegacia de Policia Civil", "UNISP", "Delegacia Virtual", "DGPC - Delegacia Geral"],
        "tipo_unidade": ["NAO_CLASSIFICADO"] * 4, "corporacao": ["PM"] * 4,
        "codigo_municipio_ibge": [None, "B", "A", "A"], "latitude": [.5, .5, .5, None],
        "longitude": [.5, 1., .5, None]})


def sources():
    return {"official": {"status": "archived", "url": "https://example.ro.gov.br/decreto", "sha256": "hash"}}


def rule():
    return {"rule_id": "synthetic", "source_id": "official", "valid_from": "2023-01-01", "valid_to": "2024-12-31",
        "month": 2, "day": 13, "title": "Synthetic event", "event_kind": "FERIADO", "audience": "EXECUTIVO_MUNICIPAL",
        "municipality_code": "A", "municipality_name": "Cidade A", "locator": "Art. 1", "publication_date": "2022-12-20"}


def test_name_classification_does_not_treat_origin_as_verified_corporation():
    frame = classify_units(units(), "sourcehash")
    assert frame.iloc[0].corporacao == "NAO_DETERMINADA"
    assert frame.iloc[0].corporation_status == "name_source_contradiction_pending"
    assert frame.iloc[1].corporacao == "NAO_DETERMINADA"
    assert frame.iloc[3].natureza == "ADMINISTRATIVA"
    assert frame.nome_original.tolist() == units().nome_unidade.tolist()


def test_missing_municipality_boundary_virtual_and_missing_coordinates():
    frame = locate_register(classify_units(units(), "hash"), cities())
    assert frame.iloc[0].codigo_municipio_ibge == "A"
    assert frame.iloc[0].municipality_method.startswith("retrospective")
    assert frame.iloc[1].location_status == "boundary_ambiguous"
    assert not frame.iloc[1].spatial_eligible
    assert not frame.iloc[2].spatial_eligible
    assert not frame.iloc[3].spatial_eligible
    assert not frame.physical_location_verified.any()
    changed = units().iloc[:1].copy()
    changed["codigo_municipio_ibge"] = "B"
    assert locate_register(changed, cities()).municipality_conflict.all()
    changed["longitude"] = 3.
    assert locate_register(changed, cities()).location_status.eq("outside_ro").all()


def test_reviewed_attribute_correction_preserves_uuid_and_checks_before_value():
    correction = {"table": "unidade_id", "entity_id": "uuid-a", "field": "corporacao", "before": "PM", "after": "PC",
                  "source_id": "official", "locator": "Roster entry", "reason": "Institutional responsibility confirmed"}
    new, audit = apply_corrections(units().iloc[::-1], "unidade_id", [correction], sources())
    assert new.set_index("unidade_id").loc["uuid-a", "corporacao"] == "PC"
    assert set(new.unidade_id) == set(units().unidade_id) and len(audit) == 1
    changed = copy.deepcopy(correction)
    changed["before"] = "CB"
    with pytest.raises(ValueError, match="before_value"):
        apply_corrections(units(), "unidade_id", [changed], sources())
    changed["field"] = "unidade_id"
    with pytest.raises(ValueError, match="unreviewed_entity"):
        apply_corrections(units(), "unidade_id", [changed], sources())






def test_offline_revision_preserves_previous_receipt_ids_and_totem_bytes(tmp_path, monkeypatch):
    root, secret = tmp_path / "data", b"synthetic-key-only"
    previous = root / "reference/v1"
    previous.mkdir(parents=True)
    geo = root / "reference/geospatial"
    geo.mkdir()
    cities().to_parquet(geo / "ro_municipios_2024__ro_municipios_2024.parquet")
    first = units()
    first.to_parquet(previous / "unidades.parquet", index=False)
    equipment = first.iloc[:2].rename(columns={"unidade_id": "equipamento_id"}).copy()
    equipment["origin"], equipment["id_legado"] = "old.csv", "shared"
    equipment["identity_status"] = "legacy_id_ambiguous"
    equipment.to_parquet(previous / "equipamentos.parquet", index=False)
    pd.DataFrame({"id_unidade": ["totem"], "latitude": [.5], "longitude": [.5]}).to_parquet(previous / "totens.parquet")
    calendar = pd.DataFrame({"data": pd.to_datetime(["2023-02-13"]), "tipo": ["MUNICIPAL"], "codigo_municipio_ibge": ["A"],
                             "municipio_nome": ["Cidade A"], "descricao": ["Synthetic"]})
    calendar.to_parquet(previous / "calendario.parquet", index=False)
    (previous / "entity_registry.json").write_text(json.dumps(sign_document({"registry": {}}, secret)), encoding="utf8")
    receipt = sign_document({"status": "complete", "outputs": {p.name: digest(p) for p in previous.iterdir()}}, secret)
    (previous / "receipt.json").write_text(json.dumps(receipt), encoding="utf8")
    original_hashes = {p.name: digest(p) for p in previous.iterdir()}
    evidence = root / "reference_sources/synthetic"
    (evidence / "archive").mkdir(parents=True)
    doc = evidence / "archive/source.txt"
    doc.write_text("Synthetic official document", encoding="utf8")
    source = {"source_id": "official", "status": "archived", "path": "source.txt", "url": "https://example.ro.gov.br", "sha256": digest(doc)}
    (evidence / "archive/acquisition.json").write_text(json.dumps([source]), encoding="utf8")
    (evidence / "review.json").write_text(json.dumps({"corrections": [], "rules": [rule()]}), encoding="utf8")
    (evidence / "search_broad").mkdir()
    queries = [{"cd_mun": code, "year": year, "q": "synthetic"} for code in ("A", "B") for year in range(2019, 2025)]
    (evidence / "search_broad/batch.json").write_text(json.dumps({"queries": queries, "result": "synthetic search"}), encoding="utf8")
    def no_network(*args, **kwargs):
        raise AssertionError("Offline revision must not access the network")
    monkeypatch.setattr("requests.get", no_network)
    result = revise_references(root, secret, previous_version="v1", version="v2", evidence_dir=evidence)
    assert result["municipality_years_researched"] == 12 and result["legacy_rows_reviewed"] == 1
    assert {p.name: digest(p) for p in previous.iterdir()} == original_hashes
    new = root / "reference/v2"
    assert (new / "totens.parquet").read_bytes() == (previous / "totens.parquet").read_bytes()
    assert set(pd.read_parquet(new / "equipamentos.parquet").equipamento_id) == set(equipment.equipamento_id)
    verified = verify_document(json.loads((new / "receipt.json").read_text(encoding="utf8")), secret)
    assert verified["identity_preserved"]
    # An archived source changed after acquisition must be rejected before publication.
    doc.write_text("changed", encoding="utf8")
    with pytest.raises(ValueError, match="source_changed"):
        revise_references(root, secret, previous_version="v1", version="v3", evidence_dir=evidence)
    assert not (root / "reference/v3").exists()
    source["path"] = "../../outside"
    (evidence / "archive/acquisition.json").write_text(json.dumps([source]), encoding="utf8")
    with pytest.raises(ValueError, match="path_escape"):
        verify_evidence(evidence)


def test_later_review_keeps_original_provenance():
    frame = classify_units(units(), "first")
    frame.loc[0, "corporacao"] = "PC"
    frame.loc[0, "corporation_status"] = "official_document_reviewed_no_operation_date_asserted"
    again = classify_units(frame.iloc[::-1], "second").set_index("unidade_id")
    assert again.loc["uuid-a", "corporacao_origem"] == "PM"
    assert again.loc["uuid-a", "corporacao"] == "PC"
    assert again.loc["uuid-a", "corporation_status"].startswith("official")


def test_official_municipality_correction_flags_coordinate_disagreement():
    located = locate_register(classify_units(units(), "hash"), cities())
    correction = {"table": "unidade_id", "entity_id": "uuid-a", "field": "codigo_municipio_ibge", "before": "A", "after": "B",
                  "source_id": "official", "locator": "Reviewed municipal attribution", "reason": "Institutional evidence"}
    changed, _ = apply_corrections(located, "unidade_id", [correction], sources())
    row = changed.set_index("unidade_id").loc["uuid-a"]
    assert row.municipality_conflict and not row.spatial_eligible
    assert row.location_status == "name_coordinate_disagreement"


def test_reviewed_missing_value_correction_preserves_identity():
    correction = {"table": "unidade_id", "entity_id": "uuid-a", "field": "codigo_municipio_ibge", "before": None, "after": "B",
                  "source_id": "official", "locator": "Official municipal code", "reason": "Missing institutional municipality"}
    frame, _ = apply_corrections(units(), "unidade_id", [correction], sources())
    assert frame.set_index("unidade_id").loc["uuid-a", "codigo_municipio_ibge"] == "B"
    assert set(frame.unidade_id) == set(units().unidade_id)
