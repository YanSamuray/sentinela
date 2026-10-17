"""Independently verify minimized Gold; emit aggregates only, never row values."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import hmac
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from totens.local_migration import json_bytes, publish_new, reject_links, sign_document
from totens.local_secrets import load_local_key

EARTH_RADIUS = 6_371_000.0
RADIUS = 300.0
TOLERANCE = 1e-4
BASE_COLUMNS = ["protocolo", "dataCriacao", "localizacao_latitude", "localizacao_longitude"]
GOLD_COLUMNS = [*BASE_COLUMNS, "distancia_totem_m", "id_totem_prox", "flag_dentro_raio"]


def _files(folder: Path) -> list[Path]:
    reject_links(folder)
    files = sorted(folder.rglob("*.parquet"))
    if not files:
        raise ValueError("missing_verification_input")
    for path in files:
        reject_links(path)
    return files


def _batches(files: list[Path], columns: list[str]):
    for path in files:
        yield from pq.ParquetFile(path).iter_batches(columns=columns, batch_size=16384)


def _float(value):
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def _tuples(batch):
    values = [batch.column(name).to_pylist() for name in BASE_COLUMNS]
    dates = pd.to_datetime(values[1], utc=True, errors="raise").asi8
    missing_date = np.iinfo(np.int64).min
    return (
        (key, None if timestamp == missing_date else int(timestamp), _float(lat), _float(lon))
        for key, timestamp, lat, lon in zip(values[0], dates, values[2], values[3])
    )


def _haversine(latitudes: np.ndarray, longitudes: np.ndarray, totems: np.ndarray) -> np.ndarray:
    """Independent vectorized great-circle calculation against every totem."""
    lat = np.radians(latitudes)[:, None]
    lon = np.radians(longitudes)[:, None]
    target_lat = np.radians(totems[:, 0])[None, :]
    target_lon = np.radians(totems[:, 1])[None, :]
    term = np.sin((target_lat - lat) / 2) ** 2
    term += np.cos(lat) * np.cos(target_lat) * np.sin((target_lon - lon) / 2) ** 2
    return 2 * EARTH_RADIUS * np.arcsin(np.sqrt(np.clip(term, 0, 1)))


def audit_gold(root: Path, *, expected_totems: int | None = None, radius_meters: float = RADIUS) -> dict:
    """Read minimized inputs only; no key access and no filesystem writes."""
    if (isinstance(radius_meters, bool) or not isinstance(radius_meters, (int, float))
            or not math.isfinite(radius_meters) or radius_meters <= 0):
        raise ValueError("invalid_verification_radius")
    root = Path(root)
    reject_links(root)
    silver_files = _files(root / "silver/normalized/f_ocorrencia")
    gold_files = _files(root / "gold/analitica/f_ocorrencia")
    totem_path = root / "silver/cadastros/cadastro_totens.parquet"
    reject_links(totem_path)
    totem_table = pq.ParquetFile(totem_path).read(columns=["id_unidade", "latitude", "longitude"]).to_pandas()
    totem_points = totem_table[["latitude", "longitude"]].to_numpy(dtype=float)
    totem_ids = totem_table["id_unidade"].tolist()
    if (not len(totem_points) or len(set(totem_ids)) != len(totem_ids)
            or any(value is None for value in totem_ids) or not np.isfinite(totem_points).all()
            or (np.abs(totem_points[:, 0]) > 90).any() or (np.abs(totem_points[:, 1]) > 180).any()):
        raise ValueError("invalid_minimized_totems")
    id_to_index = {value: index for index, value in enumerate(totem_ids)}
    remaining = Counter()
    silver_rows = 0
    for batch in _batches(silver_files, BASE_COLUMNS):
        remaining.update(_tuples(batch))
        silver_rows += batch.num_rows

    counts = Counter()
    max_error = 0.0
    for batch in _batches(gold_files, GOLD_COLUMNS):
        remaining.subtract(_tuples(batch))
        counts["gold_rows"] += batch.num_rows
        lat = np.asarray(batch.column("localizacao_latitude").to_pylist(), dtype=float)
        lon = np.asarray(batch.column("localizacao_longitude").to_pylist(), dtype=float)
        distances = np.asarray(batch.column("distancia_totem_m").to_pylist(), dtype=float)
        nearest = batch.column("id_totem_prox").to_pylist()
        flags = batch.column("flag_dentro_raio").to_pylist()
        valid = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
        counts["rows_with_valid_coordinates"] += int(valid.sum())
        counts["rows_without_valid_coordinates"] += int((~valid).sum())
        counts["null_distances"] += int(np.isnan(distances).sum())
        counts["infinite_distances"] += int(np.isinf(distances).sum())
        counts["null_radius_flags"] += sum(flag is None for flag in flags)
        counts["null_nearest_ids"] += sum(value is None for value in nearest)
        # Invalid/missing coordinates must have no inferred distance, ID or flag.
        for position in np.flatnonzero(~valid):
            counts["missing_coordinate_output_errors"] += int(
                not np.isnan(distances[position]) or nearest[position] is not None or flags[position] is not None
            )
        if not valid.any():
            continue
        calculated = _haversine(lat[valid], lon[valid], totem_points)
        minima = calculated.min(axis=1)
        observed = distances[valid]
        finite = np.isfinite(observed)
        errors = np.abs(observed[finite] - minima[finite])
        if errors.size:
            max_error = max(max_error, float(errors.max()))
        counts["distance_errors"] += int((~finite).sum() + (errors > TOLERANCE).sum())
        for offset, position in enumerate(np.flatnonzero(valid)):
            chosen = id_to_index.get(nearest[position])
            counts["nearest_id_errors"] += int(
                chosen is None or calculated[offset, chosen] > minima[offset] + TOLERANCE
            )
            flag = flags[position]
            # Radius flag follows persisted distance exactly; its value is checked above.
            expected_flag = bool(observed[offset] <= radius_meters) if finite[offset] else None
            counts["radius_flag_errors"] += int(
                flag is None if expected_flag is not None else flag is not None
            )
            if flag is not None and expected_flag is not None:
                counts["radius_flag_errors"] += int(not isinstance(flag, bool) or flag != expected_flag)

    source_only = sum(value for value in remaining.values() if value > 0)
    gold_only = -sum(value for value in remaining.values() if value < 0)
    error_fields = ("infinite_distances", "missing_coordinate_output_errors", "distance_errors",
                    "nearest_id_errors", "radius_flag_errors")
    error_counts = {name: counts[name] for name in error_fields}
    totem_count_matches = expected_totems is None or len(totem_ids) == expected_totems
    passed = not (source_only or gold_only or any(error_counts.values())) and totem_count_matches
    return {
        "status": "complete" if passed else "failed",
        "verification_version": "1.1.0",
        "silver_files": len(silver_files), "gold_files": len(gold_files),
        "silver_rows": silver_rows, "gold_rows": counts["gold_rows"],
        "totems": len(totem_ids), "expected_totem_count_matches": totem_count_matches,
        "exact_occurrence_multiset_matches": not (source_only or gold_only),
        "source_only_occurrences": source_only, "gold_only_occurrences": gold_only,
        "earth_radius_meters": EARTH_RADIUS, "radius_meters": float(radius_meters),
        "distance_absolute_tolerance_meters": TOLERANCE, "maximum_distance_error_meters": max_error,
        "rows_with_valid_coordinates": counts["rows_with_valid_coordinates"],
        "rows_without_valid_coordinates": counts["rows_without_valid_coordinates"],
        "null_distances": counts["null_distances"], "null_radius_flags": counts["null_radius_flags"],
        "null_nearest_ids": counts["null_nearest_ids"], "errors": error_counts,
        "datetime_comparison": "UTC nanoseconds; naive timestamps interpreted as UTC; missing values normalized",
    }


def verify_rebuild_inventory(root: Path, receipt: dict) -> int:
    """Compare all current output bytes with the authenticated runner inventory."""
    root = Path(root)
    reject_links(root)
    expected = receipt.get("outputs")
    if not isinstance(expected, list) or not expected:
        raise ValueError("invalid_rebuild_inventory")
    expected_by_path = {}
    for entry in expected:
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}
                or not isinstance(entry["path"], str)
                or not isinstance(entry["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
                or isinstance(entry["bytes"], bool) or not isinstance(entry["bytes"], int)
                or entry["bytes"] < 0 or entry["path"] in expected_by_path):
            raise ValueError("invalid_rebuild_inventory")
        expected_by_path[entry["path"]] = entry
    actual_by_path = {}
    excluded = {"metadata/rebuild/receipt.json", "metadata/rebuild/receipt.json.tmp"}
    for relative in ("silver", "gold", "metadata/rebuild"):
        folder = root / relative
        reject_links(folder)
        if not folder.is_dir():
            raise ValueError("missing_rebuild_output")
        for path in sorted(folder.rglob("*")):
            reject_links(path)
            name = path.relative_to(root).as_posix()
            if not path.is_file() or name in excluded:
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            actual_by_path[name] = {"path": name, "sha256": digest, "bytes": path.stat().st_size}
    if actual_by_path != expected_by_path:
        raise ValueError("rebuild_output_hash_mismatch")
    return len(actual_by_path)


def verify_completed_gold(root: Path, key: bytes) -> dict:
    """Authenticate the completed snapshot before and after the independent audit."""
    root = Path(root)
    reject_links(root)
    receipt_path = root / "metadata/rebuild/receipt.json"
    reject_links(receipt_path)
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    if not isinstance(receipt, dict):
        raise ValueError("completed_rebuild_required")
    signature = receipt.pop("receipt_hmac", None)
    expected = hmac.new(key, b"totens-rebuild-receipt-v1\0" + json_bytes(receipt), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected) or receipt.get("status") != "complete":
        raise ValueError("completed_rebuild_required")
    verified_files = verify_rebuild_inventory(root, receipt)
    summary = audit_gold(root, expected_totems=receipt["auxiliary"]["totems"]["retained_rows"],
                         radius_meters=receipt["request"]["radius_meters"])
    # An edit during the audit cannot receive an attestation for the old snapshot.
    verify_rebuild_inventory(root, receipt)
    if receipt_path.read_bytes() != receipt_bytes:
        raise ValueError("rebuild_receipt_changed")
    summary["rebuild_receipt_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
    summary["authenticated_output_inventory"] = True
    summary["verified_output_files"] = verified_files
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--key-file", type=Path, default=Path(".secrets/local_minimized_v1.dpapi"))
    parser.add_argument("--report", type=Path, help="New signed report; existing reports are never overwritten.")
    args = parser.parse_args(argv)
    try:
        reject_links(args.root)
        report = args.report or args.root / "metadata/gold_verification_summary.json"
        reject_links(report)
        if any(report.resolve().is_relative_to((args.root / folder).resolve())
               for folder in ("bronze", "silver", "gold", "metadata/rebuild")):
            raise ValueError("report_inside_dataset")
        key = load_local_key(args.key_file, create=False)
        summary = verify_completed_gold(args.root, key)
        publish_new(report, sign_document(summary, key))
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["status"] == "complete" else 1
    except Exception:
        print(json.dumps({"status": "blocked", "code": "gold_verification_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
