"""Independently verify a completed local migration; persist aggregates only."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from functools import lru_cache
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
from typing import Any, Callable
import unicodedata

from totens.local_migration import (
    publish_new, reject_links, sign_document, verify_document,
)
from totens.local_secrets import load_local_key
from totens.privacy import validate_sanitized_payload


VERIFICATION_VERSION = "1.0.0"
_NAME = re.compile(r"ocorrencias?_\d{8}_a_\d{8}\.json\Z")
_PERSONAL_KEYS = frozenset({
    "nome", "nomecompleto", "nomemae", "nomepai", "mae", "pai", "cpf", "rg",
    "cnpj", "documento", "documentos", "telefone", "telefones", "celular",
    "email", "contato", "contatos", "endereco", "logradouro", "numero",
    "complemento", "cep", "bairro", "relatopolicial", "relato", "narrativa",
    "observacao", "observacoes", "motivoabordagem", "nascimento", "datanascimento", "operador", "usuario",
    "solicitante", "comunicante", "vitima", "vitimas", "envolvidos", "pessoas",
    "senha", "password", "token", "authorization",
})


class VerificationError(ValueError):
    """Only fixed local codes; never interpolate source values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise VerificationError(code)


@lru_cache(maxsize=512)
def _normalized_key(value: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", value)
                    if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", plain.casefold())


def check_personal_keys(value: Any, *, record_root: bool = False) -> None:
    """Descriptions remain allowed in reviewed categorical dictionaries."""
    if isinstance(value, dict):
        for field, child in value.items():
            _require(isinstance(field, str), "invalid_output_key")
            _require(_normalized_key(field) not in _PERSONAL_KEYS,
                     "personal_key_present")
            _require(not (record_root and field == "descricao"),
                     "narrative_key_present")
            check_personal_keys(child)
    elif isinstance(value, list):
        for child in value:
            check_personal_keys(child)


def independent_protocol(value: Any, key: bytes) -> tuple[bytes, bytes, str]:
    _require(not isinstance(value, bool) and isinstance(value, (str, int)),
             "invalid_source_protocol")
    normalized = str(value).strip()
    _require(bool(normalized) and len(normalized) <= 256,
             "invalid_source_protocol")
    source_identity = normalized.encode("utf-8")
    digest = hmac.new(key, b"totens-protocol-v1\0" + source_identity,
                      hashlib.sha256).digest()
    return source_identity, digest, "hmac-sha256:" + digest.hex()


def _same_datetime(source: Any, output: Any, counts: Counter, kind: str) -> None:
    _require(isinstance(source, str) and isinstance(output, str),
             "datetime_not_preserved")
    try:
        original = datetime.fromisoformat(source)
        minimized = datetime.fromisoformat(output)
    except (TypeError, ValueError, OverflowError):
        raise VerificationError("datetime_not_preserved") from None
    # Compare the instant AND the original offset/naive interpretation.
    _require(original == minimized and original.utcoffset() == minimized.utcoffset(),
             "datetime_not_preserved")
    counts[kind + "_datetimes_verified"] += 1


def _valid_coordinate(value: Any, limit: int) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and abs(number) <= limit else None


def verify_record(source: dict, output: dict, key: bytes,
                  counts: Counter | None = None) -> tuple[bytes, bytes]:
    """Compare one positional pair without using the migration projection."""
    if counts is None:
        counts = Counter()
    _require(isinstance(source, dict) and isinstance(output, dict),
             "invalid_record_structure")
    check_personal_keys(output, record_root=True)
    source_identity, digest, expected_token = independent_protocol(source.get("protocolo"), key)
    _require(output.get("protocolo") == expected_token, "protocol_not_preserved")
    counts["protocol_hmac_verified"] += 1
    _same_datetime(source.get("dataCriacao"), output.get("dataCriacao"), counts, "creation")

    source_location, output_location = source.get("localizacao"), output.get("localizacao")
    if source_location is None:
        _require(output_location is None, "unexpected_output_location")
        counts["source_locations_absent"] += 1
    else:
        _require(isinstance(source_location, dict) and isinstance(output_location, dict),
                 "invalid_location_structure")
        pair_valid = True
        for field, limit in (("latitude", 90), ("longitude", 180)):
            original = source_location.get(field)
            expected = _valid_coordinate(original, limit)
            if expected is None:
                pair_valid = False
                _require(field not in output_location, "invalid_coordinate_not_omitted")
                counts["null_coordinates_verified_absent" if original is None
                       else "invalid_coordinates_verified_absent"] += 1
            else:
                actual = output_location.get(field)
                _require(isinstance(actual, float) and actual == expected,
                         "coordinate_not_preserved")
                counts["valid_coordinates_preserved_exactly"] += 1
        if pair_valid:
            counts["valid_coordinate_pairs_preserved"] += 1

    source_closures, output_closures = source.get("dadosEncerramento"), output.get("dadosEncerramento")
    if source_closures is None:
        _require(output_closures is None, "closure_structure_not_preserved")
    else:
        _require(isinstance(source_closures, list) and isinstance(output_closures, list)
                 and len(source_closures) == len(output_closures),
                 "closure_structure_not_preserved")
        for original, minimized in zip(source_closures, output_closures, strict=True):
            _require(isinstance(original, dict) and isinstance(minimized, dict),
                     "closure_structure_not_preserved")
            if original.get("dataHoraFato") is None:
                _require("dataHoraFato" not in minimized, "unexpected_closure_datetime")
                counts["null_closure_datetimes_verified_absent"] += 1
            else:
                _same_datetime(original["dataHoraFato"], minimized.get("dataHoraFato"), counts, "closure")
            counts["closure_records_verified"] += 1
    counts["records"] += 1
    return source_identity, digest


def verify(source: Path, root: Path, key: bytes, *, category_catalog: dict,
           on_progress: Callable[[dict], None] | None = None) -> dict:
    """Read source/output snapshots and write one signed aggregate after success."""
    _require(isinstance(key, bytes) and len(key) >= 32, "invalid_verification_key")
    reject_links(source)
    reject_links(root)
    source, root = source.resolve(), root.resolve()
    _require(not (source == root or source.is_relative_to(root) or root.is_relative_to(source)),
             "source_destination_overlap")
    metadata, bronze = root / "metadata", root / "bronze" / "ocorrencia"
    for path in (metadata, bronze, metadata / "migration_summary.json",
                 metadata / "migration_identity.json", metadata / "migration_manifest.jsonl"):
        reject_links(path)
    migration = verify_document(json.loads((metadata / "migration_summary.json").read_bytes()), key)
    _require(migration.get("status") == "complete" and migration.get("dataset_private") is True
             and migration.get("originals_verified_unchanged") is True,
             "migration_not_complete")
    files = sorted(source.iterdir())
    _require(bool(files) and all(p.is_file() and _NAME.fullmatch(p.name) for p in files),
             "unexpected_source_files")
    names = [p.name for p in files]
    identity = verify_document(json.loads((metadata / "migration_identity.json").read_bytes()), key)
    _require(identity == {"identity": migration["identity"], "source_names": names},
             "migration_identity_mismatch")
    receipt_rows = []
    receipts = {}
    for line in (metadata / "migration_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        row = verify_document(json.loads(line), key)
        name = row.get("source_name")
        _require(isinstance(name, str) and name in names and name not in receipts,
                 "receipt_source_set_mismatch")
        _require(row.get("identity") == migration["identity"], "receipt_identity_mismatch")
        receipts[name] = row
        receipt_rows.append(row)
    _require(set(receipts) == set(names) and migration.get("source_files") == len(files),
             "receipt_source_set_mismatch")
    _require(set(p.name for p in bronze.iterdir()) == set(names), "output_source_set_mismatch")
    # Completion binds the exact ordered receipt list, not just each row's MAC.
    encoded_receipts = json.dumps(receipt_rows, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8")
    _require(hashlib.sha256(encoded_receipts).hexdigest() == migration.get("source_receipts_sha256"),
             "completion_receipts_mismatch")
    counts = Counter()
    # Only held in memory: no source identity, token or example is persisted.
    token_sources: dict[bytes, bytes] = {}
    duplicated_protocols: set[bytes] = set()
    for index, path in enumerate(files, 1):
        reject_links(path)
        target = bronze / path.name
        reject_links(target)
        receipt = receipts[path.name]
        source_bytes, output_bytes = path.read_bytes(), target.read_bytes()
        _require(hashlib.sha256(source_bytes).hexdigest() == receipt.get("source_sha256")
                 and len(source_bytes) == receipt.get("source_bytes"), "source_hash_mismatch")
        _require(hashlib.sha256(output_bytes).hexdigest() == receipt.get("output_sha256"),
                 "output_hash_mismatch")
        source_records = json.loads(source_bytes)
        output_payload = json.loads(output_bytes)
        counts["source_bytes_verified"] += len(source_bytes)
        del source_bytes, output_bytes
        _require(isinstance(source_records, list), "unsupported_source_envelope")
        validate_sanitized_payload(output_payload, key, category_catalog=category_catalog)
        output_identity = {k: v for k, v in output_payload["_privacy"].items() if k != "payload_hmac"}
        expected_identity = {k: v for k, v in migration["identity"].items() if k != "migration_version"}
        _require(output_identity == expected_identity, "output_identity_mismatch")
        output_records = output_payload["params"]
        _require(len(source_records) == len(output_records)
                 and len(source_records) == receipt.get("counts", {}).get("records"),
                 "record_count_mismatch")
        file_seen: set[bytes] = set()
        file_duplicate_count = 0
        for original, minimized in zip(source_records, output_records, strict=True):
            source_identity, token = verify_record(original, minimized, key, counts)
            previous_source = token_sources.get(token)
            if previous_source is None:
                token_sources[token] = source_identity
            else:
                _require(previous_source == source_identity, "observed_hmac_collision")
                counts["duplicate_record_excess_global"] += 1
                duplicated_protocols.add(token)
            if source_identity in file_seen:
                file_duplicate_count += 1
            else:
                file_seen.add(source_identity)
        counts["duplicate_record_excess_within_files"] += file_duplicate_count
        counts["files_with_internal_duplicates"] += bool(file_duplicate_count)
        counts["files_verified"] += 1
        del source_records, output_payload, output_records, file_seen
        if on_progress:
            on_progress({"files_verified": index, "files_total": len(files), "records_verified": counts["records"]})
    _require(counts["records"] == migration.get("counts", {}).get("records"),
             "completion_record_count_mismatch")
    _require(counts["source_bytes_verified"] == migration.get("source_total_bytes"),
             "completion_source_bytes_mismatch")
    counts["unique_canonical_protocols"] = len(token_sources)
    counts["distinct_protocols_with_duplicates"] = len(duplicated_protocols)
    counts["observed_hmac_collisions"] = 0
    counts["personal_keys_found"] = 0
    summary = {
        "status": "complete", "verification_version": VERIFICATION_VERSION,
        "identity": migration["identity"], "counts": dict(counts),
        "source_receipts_sha256": migration["source_receipts_sha256"],
        "conclusions": {
            "source_and_output_hashes_match_signed_receipts": True,
            "record_counts_preserved": True,
            "positional_protocol_date_coordinate_correspondence_verified": True,
            "protocol_hmac_independently_verified": True,
            "creation_and_closure_datetimes_preserved": True,
            "valid_coordinates_preserved_without_rounding": True,
            "invalid_or_null_coordinates_absent": True,
            "authenticated_schema_and_catalog_verified": True,
            "known_personal_keys_absent_recursively": True,
            "source_and_bronze_read_only": True,
            "precise_coordinates_remain_private": True,
        },
    }
    publish_new(metadata / "verification_summary.json", sign_document(summary, key))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/bronze/ocorrencia"))
    parser.add_argument("--root", type=Path, default=Path("data/private_minimized_v1"))
    parser.add_argument("--key-file", type=Path, default=Path(".secrets/local_minimized_v1.dpapi"))
    parser.add_argument("--catalog", type=Path, help="Defaults to <root>/metadata/privacy_catalog.json.")
    args = parser.parse_args(argv)
    try:
        key = load_local_key(args.key_file, create=False)
        catalog_path = args.catalog or args.root / "metadata" / "privacy_catalog.json"
        reject_links(catalog_path)
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        def progress(value: dict) -> None:
            if value["files_verified"] % 25 == 0 or value["files_verified"] == value["files_total"]:
                print(json.dumps(value), flush=True)
        summary = verify(args.source, args.root, key, category_catalog=catalog,
                         on_progress=progress)
        print(json.dumps({"status": summary["status"], "counts": summary["counts"]}), flush=True)
        return 0
    except VerificationError as error:
        print(json.dumps({"status": "blocked", "code": error.code}), flush=True)
    except Exception:
        print(json.dumps({"status": "blocked", "code": "verification_failed"}), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
