"""Private institutional registers and reproducible, explicitly scoped calendars."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
from unidecode import unidecode

from totens.acervo import digest
from totens.local_migration import json_bytes, publish_new, reject_links, sign_document, verify_document


def normalized(value) -> str:
    return unidecode(str(value)).strip().upper()


def register_entities(frame: pd.DataFrame, registry: dict, namespace: str) -> pd.DataFrame:
    """Assign UUID once; ambiguous legacy IDs require explicit correction mapping."""
    frame = frame.copy()
    keys = frame.origin.astype(str) + ":" + frame.id_legado.fillna("").astype(str)
    frame["legacy_key"] = keys
    counts = keys.value_counts()
    rows = []
    for row in frame.to_dict("records"):
        identity = namespace + ":" + row["legacy_key"]
        ambiguous = bool(counts[row["legacy_key"]] > 1 or not str(row.get("id_legado") or "").strip())
        # Fingerprint is only an initial disambiguation lookup, never the entity ID.
        fingerprint = hashlib.sha256(json_bytes({k: None if pd.isna(v) else v for k, v in row.items()})).hexdigest()
        group = registry.get(identity)
        if group is not None and bool(group["ambiguous"]) != ambiguous:
            raise ValueError("entity_identity_requires_review")
        if group is None:
            group = registry[identity] = {"ambiguous": ambiguous, "ids": {}}
        lookup = fingerprint if ambiguous else "entity"
        if lookup not in group["ids"]:
            if ambiguous and group["ids"] and group.get("frozen"):
                raise ValueError("ambiguous_entity_change_requires_review")
            group["ids"][lookup] = str(uuid.uuid4())
        row["entity_id"] = group["ids"][lookup]
        row["identity_status"] = "legacy_id_ambiguous" if ambiguous else "internal_registered"
        rows.append(row)
    for group in registry.values():
        group["frozen"] = True
    result = pd.DataFrame(rows).drop(columns="legacy_key")
    if result.entity_id.duplicated().any():
        raise ValueError("duplicate_entity_id")
    return result


def read_institutional_csv(path: Path, corporation: str, municipalities: pd.DataFrame) -> pd.DataFrame:
    reject_links(path)
    source = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8").fillna("")
    mapping = {"ID": "id_legado", "Unidade": "nome_unidade", "Tipo": "tipo_unidade",
               "Endereço": "endereco_institucional", "Município": "municipio", "Latitude": "latitude", "Longitude": "longitude"}
    source = source.rename(columns=mapping)
    # Explicit institutional allowlist; no arbitrary contacts/documents carried over.
    result = source[[c for c in mapping.values() if c in source]].copy()
    for name in ("id_legado", "nome_unidade", "tipo_unidade", "municipio", "latitude", "longitude"):
        if name not in result:
            result[name] = ""
    result["tipo_unidade"] = result.tipo_unidade.replace("", "NAO_CLASSIFICADO")
    for name in ("latitude", "longitude"):
        result[name] = pd.to_numeric(result[name].str.replace(",", ".", regex=False), errors="coerce")
    valid = np.isfinite(result.latitude) & np.isfinite(result.longitude) & result.latitude.abs().le(90) & result.longitude.abs().le(180)
    result["coordinates_valid"] = valid
    result.loc[~valid, ["latitude", "longitude"]] = np.nan
    names = municipalities.nm_mun.map(normalized)
    if names.duplicated().any():
        raise ValueError("ambiguous_municipality_names")
    city_ids = dict(zip(names, municipalities.cd_mun.astype(str)))
    result["codigo_municipio_ibge"] = result.municipio.map(normalized).map(city_ids)
    result["corporacao"], result["origin"] = corporation, path.name
    result["source_sha256"] = digest(path)
    return result


def generated_calendar(years) -> pd.DataFrame:
    rows = []
    for year in sorted(set(years)):
        national = holidays.country_holidays("BR", years=year, language="pt_BR", categories=holidays.PUBLIC)
        regional = holidays.country_holidays("BR", subdiv="RO", years=year, language="pt_BR", categories=holidays.PUBLIC)
        optional = holidays.country_holidays("BR", years=year, language="pt_BR", categories=holidays.OPTIONAL)
        for date, title in sorted(regional.items()):
            scopes = [("NACIONAL", national[date])] if date in national else [("ESTADUAL", title)]
            if date in national and title != national[date]:
                scopes.append(("ESTADUAL", title))
            for scope, label in scopes:
                rows.append({"data": pd.Timestamp(date), "descricao": label, "tipo": scope,
                             "municipio_nome": "TODOS", "codigo_municipio_ibge": None,
                             "source": "holidays:BR:RO", "source_version": holidays.__version__,
                             "verified": True, "status": "library_rule"})
        for date, title in sorted(optional.items()):
            rows.append({"data": pd.Timestamp(date), "descricao": title, "tipo": "FACULTATIVO",
                         "municipio_nome": "TODOS", "codigo_municipio_ibge": None,
                         "source": "holidays:BR:OPTIONAL", "source_version": holidays.__version__,
                         "verified": False, "status": "generic_rule_not_executive_decree"})
    result = pd.DataFrame(rows)
    result["vigencia_inicio"] = result.data.dt.to_period("Y").dt.start_time
    result["vigencia_fim"] = result.data.dt.to_period("Y").dt.end_time.dt.normalize()
    return result


def holiday_features(dates: pd.DatetimeIndex, calendar: pd.DataFrame) -> pd.DataFrame:
    covered_years = set(pd.to_datetime(calendar.loc[calendar.source.eq("holidays:BR:RO"), "data"]).dt.year)
    if not set(dates.year).issubset(covered_years):
        raise ValueError("holiday_year_coverage_missing")
    result = pd.DataFrame(index=dates)
    for kind in ("NACIONAL", "ESTADUAL"):
        # Preserve the original library-rule experiment. Scoped administrative
        # decrees are available separately and must not silently change features.
        days = set(pd.to_datetime(calendar.loc[calendar.tipo.eq(kind) & calendar.verified
            & calendar.source.eq("holidays:BR:RO"), "data"]).dt.normalize())
        name = kind.lower()
        result[f"holiday_{name}"] = [int(d.normalize() in days) for d in dates]
        result[f"eve_{name}"] = [int((d + pd.Timedelta(days=1)).normalize() in days) for d in dates]
        result[f"after_{name}"] = [int((d - pd.Timedelta(days=1)).normalize() in days) for d in dates]
    return result


def prepare_references(project: Path, source: Path, root: Path, key: bytes, version: str, *, previous_version: str | None = None):
    import geopandas as gpd
    reject_links(source)
    if not version or Path(version).name != version or version in (".", ".."):
        raise ValueError("invalid_reference_version")
    destination = root / "reference" / version
    reject_links(destination)
    if destination.exists():
        raise ValueError("reference_version_exists")
    municipalities = gpd.read_parquet(root / "reference/geospatial/ro_municipios_2024__ro_municipios_2024.parquet")
    registry = {}
    if previous_version:
        if Path(previous_version).name != previous_version or previous_version in (".", ".."):
            raise ValueError("invalid_previous_reference_version")
        registry_path = root / "reference" / previous_version / "entity_registry.json"
        old_receipt = verify_document(json.loads((registry_path.parent / "receipt.json").read_bytes()), key)
        if digest(registry_path) != old_receipt["outputs"]["entity_registry.json"]:
            raise ValueError("entity_registry_changed")
        registry = verify_document(json.loads(registry_path.read_bytes()), key)["registry"]
    elif any((root / "reference").glob("*/entity_registry.json")):
        raise ValueError("explicit_previous_reference_version_required")
    destination.mkdir(parents=True)
    units = pd.concat([read_institutional_csv(source / "bronze/cadastros" / filename, corp, municipalities)
                       for filename, corp in (("pc.csv", "PC"), ("pm.csv", "PM"), ("cb.csv", "CB"))], ignore_index=True)
    units = register_entities(units, registry, "unit").rename(columns={"entity_id": "unidade_id"})
    units.to_parquet(destination / "unidades.parquet", index=False)
    equipment = read_institutional_csv(source / "bronze/cadastros/cameras.csv", "SESDEC", municipalities)
    equipment = register_entities(equipment, registry, "equipment").rename(columns={"entity_id": "equipamento_id"})
    equipment["is_totem"] = equipment.tipo_unidade.map(normalized).str.contains("MODULO|TOTEM", regex=True)
    equipment["unidade_id"] = None  # No institution relationship inferred from distance.
    equipment.to_parquet(destination / "equipamentos.parquet", index=False)
    points = equipment[equipment.is_totem & equipment.coordinates_valid].copy()
    points = points.rename(columns={"equipamento_id": "id_unidade"})
    points.to_parquet(destination / "totens.parquet", index=False)
    # Reconcile every retained point against the authenticated historical reference.
    prior = pd.read_parquet(root / "silver/cadastros/cadastro_totens.parquet")
    def point_counts(frame):
        return frame.groupby(["latitude", "longitude"]).size().to_dict()
    if point_counts(points) != point_counts(prior):
        raise ValueError("canonical_totem_points_changed")
    calendar = generated_calendar(range(2019, 2027))
    old = pd.read_parquet(source / "silver/calendario/feriados.parquet")
    municipal = old[old.tipo.map(normalized).str.contains("MUNICIPAL")].copy()
    municipal["tipo"] = "MUNICIPAL"
    municipal["source"] = "legacy_calendar_not_authoritatively_verified"
    municipal["source_version"] = digest(source / "silver/calendario/feriados.parquet")
    municipal["verified"], municipal["status"] = False, "historical_reference_pending_verification"
    name_ids = dict(zip(municipalities.nm_mun.map(normalized), municipalities.cd_mun.astype(str)))
    municipal["codigo_municipio_ibge"] = municipal.municipio_nome.map(normalized).map(name_ids)
    municipal["data"] = pd.to_datetime(municipal.data).dt.normalize()
    municipal["vigencia_inicio"] = municipal.data.dt.to_period("Y").dt.start_time
    municipal["vigencia_fim"] = municipal.data.dt.to_period("Y").dt.end_time.dt.normalize()
    calendar = pd.concat([calendar, municipal[calendar.columns]], ignore_index=True).drop_duplicates()
    calendar.to_parquet(destination / "calendario.parquet", index=False)
    publish_new(destination / "entity_registry.json", sign_document({"registry": registry}, key))
    # Registry is append-only by version: select latest via the authenticated receipt.
    receipt = {"status": "complete", "version": version, "root": destination.relative_to(root).as_posix(),
               "units": len(units), "equipment": len(equipment), "totems": len(points),
               "ambiguous_equipment_rows": int(equipment.identity_status.eq("legacy_id_ambiguous").sum()),
               "municipal_coverage_complete": False, "optional_decrees_verified": False,
               "municipal_rows_pending_verification": len(municipal),
               "calendar_version": holidays.__version__,
               "authority_candidate": "https://transparencia.ro.gov.br/feriados",
               "inputs": {p.relative_to(project).as_posix(): digest(p) for p in [*(source / "bronze/cadastros").glob("*.csv"), source / "silver/calendario/feriados.parquet"]},
               "outputs": {p.name: digest(p) for p in destination.iterdir() if p.is_file()}}
    publish_new(destination / "receipt.json", sign_document(receipt, key))
    return receipt


def authenticated_reference(root: Path, version: str, name: str, key: bytes):
    if not version or Path(version).name != version or Path(name).name != name:
        raise ValueError("invalid_reference_name")
    folder = root / "reference" / version
    reject_links(folder)
    receipt_path = folder / "receipt.json"
    receipt = verify_document(json.loads(receipt_path.read_bytes()), key)
    if receipt.get("status") != "complete" or name not in receipt["outputs"]:
        raise ValueError("reference_not_authenticated")
    path = folder / name
    if digest(path) != receipt["outputs"][name]:
        raise ValueError("reference_hash_mismatch")
    return pd.read_parquet(path), {str(path): digest(path), str(receipt_path): digest(receipt_path)}
