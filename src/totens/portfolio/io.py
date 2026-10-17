"""Authenticated inputs and private-only ledgers; never persist raw identifiers."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from totens.local_migration import json_bytes, reject_links, verify_document
from totens.privacy import _catalog, validate_sanitized_payload
from totens.portfolio.data import project_records, window_days


def file_hash(path: Path) -> str:
    reject_links(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_manifest(root: Path, key: bytes) -> tuple[dict, dict, dict]:
    metadata = root / "metadata"
    paths = [metadata / name for name in ("migration_summary.json", "migration_identity.json", "migration_manifest.jsonl", "privacy_catalog.json")]
    for path in paths:
        reject_links(path)
    summary = verify_document(json.loads(paths[0].read_bytes()), key)
    identity = verify_document(json.loads(paths[1].read_bytes()), key)
    if summary.get("status") != "complete" or identity["identity"] != summary["identity"]:
        raise ValueError("incomplete_migration")
    rows = [verify_document(json.loads(line), key) for line in paths[2].read_text(encoding="utf-8").splitlines()]
    receipts = {row["source_name"]: row for row in rows}
    actual = sorted(p.name for p in (root / "bronze/ocorrencia").iterdir())
    if (len(receipts) != len(rows) or actual != identity["source_names"] or actual != sorted(receipts)
            or len(actual) != summary["source_files"] or any(row["identity"] != summary["identity"] for row in rows)
            or hashlib.sha256(json_bytes(rows)).hexdigest() != summary["source_receipts_sha256"]):
        raise ValueError("migration_manifest_mismatch")
    return receipts, json.loads(paths[3].read_bytes()), summary


def private_ledger(root: Path, destination: Path, key: bytes, progress=None) -> tuple[list[Path], set, dict, dict[str, str]]:
    receipts, catalog, summary = load_manifest(root, key)
    reviewed = set(_catalog(catalog)["nivelRisco"])
    destination.mkdir()
    input_hashes = {str(root / "metadata" / name): file_hash(root / "metadata" / name)
                    for name in ("migration_summary.json", "migration_identity.json", "migration_manifest.jsonl", "privacy_catalog.json")}
    outputs, rows = [], 0
    for index, (name, receipt) in enumerate(sorted(receipts.items()), 1):
        path = root / "bronze/ocorrencia" / name
        reject_links(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != receipt["output_sha256"]:
            raise ValueError("bronze_hash_mismatch")
        payload = validate_sanitized_payload(json.loads(raw), key, category_catalog=catalog)
        privacy_identity = {k: v for k, v in payload["_privacy"].items() if k != "payload_hmac"}
        expected_identity = {k: v for k, v in summary["identity"].items() if k != "migration_version"}
        if privacy_identity != expected_identity or len(payload["params"]) != receipt["counts"]["records"]:
            raise ValueError("bronze_identity_mismatch")
        frame = project_records(payload["params"], name, reviewed)
        output = destination / f"part_{index:04d}.parquet"
        frame.to_parquet(output, index=False, compression="zstd")
        outputs.append(output)
        input_hashes[str(path)] = digest
        rows += len(frame)
        if progress and (index % 25 == 0 or index == len(receipts)):
            progress({"event": "bronze_projected", "files": index, "rows": rows})
    if rows != summary["counts"]["records"]:
        raise ValueError("bronze_count_mismatch")
    return outputs, window_days(sorted(receipts)), {
        "authenticated_migration": True, "source_files": len(receipts), "source_rows": rows,
        "migration_summary_sha256": file_hash(root / "metadata/migration_summary.json"),
        "risk_mapping": {label: _risk_for_label(label, reviewed) for label in sorted(reviewed) if not label.startswith("category-hmac")},
    }, input_hashes


def _risk_for_label(label, reviewed):
    from totens.portfolio.data import source_risk
    return source_risk(label, reviewed)


def authenticated_totems(root: Path, key: bytes) -> tuple[pd.DataFrame, dict[str, str]]:
    path = root / "metadata/rebuild/receipt.json"
    reject_links(path)
    receipt = json.loads(path.read_bytes())
    signature = receipt.pop("receipt_hmac", None)
    expected = hmac.new(key, b"totens-rebuild-receipt-v1\0" + json_bytes(receipt), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected) or receipt["status"] != "complete":
        raise ValueError("invalid_rebuild_receipt")
    relative = "silver/cadastros/cadastro_totens.parquet"
    item = next(entry for entry in receipt["outputs"] if entry["path"] == relative)
    target = root / relative
    digest = file_hash(target)
    if digest != item["sha256"] or target.stat().st_size != item["bytes"]:
        raise ValueError("totem_hash_mismatch")
    frame = pq.ParquetFile(target).read(columns=["id_unidade", "latitude", "longitude"]).to_pandas()
    if len(frame) != receipt["auxiliary"]["totems"]["retained_rows"]:
        raise ValueError("totem_count_mismatch")
    return frame, {str(target): digest, str(path): file_hash(path)}


def archived_legacy(root: Path, key: bytes, source_info: dict) -> tuple[dict, dict[str, str]]:
    """Authenticate past reconciliation without reading complete legacy data."""
    path = root / "metadata/legacy_reconciliation.json"
    reject_links(path)
    body = verify_document(json.loads(path.read_bytes()), key)
    if (body.get("status") != "complete" or body["source_summary_sha256"] != source_info["migration_summary_sha256"]
            or body["reconciliation"]["current_rows"] != source_info["source_rows"]):
        raise ValueError("historical_reconciliation_source_mismatch")
    return {**body["reconciliation"], "evidence_mode": "authenticated_historical_not_recalculated",
            "original_run": body["original_run"]}, {str(path): file_hash(path)}


def _legacy_token(value, key: bytes) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 256:
        return None
    if re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", normalized):
        return normalized
    return "hmac-sha256:" + hmac.new(key, b"totens-protocol-v1\0" + normalized.encode(), hashlib.sha256).hexdigest()


def reconcile_legacy(project: Path, ledgers: list[Path], destination: Path, key: bytes, progress=None) -> tuple[dict, dict[str, str]]:
    """Read only two identifying columns plus holiday labels; hash before disk."""
    folder = project / "data/gold/analitica/f_ocorrencia"
    reject_links(folder)
    files = sorted(folder.rglob("*.parquet"))
    if not files:
        return {"status": "unavailable", "reason": "legacy_gold_missing"}, {}
    destination.mkdir()
    staged, hashes, unmappable, total = [], {}, 0, 0
    for index, file in enumerate(files, 1):
        hashes[str(file)] = file_hash(file)
        parquet = pq.ParquetFile(file)
        columns = ["protocolo", "dataCriacao"] + [c for c in ("nome_feriado", "tipo_feriado") if c in parquet.schema_arrow.names]
        if not {"protocolo", "dataCriacao"}.issubset(parquet.schema_arrow.names):
            raise ValueError("legacy_schema_unreconcilable")
        for batch_index, batch in enumerate(parquet.iter_batches(columns=columns, batch_size=65536)):
            frame = batch.to_pandas()
            total += len(frame)
            tokens = [_legacy_token(value, key) for value in frame.protocolo.tolist()]
            unmappable += sum(value is None for value in tokens)
            holidays = [hashlib.sha256(json_bytes([None if pd.isna(row.get(c)) else str(row[c])
                        for c in ("nome_feriado", "tipo_feriado")])).hexdigest() for row in frame.to_dict("records")]
            safe = pd.DataFrame({"protocolo": tokens,
                "timestamp_utc": pd.to_datetime(frame.dataCriacao, utc=True, errors="coerce"), "holiday_hash": holidays})
            safe = safe[safe.protocolo.notna()]
            target = destination / f"part_{index:04d}_{batch_index:03d}.parquet"
            safe.to_parquet(target, index=False, compression="zstd")
            staged.append(target)
        if progress and index % 75 == 0:
            progress({"event": "legacy_reconciled", "files": index, "rows": total})
    with duckdb.connect() as connection:
        connection.execute("SET threads=2")
        connection.read_parquet([str(p) for p in ledgers]).create_view("current_rows")
        connection.read_parquet([str(p) for p in staged]).create_view("old_rows")
        result = connection.sql("""
            WITH current_counts AS (SELECT protocolo, timestamp_utc, count(*) n FROM current_rows GROUP BY ALL),
            old_counts AS (SELECT protocolo, timestamp_utc, count(*) n, count(DISTINCT holiday_hash) holidays FROM old_rows GROUP BY protocolo,timestamp_utc),
            joined AS (SELECT coalesce(a.n,0) current_n, coalesce(b.n,0) old_n, coalesce(b.holidays,0) holidays
              FROM current_counts a FULL JOIN old_counts b ON a.protocolo=b.protocolo AND a.timestamp_utc IS NOT DISTINCT FROM b.timestamp_utc)
            SELECT sum(current_n) current_rows, sum(old_n) legacy_mapped_rows,
              sum(greatest(old_n-current_n,0)) legacy_surplus_rows,
              sum(greatest(current_n-old_n,0)) current_surplus_rows,
              sum(CASE WHEN current_n>0 THEN greatest(old_n-current_n,0) ELSE 0 END) excess_multiplicity_same_key_date,
              sum(CASE WHEN current_n>0 AND holidays>1 THEN greatest(old_n-current_n,0) ELSE 0 END) surplus_with_different_holiday_labels,
              sum(CASE WHEN current_n=0 THEN old_n ELSE 0 END) legacy_only_key_date_rows
            FROM joined
        """).df().iloc[0].to_dict()
    summary = {k: int(v) for k, v in result.items()}
    summary.update({"status": "complete", "legacy_rows": total, "legacy_unmappable_rows": unmappable,
                    "net_difference": total - summary["current_rows"],
                    "interpretation": "Multiplicity by pseudonym and UTC instant; holiday association is not proof of historical cause."})
    assert summary["legacy_surplus_rows"] - summary["current_surplus_rows"] + unmappable == summary["net_difference"]
    return summary, hashes
