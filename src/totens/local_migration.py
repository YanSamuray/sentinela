"""Offline minimization into an isolated lake; never mutate source files.

Legacy adaptations are deliberately narrower than the API contract. Only
reviewed descriptive categories survive; institution IDs/names, narratives,
addresses and every unlisted field are omitted. Non-boolean risk responses are
missing rather than false. All adaptation counts are recorded without values.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from totens.privacy import PrivacyError, sanitize_payload, validate_sanitized_payload

MIGRATION_VERSION = "1.1.0"
_NAME = re.compile(r"ocorrencias?_\d{8}_a_\d{8}\.json\Z")
_SCALARS = ("nivelRisco", "classificacaoSituacao", "origemRegistro")
_DESCRIPTORS = ("grupo", "codigoEncerramento", "fatoComunicado")
_LISTS = ("unidades", "areasDespacho", "centraisAtendimento", "fatoConstatado")
_BOOLS = ("houveDisparoArmaFogo", "houveUtilizacaoForcaFisica",
          "policiaCivilAcionada", "policiaCivilEsteveLocal")
_QUESTIONS = {"Há pessoas feridas em risco de morte?", "Autor do fato está armado?"}
_TRUE = {"sim", "true", "1", "s"}
_FALSE = {"não", "nao", "false", "0", "n"}
_CATEGORY_PREFIX = "category-hmac-sha256:"


class MigrationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_document(value: dict, key: bytes) -> dict:
    if "signature" in value:
        raise MigrationError("unexpected_signature")
    signature = hmac.new(key, b"totens-local-migration-v1\0" + json_bytes(value), hashlib.sha256).hexdigest()
    return {**value, "signature": signature}


def verify_document(value: dict, key: bytes) -> dict:
    if not isinstance(value, dict):
        raise MigrationError("invalid_receipt")
    body = {k: v for k, v in value.items() if k != "signature"}
    sig = value.get("signature")
    if not isinstance(sig, str) or not hmac.compare_digest(sig, sign_document(body, key)["signature"]):
        raise MigrationError("invalid_receipt")
    return body


def reject_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise MigrationError("linked_path_forbidden")


def publish_new(path: Path, value: Any) -> str:
    """Only minimized/safe values may reach this exclusive-write helper."""
    reject_links(path)
    payload = json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if path.read_bytes() == payload:
            return digest
        raise MigrationError("destination_exists_with_different_content")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".minimized-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        if value.casefold() in _TRUE:
            return True
        if value.casefold() in _FALSE:
            return False
    return None


def _descriptor(value: Any) -> dict:
    if not isinstance(value, dict):
        raise MigrationError("invalid_legacy_descriptor")
    result = {}
    if value.get("descricao") is not None:
        result["descricao"] = value["descricao"]
    if value.get("grupo") is not None:
        if isinstance(value["grupo"], dict):
            result["grupo"] = _descriptor(value["grupo"])
        else:
            result["grupo"] = value["grupo"]
    return result


def _records(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("params"), list):
        payload = payload["params"]
    if not isinstance(payload, list) or any(not isinstance(v, dict) for v in payload):
        raise MigrationError("unsupported_legacy_envelope")
    return payload


def _category_slots(record: dict):
    """Only slots that the offline projection actually preserves."""
    for field in _SCALARS:
        if record.get(field) is not None:
            yield record, field, field
    location = record.get("localizacao")
    if isinstance(location, dict) and location.get("municipio") is not None:
        yield location, "municipio", "localizacao.municipio"
    def descriptor(value: Any, path: str):
        if not isinstance(value, dict):
            raise MigrationError("invalid_legacy_descriptor")
        if value.get("descricao") is not None:
            yield value, "descricao", path + ".descricao"
        group = value.get("grupo")
        if isinstance(group, dict):
            if group.get("descricao") is not None:
                yield group, "descricao", path + ".grupo.descricao"
        elif group is not None:
            yield value, "grupo", path + ".grupo"
    for field in _DESCRIPTORS:
        if record.get(field) is not None:
            yield from descriptor(record[field], field)
    for field in _LISTS:
        if record.get(field) is not None:
            if not isinstance(record[field], list):
                raise MigrationError("invalid_legacy_list")
            for item in record[field]:
                yield from descriptor(item, field + "[]")


def category_token(value: Any, path: str, key: bytes) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MigrationError("invalid_legacy_category")
    # The same nature/group has the same pseudonym whether communicated or
    # confirmed, preserving cross-column comparisons without a cleartext map.
    family = path
    if path in ("fatoComunicado.descricao", "fatoConstatado[].descricao"):
        family = "natureza.descricao"
    elif path in ("grupo.descricao", "fatoComunicado.grupo", "fatoConstatado[].grupo"):
        family = "grupo.descricao"
    token = hmac.new(key, b"totens-category-v1\0" + family.encode() + b"\0" + str(value).encode("utf-8"), hashlib.sha256)
    return _CATEGORY_PREFIX + token.hexdigest()


def compile_catalog(source: Path, key: bytes, *, reviewed_catalog: dict,
                    on_progress: Callable[[dict], None] | None = None) -> dict:
    """Compile reviewed labels + pseudonyms, NEVER approve raw unknown labels."""
    sanitize_payload({"params": []}, key, category_catalog=reviewed_catalog)
    approved = {path: set(values) for path, values in reviewed_catalog.items()}
    catalog = {path: set(values) for path, values in reviewed_catalog.items()}
    reject_links(source)
    files = sorted(source.iterdir())
    if not files or any(not p.is_file() or not _NAME.fullmatch(p.name) for p in files):
        raise MigrationError("unexpected_source_files")
    for index, path in enumerate(files, 1):
        reject_links(path)
        payload = json.loads(path.read_bytes())
        for record in _records(payload):
            for container, field, slot in _category_slots(record):
                value = container[field]
                if isinstance(value, str) and not value.startswith(_CATEGORY_PREFIX) and value in approved.get(slot, set()):
                    continue
                catalog.setdefault(slot, set()).add(category_token(value, slot, key))
        del payload
        if on_progress is not None:
            on_progress({"catalog_files_done": index, "files_total": len(files)})
    result = {path: sorted(values) for path, values in sorted(catalog.items())}
    sanitize_payload({"params": []}, key, category_catalog=result)
    return result


def _legacy_datetime(value: Any, stats: Counter, kind: str) -> Any:
    # Historical ISO timestamps use -0400 rather than -04:00. Only change that
    # explicit syntax; the common sanitizer still validates the date and offset.
    if isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?[+-]\d{4}", value
    ):
        stats[f"{kind}_compact_offsets_normalized"] += 1
        return value[:-2] + ":" + value[-2:]
    return value


def sanitize_legacy_payload(payload: Any, key: bytes, *, category_catalog: dict) -> tuple[dict, dict]:
    """Apply explicit legacy projection, then the authenticated privacy policy."""
    records = _records(payload)
    approved = {path: set(values) for path, values in category_catalog.items()}
    stats = Counter(records=len(records))
    projected = []
    for record in records:
        out = {k: record.get(k) for k in ("protocolo", "dataCriacao")}
        out["dataCriacao"] = _legacy_datetime(out["dataCriacao"], stats, "creation")
        for field in _SCALARS:
            if record.get(field) is not None:
                out[field] = record[field]
        for field in _DESCRIPTORS:
            if record.get(field) is not None:
                out[field] = _descriptor(record[field])
        for field in _LISTS:
            if record.get(field) is not None:
                if not isinstance(record[field], list):
                    raise MigrationError("invalid_legacy_list")
                out[field] = [_descriptor(v) for v in record[field]]
        if record.get("localizacao") is not None:
            loc = record["localizacao"]
            if not isinstance(loc, dict):
                raise MigrationError("invalid_legacy_location")
            clean_loc = {}
            valid_pair = True
            for field, limit in (("latitude", 90), ("longitude", 180)):
                value = loc.get(field)
                if value is None:
                    valid_pair = False
                    stats["coordinates_missing"] += 1
                    continue
                try:
                    number = float(value) if not isinstance(value, bool) else math.nan
                except (ValueError, TypeError, OverflowError):
                    number = math.nan
                if math.isfinite(number) and abs(number) <= limit:
                    clean_loc[field] = number
                else:
                    valid_pair = False
                    stats["coordinates_invalid_omitted"] += 1
            if valid_pair:
                stats["coordinate_pairs_preserved"] += 1
            if loc.get("municipio") is not None:
                clean_loc["municipio"] = loc["municipio"]
            out["localizacao"] = clean_loc
        if record.get("dadosEncerramento") is not None:
            if not isinstance(record["dadosEncerramento"], list):
                raise MigrationError("invalid_legacy_closure")
            out["dadosEncerramento"] = []
            for item in record["dadosEncerramento"]:
                if not isinstance(item, dict):
                    raise MigrationError("invalid_legacy_closure")
                clean = {}
                if item.get("dataHoraFato") is not None:
                    clean["dataHoraFato"] = _legacy_datetime(item["dataHoraFato"], stats, "closure")
                for field in _BOOLS:
                    if item.get(field) is not None:
                        value = _boolean(item[field])
                        if value is None:
                            stats["closure_non_boolean_omitted"] += 1
                        else:
                            clean[field] = value
                out["dadosEncerramento"].append(clean)
        if record.get("perguntasNivelRisco") is not None:
            if not isinstance(record["perguntasNivelRisco"], list):
                raise MigrationError("invalid_legacy_questions")
            out["perguntasNivelRisco"] = []
            for item in record["perguntasNivelRisco"]:
                if not isinstance(item, dict):
                    raise MigrationError("invalid_legacy_question")
                question = item.get("pergunta")
                if question not in _QUESTIONS:
                    stats["risk_questions_not_approved_omitted"] += 1
                    continue
                value = _boolean(item.get("resposta"))
                if value is None:
                    stats["risk_answers_non_boolean_omitted"] += 1
                    continue
                out["perguntasNivelRisco"].append({"pergunta": question, "resposta": value})
        if isinstance(out["dataCriacao"], str) and not re.search(r"Z$|[+-]\d\d:\d\d$", out["dataCriacao"]):
            stats["creation_dates_without_timezone"] += 1
        for container, field, slot in _category_slots(out):
            value = container[field]
            if isinstance(value, str) and not value.startswith(_CATEGORY_PREFIX) and value in approved.get(slot, set()):
                continue
            container[field] = category_token(value, slot, key)
            stats["categories_pseudonymized:" + slot] += 1
        projected.append(out)
    sanitized = sanitize_payload({"params": projected}, key, category_catalog=category_catalog)
    validate_sanitized_payload(sanitized, key, category_catalog=category_catalog)
    return sanitized, dict(stats)


def migrate(source: Path, destination: Path, key: bytes, *, category_catalog: dict,
            on_progress: Callable[[dict], None] | None = None) -> dict:
    """Resume only receipts with matching source bytes, identity and outputs."""
    reject_links(source)
    reject_links(destination)
    source, destination = source.resolve(), destination.resolve()
    if source == destination or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise MigrationError("source_destination_overlap")
    probe = sanitize_payload({"params": []}, key, category_catalog=category_catalog)["_privacy"]
    identity = {k: v for k, v in probe.items() if k != "payload_hmac"}
    identity["migration_version"] = MIGRATION_VERSION
    files = sorted(source.iterdir())
    if not files or any(not p.is_file() or not _NAME.fullmatch(p.name) for p in files):
        raise MigrationError("unexpected_source_files")
    for path in files:
        reject_links(path)
    bronze = destination / "bronze" / "ocorrencia"
    metadata = destination / "metadata"
    manifest = metadata / "migration_manifest.jsonl"
    identity_path = metadata / "migration_identity.json"
    run_identity = {"identity": identity, "source_names": [p.name for p in files]}
    if destination.exists() and any(destination.iterdir()):
        reject_links(identity_path)
        if not identity_path.is_file():
            raise MigrationError("untrusted_existing_destination")
        if verify_document(json.loads(identity_path.read_bytes()), key) != run_identity:
            raise MigrationError("migration_identity_changed")
    reject_links(manifest)
    receipts = {}
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            row = verify_document(json.loads(line), key)
            if row.get("identity") != identity or row.get("source_name") in receipts:
                raise MigrationError("manifest_identity_or_duplicate")
            receipts[row["source_name"]] = row
    if not set(receipts).issubset({p.name for p in files}):
        raise MigrationError("source_set_changed")
    if bronze.exists():
        for path in bronze.iterdir():
            reject_links(path)
            if path.name not in receipts or not path.is_file():
                raise MigrationError("unreceipted_output")
    # Only a reviewed catalog is persisted; never derive it from raw records.
    publish_new(identity_path, sign_document(run_identity, key))
    publish_new(metadata / "privacy_catalog.json", category_catalog)
    try:
        with manifest.open("xb"):
            pass
    except FileExistsError:
        pass
    totals = Counter()
    processed = []
    for index, path in enumerate(files, 1):
        raw = path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        previous = receipts.get(path.name)
        output = bronze / path.name
        if previous is not None:
            reject_links(output)
            output_bytes = output.read_bytes()
            if previous["source_sha256"] != source_hash or previous["output_sha256"] != hashlib.sha256(output_bytes).hexdigest():
                raise MigrationError("source_or_output_changed")
            validate_sanitized_payload(json.loads(output_bytes), key, category_catalog=category_catalog)
            row = previous
            del output_bytes
        else:
            try:
                payload = json.loads(raw)
                sanitized, counts = sanitize_legacy_payload(payload, key, category_catalog=category_catalog)
            except PrivacyError:
                raise
            except (ValueError, TypeError, OverflowError, RecursionError):
                raise MigrationError("legacy_payload_invalid") from None
            del payload
            row = {"identity": identity, "source_name": path.name,
                   "source_sha256": source_hash, "source_bytes": len(raw),
                   "output_sha256": publish_new(output, sanitized), "counts": counts}
            del sanitized
            with manifest.open("ab") as stream:
                stream.write(json_bytes(sign_document(row, key)) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        del raw
        totals.update(row["counts"])
        processed.append(row)
        if on_progress is not None:
            on_progress({"files_done": index, "files_total": len(files), "records": totals["records"]})
    # Verify originals again before certifying completion, including skipped files.
    for path, row in zip(files, processed):
        with path.open("rb") as stream:
            current_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if current_hash != row["source_sha256"]:
            raise MigrationError("source_changed_during_migration")
    summary = {"status": "complete", "identity": identity, "source_files": len(files),
               "counts": dict(totals), "source_total_bytes": sum(r["source_bytes"] for r in processed),
               "originals_verified_unchanged": True, "dataset_private": True,
               "source_receipts_sha256": hashlib.sha256(json_bytes(processed)).hexdigest()}
    publish_new(metadata / "migration_summary.json", sign_document(summary, key))
    return summary
