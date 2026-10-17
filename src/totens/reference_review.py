"""Offline, UUID-preserving revisions of authenticated institutional references."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from totens.acervo import digest
from totens.local_migration import publish_new, reject_links, sign_document, verify_document
from totens.references import authenticated_reference, generated_calendar, normalized
from totens.reference_calendar import expand_rules, municipal_daily, review_legacy


# These describe the recorded name, not verified staffing or historical operation.
UNIT_TYPES = (
    ("INTERATIVA|VIRTUAL", "VIRTUAL", "NAO_DETERMINADA"),
    ("UNISP", "INSTALACAO_COMPARTILHADA", "INSTALACAO_COMPARTILHADA"),
    ("ACADEPOL|ACADEMIA|COLEGIO|CENTRO DE ENSINO|COORDENADORIA DE ENSINO|COORDENADORIA DE EDUCACAO", "ENSINO", "ENSINO"),
    ("DELEGACIA GERAL|DGPC", "ADMINISTRACAO", "ADMINISTRATIVA"),
    ("DELEGACIA|DEFLAG|DEPARTAMENTO DE FLAGRANTES", "DELEGACIA", "OPERACIONAL"),
    ("INSTITUTO MEDICO LEGAL|\\bIML\\b", "INSTITUTO_MEDICO_LEGAL", "OPERACIONAL"),
    ("IDENTIFICACAO|\\bIICC\\b", "INSTITUTO_IDENTIFICACAO", "OPERACIONAL"),
    ("BATALHAO|BPM\\b", "BATALHAO", "OPERACIONAL"),
    ("COMPANHIA|\\bCIA\\b", "COMPANHIA", "OPERACIONAL"),
    ("GPPO|GRUPAMENTO|\\bGBM\\b", "GRUPAMENTO", "OPERACIONAL"),
    ("COMANDO DE OPERACOES|AVIACAO", "COMANDO_OPERACIONAL", "OPERACIONAL"),
    ("INTELIGENCIA", "INTELIGENCIA", "OPERACIONAL"),
    ("CORREG", "CORREGEDORIA", "ADMINISTRATIVA"),
    ("FUNDO|FUNRESPOL|FUMRESPOM", "FUNDO", "ADMINISTRATIVA"),
    ("CONSELHO|CONSUPOL", "CONSELHO", "ADMINISTRATIVA"),
    ("GABINETE|ESTADO.MAIOR|COMANDO GERAL|COMANDO REGIONAL|COMANDO DE POLICIAMENTO", "COMANDO_ADMINISTRATIVO", "ADMINISTRATIVA"),
    ("DIRETORIA|IRETORIA|COORDENADORIA|ASSESSORIA|AJUDANCIA|SECRETARIA|DELEGACIA GERAL", "ADMINISTRACAO", "ADMINISTRATIVA"),
    ("DIVISAO", "DIVISAO", "NAO_DETERMINADA"),
    ("POLICIA MILITAR", "UNIDADE_POLICIAL_TIPO_NAO_DETERMINADO", "NAO_DETERMINADA"),
)


def classify_units(units: pd.DataFrame, source_hash: str) -> pd.DataFrame:
    result = units.copy()
    for original, current in (("corporacao_origem", "corporacao"), ("nome_original", "nome_unidade"), ("tipo_original", "tipo_unidade")):
        if original not in result:
            result[original] = result[current]
    result["natureza"] = "NAO_DETERMINADA"
    result["tipo_unidade"] = "NAO_CLASSIFICADO"
    result["classification_rule"] = "no_name_rule"
    names = result.nome_unidade.map(normalized)
    for pattern, kind, nature in UNIT_TYPES:
        chosen = names.str.contains(pattern, regex=True) & result.tipo_unidade.eq("NAO_CLASSIFICADO")
        result.loc[chosen, ["tipo_unidade", "natureza", "classification_rule"]] = [kind, nature, pattern]
    result["classification_method"] = "institutional_name_rule_v1"
    result["classification_source"] = source_hash
    result["classification_status"] = np.where(result.tipo_unidade.eq("NAO_CLASSIFICADO"), "pending", "descriptive_name_only")
    if "corporation_status" not in result:
        result["corporation_status"] = "legacy_source_not_independently_verified"
    # Contradictory legacy attribution stays unknown until an official correction.
    contradiction = result.corporacao.eq("PM") & names.str.contains("POLICIA CIVIL|SECRETARIA", regex=True)
    result.loc[contradiction, "corporacao"] = "NAO_DETERMINADA"
    result.loc[contradiction, "corporation_status"] = "name_source_contradiction_pending"
    # Shared premises do not identify a unique responsible corporation.
    result.loc[result.natureza.eq("INSTALACAO_COMPARTILHADA"), "corporacao"] = "NAO_DETERMINADA"
    result.loc[result.natureza.eq("INSTALACAO_COMPARTILHADA"), "corporation_status"] = "shared_site_responsibility_pending"
    return result


def locate_register(frame: pd.DataFrame, municipalities: gpd.GeoDataFrame) -> pd.DataFrame:
    result = frame.copy()
    municipalities = municipalities.to_crs(4326)
    if "codigo_municipio_original" not in result:
        result["codigo_municipio_original"] = result.codigo_municipio_ibge
    result["codigo_municipio_geometrico"] = None
    result["municipality_method"] = "legacy_name_match"
    result["location_status"] = "missing_or_invalid_coordinates"
    result["municipality_conflict"] = False
    result["spatial_eligible"] = False
    for index, row in result.iterrows():
        valid = pd.notna(row.latitude) and pd.notna(row.longitude) and np.isfinite(row.latitude) and np.isfinite(row.longitude)
        valid = bool(valid and abs(row.latitude) <= 90 and abs(row.longitude) <= 180)
        result.at[index, "coordinates_valid"] = valid
        if not valid:
            continue
        candidates = municipalities.loc[municipalities.geometry.intersects(Point(row.longitude, row.latitude)), "cd_mun"].astype(str).unique()
        if len(candidates) != 1:
            result.at[index, "location_status"] = "boundary_ambiguous" if len(candidates) > 1 else "outside_ro"
            continue
        code = candidates[0]
        result.at[index, "codigo_municipio_geometrico"] = code
        result.at[index, "location_status"] = "inside_unique_municipality_not_physical_site_verified"
        if pd.isna(row.codigo_municipio_ibge) or not str(row.codigo_municipio_ibge):
            result.at[index, "codigo_municipio_ibge"] = code
            result.at[index, "municipality_method"] = "retrospective_point_in_ibge_polygon_2024"
        elif str(row.codigo_municipio_ibge) != code:
            result.at[index, "municipality_conflict"] = True
            result.at[index, "location_status"] = "name_coordinate_disagreement"
        virtual = row.get("tipo_unidade") == "VIRTUAL"
        result.at[index, "spatial_eligible"] = not virtual and not result.at[index, "municipality_conflict"]
        if virtual:
            result.at[index, "location_status"] = "virtual_unit_physical_location_unverified"
    result["physical_location_verified"] = False
    return result


def apply_corrections(frame, id_column, corrections, sources):
    """Apply reviewed attribute changes to existing UUIDs; never create identities."""
    result, audit = frame.copy(), []
    allowed = {"corporacao", "codigo_municipio_ibge", "tipo_unidade", "natureza"}
    if result[id_column].isna().any() or result[id_column].duplicated().any():
        raise ValueError("invalid_entity_ids")
    for change in corrections:
        if change["table"] != id_column:
            continue
        selected = result[id_column].eq(change["entity_id"])
        if selected.sum() != 1 or change["field"] not in allowed:
            raise ValueError("unreviewed_entity_change")
        if change["source_id"] not in sources or sources[change["source_id"]]["status"] != "archived":
            raise ValueError("correction_requires_archived_source")
        index = result.index[selected][0]
        actual = result.at[index, change["field"]]
        same = change["before"] is None if pd.isna(actual) else actual == change["before"]
        if not same:
            raise ValueError("correction_before_value_mismatch")
        if not change.get("locator") or not change.get("reason"):
            raise ValueError("correction_evidence_missing")
        result.at[index, change["field"]] = change["after"]
        if change["field"] == "corporacao":
            result.at[index, "corporation_status"] = "official_document_reviewed_no_operation_date_asserted"
        if change["field"] == "codigo_municipio_ibge" and "codigo_municipio_geometrico" in result:
            geometric = result.at[index, "codigo_municipio_geometrico"]
            conflict = pd.notna(geometric) and str(geometric) != str(change["after"])
            result.at[index, "municipality_conflict"] = conflict
            result.at[index, "municipality_method"] = "reviewed_official_document"
            if conflict:
                result.at[index, "location_status"] = "name_coordinate_disagreement"
                result.at[index, "spatial_eligible"] = False
        audit.append(change)
    if set(result[id_column]) != set(frame[id_column]):
        raise ValueError("entity_identity_changed")
    return result, audit










def verify_evidence(folder):
    """Verify all acquisition artifacts before any correction or calendar expansion."""
    bundle = json.loads((folder / "review.json").read_text(encoding="utf8"))
    archive_name = bundle.get("archive_directory", "archive")
    if Path(archive_name).name != archive_name or archive_name in (".", ".."):
        raise ValueError("invalid_evidence_archive")
    sources = {s["source_id"]: s for s in json.loads((folder / archive_name / "acquisition.json").read_text(encoding="utf8"))}
    for source in sources.values():
        if source["status"] == "archived":
            path = folder / archive_name / source["path"]
            reject_links(path)
            if not path.resolve().is_relative_to((folder / archive_name).resolve()):
                raise ValueError("source_archive_path_escape")
            if digest(path) != source["sha256"]:
                raise ValueError("reference_source_changed")
    researched = {}
    for filename in sorted((folder / "search_broad").glob("*.json")):
        record = json.loads(filename.read_text(encoding="utf8"))
        for query in record["queries"]:
            researched[(query["cd_mun"], query["year"])] = {"query": query["q"],
                "search_artifact": filename.relative_to(folder).as_posix(), "search_sha256": digest(filename),
                "search_result_status": "no_results" if "Empty search results" in str(record["result"]) else "candidates_only"}
    return bundle, sources, researched


def revise_references(root, key, *, previous_version, version, evidence_dir, years=range(2019, 2027)):
    """Publish a new complete version by atomic rename after validation."""
    if (previous_version == version or any(not name or Path(name).name != name or name in (".", "..")
                                          for name in (previous_version, version))):
        raise ValueError("invalid_reference_version")
    years = tuple(sorted(set(years)))
    if not years or min(years) > 2019 or max(years) < 2024:
        raise ValueError("calendar_must_cover_research_period")
    destination = root / "reference" / version
    reject_links(destination)
    if destination.exists():
        raise ValueError("reference_version_exists")
    previous = root / "reference" / previous_version
    receipt = verify_document(json.loads((previous / "receipt.json").read_bytes()), key)
    frames, inputs = {}, {}
    for name in ("unidades.parquet", "equipamentos.parquet", "totens.parquet", "calendario.parquet"):
        frames[name], hashes = authenticated_reference(root, previous_version, name, key)
        inputs.update(hashes)
    registry_path = previous / "entity_registry.json"
    if digest(registry_path) != receipt["outputs"]["entity_registry.json"]:
        raise ValueError("entity_registry_changed")
    registry = verify_document(json.loads(registry_path.read_bytes()), key)
    inputs[str(registry_path)] = digest(registry_path)
    reject_links(evidence_dir)
    if not evidence_dir.resolve().is_relative_to((root / "reference_sources").resolve()):
        raise ValueError("evidence_outside_private_reference_sources")
    bundle, sources, researched = verify_evidence(evidence_dir)
    geo = root / "reference/geospatial/ro_municipios_2024__ro_municipios_2024.parquet"
    municipalities = gpd.read_parquet(geo)
    inputs[str(geo)] = digest(geo)
    units = locate_register(classify_units(frames["unidades.parquet"], inputs[str(previous / "unidades.parquet")]), municipalities)
    units, corrections = apply_corrections(units, "unidade_id", bundle["corrections"], sources)
    equipment = locate_register(frames["equipamentos.parquet"], municipalities)
    equipment["physical_identity_status"] = np.where(equipment.identity_status.eq("legacy_id_ambiguous"), "pending_shared_legacy_id", "registered_record_not_physical_identity_verified")
    equipment["legacy_group_size"] = equipment.groupby(["origin", "id_legado"]).equipamento_id.transform("size")
    equipment, equipment_corrections = apply_corrections(equipment, "equipamento_id", bundle["corrections"], sources)
    known_codes = set(municipalities.cd_mun.astype(str))
    if any(not set(frame.codigo_municipio_ibge.dropna().astype(str)).issubset(known_codes) for frame in (units, equipment)):
        raise ValueError("unknown_corrected_municipality_code")
    calendar = generated_calendar(years)
    calendar["verification_basis"] = "library_rule_not_executive_decree"
    calendar["audience"] = "GENERAL_RULE"
    calendar["event_kind"] = np.where(calendar.tipo.eq("FACULTATIVO"), "FACULTATIVO", "FERIADO")
    official = expand_rules(bundle["rules"], years, sources)
    calendar = pd.concat([calendar, official], ignore_index=True)
    legacy = frames["calendario.parquet"].loc[lambda f: f.tipo.eq("MUNICIPAL")].copy()
    legacy_audit = review_legacy(legacy, official)
    coverage_rows = []
    for city in municipalities[["cd_mun", "nm_mun"]].itertuples(index=False):
        for year in range(2019, 2025):
            code = str(city.cd_mun)
            if (code, year) not in researched:
                raise ValueError("municipality_year_not_researched")
            selected = official[official.codigo_municipio_ibge.eq(code) & official.data.dt.year.eq(year)
                                & official.audience.eq("EXECUTIVO_MUNICIPAL")]
            statewide = official[official.codigo_municipio_ibge.eq(code) & official.data.dt.year.eq(year)
                                 & official.audience.eq("EXECUTIVO_ESTADUAL")]
            # No single-date source proves a full-year calendar. Completeness requires explicit evidence.
            complete = next((c for c in bundle.get("complete_coverage", []) if c["municipality_code"] == code and c["year"] == year), None)
            if complete and (complete["source_id"] not in sources or sources[complete["source_id"]]["status"] != "archived"):
                raise ValueError("coverage_requires_archived_source")
            coverage_rows.append({"codigo_municipio_ibge": code, "municipio_nome": city.nm_mun, "year": year,
                "coverage_status": "confirmed" if complete else "partial" if len(selected) else "not_proven",
                "audience": "EXECUTIVO_MUNICIPAL", "official_events": len(selected), "state_executive_events": len(statewide), "search_completed": True,
                "source_ids": "|".join(sorted(set(selected.source_id))),
                "supporting_state_source_ids": "|".join(sorted(set(statewide.source_id))),
                **researched[(code, year)],
                "limitation": "Historical municipal laws, amendments and complete annual scope not fully verified." if not complete else "Complete annual calendar document reviewed; essential services remain outside administrative closure."})
    coverage = pd.DataFrame(coverage_rows)
    equipment_audit = equipment[["equipamento_id", "origin", "id_legado", "legacy_group_size", "physical_identity_status", "location_status"]].copy()
    unit_audit = units[["unidade_id", "nome_original", "corporacao_origem", "corporacao", "corporation_status", "tipo_unidade", "natureza", "classification_method", "classification_rule", "classification_source", "classification_status", "municipality_method", "location_status", "municipality_conflict"]].copy()
    for old, new, id_name in ((frames["unidades.parquet"], units, "unidade_id"), (frames["equipamentos.parquet"], equipment, "equipamento_id")):
        if len(old) != len(new) or set(old[id_name]) != set(new[id_name]) or new[id_name].duplicated().any():
            raise ValueError("register_identity_or_count_changed")
    # Totem geometry and IDs are retained byte-for-byte as an analytical compatibility contract.
    staging = root / "reference" / (".pending_" + version + "_" + uuid.uuid4().hex[:8])
    staging.mkdir(parents=True, exist_ok=False)
    for name, frame in (("unidades.parquet", units), ("equipamentos.parquet", equipment), ("calendario.parquet", calendar),
                        ("municipal_coverage.parquet", coverage), ("legacy_calendar_review.parquet", legacy_audit),
                        ("unit_review.parquet", unit_audit), ("equipment_review.parquet", equipment_audit)):
        frame.to_parquet(staging / name, index=False)
    (staging / "totens.parquet").write_bytes((previous / "totens.parquet").read_bytes())
    (staging / "entity_registry.json").write_bytes(registry_path.read_bytes())
    publish_new(staging / "corrections.json", sign_document({"corrections": corrections + equipment_corrections,
                "previous_version": previous_version, "registry_entries_preserved": len(registry["registry"])}, key))
    dates = pd.date_range("2019-01-01", "2024-12-31")
    municipal_daily(dates, municipalities.cd_mun, calendar, coverage).to_parquet(staging / "municipal_daily.parquet", index=False)
    for path in sorted(evidence_dir.rglob("*")):
        if path.is_file():
            reject_links(path)
            inputs[str(path)] = digest(path)
    for path, expected in inputs.items():
        if digest(Path(path)) != expected:
            raise ValueError("reference_input_changed_during_build")
    report = {"status": "complete", "version": version, "previous_version": previous_version,
        "root": destination.relative_to(root).as_posix(), "units": len(units), "equipment": len(equipment),
        "totems": len(frames["totens.parquet"]), "identity_preserved": True, "totem_bytes_preserved": True,
        "corporation_corrections": sum(c["field"] == "corporacao" for c in corrections),
        "municipality_unresolved": int(units.codigo_municipio_ibge.isna().sum()),
        "municipality_conflicts": int(units.municipality_conflict.sum()),
        "units_type_pending": int(units.tipo_unidade.eq("NAO_CLASSIFICADO").sum()),
        "ambiguous_equipment_rows": int(equipment.identity_status.eq("legacy_id_ambiguous").sum()),
        "municipality_years_researched": len(coverage), "coverage_status": coverage.coverage_status.value_counts().to_dict(),
        "municipal_coverage_complete": bool(coverage.coverage_status.eq("confirmed").all()),
        "legacy_rows_reviewed": len(legacy_audit), "legacy_decisions": legacy_audit.decision.value_counts().to_dict(),
        "official_events": len(official), "calendar_years": sorted(set(years)),
        "inputs": {str(Path(p).relative_to(root.parent)): h for p, h in inputs.items()},
        "outputs": {p.name: digest(p) for p in staging.iterdir() if p.is_file()}}
    publish_new(staging / "receipt.json", sign_document(report, key))
    os.rename(staging, destination)
    return report
