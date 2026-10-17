"""One offline entry point for private and explicitly synthetic case runs."""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow
import sklearn
from shapely.geometry import box

from totens.local_migration import reject_links, sign_document, verify_document
from totens.local_secrets import load_local_key
from totens.portfolio import CASE_VERSION
from totens.portfolio.data import build_daily, consolidate, continuous_model_series, synthetic_records
from totens.portfolio.forecast import evaluate_forecasts
from totens.portfolio.io import archived_legacy, authenticated_totems, file_hash, load_manifest, private_ledger
from totens.portfolio.parameters import case_parameters
from totens.portfolio.report import figures, render_report
from totens.portfolio.spatial import analyze_spatial, load_geography


class CaseError(ValueError):
    """Fixed error codes, never remote messages or individual records."""


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean_json(v) for v in value]
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def write_json(path: Path, value):
    reject_links(path)
    path.write_text(json.dumps(clean_json(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _confined(path: Path, parent: Path) -> Path:
    reject_links(path)
    path = path.resolve()
    if path == parent.resolve() or not path.is_relative_to(parent.resolve()):
        raise CaseError("path_outside_allowed_root")
    return path


def _git_info(project: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=project, stderr=subprocess.DEVNULL))
        return commit, dirty
    except (subprocess.SubprocessError, OSError):
        return "unavailable", True


def run_case(*, mode: str = "synthetic", project: Path | None = None,
             config: Path | None = None, run_id: str | None = None, progress=None) -> Path:
    project = (project or _project_root()).resolve()
    if mode not in ("private", "synthetic"):
        raise CaseError("invalid_mode")
    config = config or project / "config/settings.minimized.toml"
    if not config.is_absolute():
        config = project / config
    reject_links(config)
    settings = tomllib.loads(config.read_text(encoding="utf-8"))
    parameters = case_parameters(settings)
    run_id = run_id or datetime.now(timezone.utc).strftime("case_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", run_id):
        raise CaseError("invalid_run_id")
    if mode == "private":
        requested = project / settings["paths"]["data_root"]
        reject_links(requested)
        root = requested.resolve()
        data = (project / "data").resolve()
        if root != data and (root.parent != data or not root.name.startswith("private_minimized_")):
            raise CaseError("private_source_required")
        if not (root / "metadata/migration_summary.json").is_file():
            raise CaseError("authenticated_private_source_required")
        # Authenticate the contract before creating any outputs.
        preflight_key = load_local_key(project / ".secrets/local_minimized_v1.dpapi", create=False)
        load_manifest(root, preflight_key)
        allowed = root / "analytics"
    else:
        root = None
        allowed = project / "data/portfolio_synthetic"
    output = _confined(allowed / run_id, allowed)
    allowed.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError:
        raise CaseError("run_destination_exists") from None
    commit, dirty = _git_info(project)
    implementation = {str(p): file_hash(p) for p in Path(__file__).parent.glob("*.py")}
    inputs = {str(config): file_hash(config)}
    meta = {"status": "running", "run_id": run_id, "mode": mode,
            "case_version": CASE_VERSION, "git_commit": commit, "git_dirty": dirty,
            "started_at": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
            "platform": platform.platform(),
            "versions": {"numpy": np.__version__, "pandas": pd.__version__, "geopandas": gpd.__version__, "pyarrow": pyarrow.__version__, "scikit_learn": sklearn.__version__},
            "implementation_sha256": {str(Path(p).relative_to(project)) if Path(p).is_relative_to(project) else Path(p).name: digest for p, digest in implementation.items()},
            "classification": "source reviewed level >= 3; consensus by protocol", "effective_parameters": parameters}
    write_json(output / "meta.json", meta)
    key = None
    try:
        if progress:
            progress({"event": "case_started", "mode": mode, "run_id": run_id})
        ledger = output / "input_ledger"
        if mode == "private":
            key = load_local_key(project / ".secrets/local_minimized_v1.dpapi", create=False)
            source, days, source_info, hashes = private_ledger(root, ledger, key, progress)
            inputs.update(hashes)
            totems, hashes = authenticated_totems(root, key)
            inputs.update(hashes)
            version = settings.get("portfolio", {}).get("reference_version")
            reference_summary = None
            if version:
                from totens.references import authenticated_reference
                canonical, hashes = authenticated_reference(root, version, "totens.parquet", key)
                if (canonical.groupby(["latitude", "longitude"]).size().to_dict()
                        != totems.groupby(["latitude", "longitude"]).size().to_dict()):
                    raise CaseError("reference_totem_points_changed")
                totems = canonical[["id_unidade", "latitude", "longitude"]].copy()
                inputs.update(hashes)
                reference_receipt = verify_document(json.loads((root / "reference" / version / "receipt.json").read_bytes()), key)
                reference_summary = {name: reference_receipt[name] for name in
                    ("version", "units", "equipment", "totems", "municipal_coverage_complete", "coverage_status",
                     "municipality_years_researched", "legacy_rows_reviewed", "official_events") if name in reference_receipt}
            geo = _confined(project / settings.get("portfolio", {}).get("geography_dir", "data/silver/geospatial"), project / "data")
            for name in ("br_uf_2024__br_uf_2024.parquet", "ro_municipios_2024__ro_municipios_2024.parquet"):
                inputs[str(geo / name)] = file_hash(geo / name)
            boundary, municipalities = load_geography(geo)
        else:
            reference_summary = {"version": "synthetic_generated", "real_references_read": False}
            source, days, source_info = synthetic_records()
            ledger.mkdir()
            source.to_parquet(ledger / "synthetic.parquet", index=False)
            totems = pd.DataFrame({"id_unidade": ["SYNTHETIC_TOTEM_A", "SYNTHETIC_TOTEM_B"],
                                   "latitude": [-10., -10.04], "longitude": [-63., -62.98]})
            boundary = box(-63.08, -10.10, -62.90, -9.93)
            municipalities = gpd.GeoDataFrame({"cd_mun": ["SYNTHETIC_AREA"], "nm_mun": ["Área fictícia"],
                                               "geometry": [boundary]}, crs=4326)
        cohort, quality = consolidate(source)
        cohort.to_parquet(output / "cohort.parquet", index=False)
        daily, temporal = build_daily(cohort, days)
        daily.to_csv(output / "daily.csv", index=False)
        if progress:
            progress({"event": "cohort_complete", "protocols": quality["distinct_protocols"],
                      "high_risk": quality["high_risk_protocols"], "excess_rows": quality["excess_rows"]})
        if mode == "private":
            legacy, hashes = archived_legacy(root, key, source_info)
            inputs.update(hashes)
        else:
            legacy = {"status": "not_applicable", "reason": "no_real_legacy_data_read_in_synthetic_mode"}
        if progress:
            progress({"event": "spatial_started"})
        cohort, spatial, tables = analyze_spatial(cohort, daily, boundary, totems, progress=progress, **parameters["spatial"])
        spatial_records = tables.pop("spatial_records")
        points = gpd.GeoDataFrame(spatial_records[["protocolo"]].copy(),
                    geometry=gpd.points_from_xy(spatial_records.longitude, spatial_records.latitude), crs=4326)
        joined = gpd.sjoin(points, municipalities[["cd_mun", "nm_mun", "geometry"]], how="left", predicate="intersects")
        # Administrative boundary ties are explicit; no arbitrary municipality.
        counts = joined.groupby("protocolo").cd_mun.nunique()
        spatial["municipality_boundary_ambiguous_protocols"] = int(counts.gt(1).sum())
        tables["municipality_counts"] = joined[joined.protocolo.isin(counts[counts.eq(1)].index)].drop_duplicates("protocolo").groupby(["cd_mun", "nm_mun"], as_index=False).size().rename(columns={"size": "high_risk_protocols"})
        cohort.to_parquet(output / "cohort.parquet", index=False)
        series = continuous_model_series(daily)
        if progress:
            progress({"event": "forecast_started", "days": len(series)})
        forecast, forecast_tables = evaluate_forecasts(series, progress, **parameters["forecast"])
        tables.update(forecast_tables)
        for name, table in tables.items():
            table.to_csv(output / f"{name}.csv", index=False)
        summary = {"mode": mode, "run_id": run_id, "case_version": CASE_VERSION, "git_commit": commit, "git_dirty": dirty,
                   "references": reference_summary,
                   "source": source_info, "quality": quality, "temporal": temporal, "spatial": spatial,
            "forecast": forecast, "legacy_reconciliation": legacy, "execution_threads": 1, "effective_parameters": parameters,
                   "forecast_metrics": clean_json(tables["forecast_performance"].to_dict("records"))}
        write_json(output / "summary.json", summary)
        figures(output, summary, daily, tables, boundary, totems)
        (output / "report.md").write_text(render_report(summary), encoding="utf-8")
        if progress:
            progress({"event": "input_integrity_recheck", "files": len(inputs)})
        for filename, expected in {**inputs, **implementation}.items():
            if file_hash(Path(filename)) != expected:
                raise CaseError("input_or_implementation_changed_during_run")
        meta.update({"status": "complete", "ended_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": {str(Path(p).relative_to(project)) if Path(p).is_relative_to(project) else Path(p).name: digest for p, digest in inputs.items()},
            "artifacts": [{"path": p.relative_to(output).as_posix(), "sha256": file_hash(p), "bytes": p.stat().st_size}
                          for p in sorted(output.rglob("*")) if p.is_file() and p.name != "meta.json"]})
        write_json(output / "meta.json", sign_document(clean_json(meta), key) if key else meta)
        if progress:
            progress({"event": "case_complete", "mode": mode, "run_id": run_id})
        return output
    except BaseException as error:
        meta.update({"status": "failed", "error_code": "case_execution_failed", "ended_at": datetime.now(timezone.utc).isoformat()})
        try:
            write_json(output / "meta.json", meta)
        except OSError:
            error.add_note("Case metadata write failed; primary failure preserved.")
        raise


def load_case(output: Path, project: Path | None = None) -> dict:
    project = (project or _project_root()).resolve()
    output = _confined(output, project / "data")
    metadata = json.loads((output / "meta.json").read_bytes())
    if output.is_relative_to(project / "data/portfolio_synthetic"):
        if metadata["mode"] != "synthetic":
            raise CaseError("case_mode_mismatch")
    else:
        if metadata["mode"] != "private" or output.parent.name != "analytics" or (output.parent.parent != project / "data" and not output.parent.parent.name.startswith("private_minimized_")):
            raise CaseError("case_mode_mismatch")
        key = load_local_key(project / ".secrets/local_minimized_v1.dpapi", create=False)
        metadata = verify_document(metadata, key)
    if metadata["status"] != "complete":
        raise CaseError("completed_case_required")
    expected = set()
    for item in metadata["artifacts"]:
        path = _confined(output / item["path"], output)
        if file_hash(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise CaseError("case_artifact_changed")
        expected.add(path.relative_to(output).as_posix())
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file() and p.name != "meta.json"}
    if actual != expected:
        raise CaseError("case_artifact_inventory_changed")
    return json.loads((output / "summary.json").read_bytes())
