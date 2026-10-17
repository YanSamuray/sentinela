"""Synthetic-only independent checks of positional migration preservation."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from totens.local_migration import migrate, sanitize_legacy_payload, verify_document


KEY = b"independent-verifier-test-key-32!!"
PRIVATE = "SYNTHETIC_PRIVATE_ONLY"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def verifier():
    path = ROOT / "scripts" / "pipeline" / "verify_local_minimization.py"
    spec = importlib.util.spec_from_file_location("test_independent_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(protocol=123):
    return {
        "protocolo": protocol, "dataCriacao": "2024-01-01T12:34:56.123-0400",
        "descricao": PRIVATE, "pessoas": [{"nome": PRIVATE}],
        "localizacao": {"latitude": -8.750000000001, "longitude": -63.900000000003,
                        "endereco": PRIVATE},
        "dadosEncerramento": [{"dataHoraFato": "2024-01-01T11:00:00.000-0400",
                                "relatoPolicial": PRIVATE}],
    }


def minimized(item):
    payload, _ = sanitize_legacy_payload([item], KEY, category_catalog={})
    return payload["params"][0]


def test_independent_pair_check_preserves_dates_offsets_and_exact_coordinates(verifier):
    original = record()
    counts = Counter()
    source_identity, digest = verifier.verify_record(original, minimized(original), KEY, counts)
    assert source_identity == b"123"
    assert len(digest) == 32
    assert counts["creation_datetimes_verified"] == 1
    assert counts["closure_datetimes_verified"] == 1
    assert counts["valid_coordinates_preserved_exactly"] == 2
    assert counts["valid_coordinate_pairs_preserved"] == 1


@pytest.mark.parametrize("field,error", [
    ("protocolo", "protocol_not_preserved"),
    ("dataCriacao", "datetime_not_preserved"),
    ("latitude", "coordinate_not_preserved"),
    ("dataHoraFato", "datetime_not_preserved"),
])
def test_independent_comparison_rejects_changed_values(verifier, field, error):
    original = record()
    output = minimized(original)
    if field == "protocolo":
        output[field] = "hmac-sha256:" + "0" * 64
    elif field == "dataCriacao":
        output[field] = "2024-01-01T12:34:57.123-04:00"
    elif field == "latitude":
        output["localizacao"][field] = round(output["localizacao"][field], 6)
    else:
        output["dadosEncerramento"][0][field] = "2024-01-02T11:00:00-04:00"
    with pytest.raises(verifier.VerificationError, match=error):
        verifier.verify_record(original, output, KEY)


def test_invalid_and_null_coordinates_must_be_absent(verifier):
    original = record()
    original["localizacao"].update(latitude=1000, longitude=None)
    output = minimized(original)
    counts = Counter()
    verifier.verify_record(original, output, KEY, counts)
    assert counts["invalid_coordinates_verified_absent"] == 1
    assert counts["null_coordinates_verified_absent"] == 1
    output["localizacao"]["latitude"] = 0.0
    with pytest.raises(verifier.VerificationError, match="invalid_coordinate_not_omitted"):
        verifier.verify_record(original, output, KEY)


def test_personal_field_rejected_recursively(verifier):
    original = record()
    output = minimized(original)
    output["dadosEncerramento"][0]["relatoPolicial"] = PRIVATE
    with pytest.raises(verifier.VerificationError, match="personal_key_present") as error:
        verifier.verify_record(original, output, KEY)
    assert PRIVATE not in str(error.value)


def test_full_verification_counts_duplicates_and_only_writes_signed_aggregate(verifier, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ocorrencias_20240101_a_20240107.json").write_text(
        json.dumps([record(), record()]), encoding="utf-8")
    (source / "ocorrencias_20240108_a_20240114.json").write_text(
        json.dumps([record(), record(456)]), encoding="utf-8")
    destination = tmp_path / "minimized"
    migrate(source, destination, KEY, category_catalog={})
    before = {p: p.read_bytes() for folder in (source, destination)
              for p in folder.rglob("*") if p.is_file()}
    result = verifier.verify(source, destination, KEY, category_catalog={})
    assert result["counts"]["records"] == 4
    assert result["counts"]["unique_canonical_protocols"] == 2
    assert result["counts"]["duplicate_record_excess_global"] == 2
    assert result["counts"]["duplicate_record_excess_within_files"] == 1
    assert result["counts"]["distinct_protocols_with_duplicates"] == 1
    assert result["counts"]["observed_hmac_collisions"] == 0
    summary_path = destination / "metadata" / "verification_summary.json"
    assert verify_document(json.loads(summary_path.read_bytes()), KEY) == result
    after_paths = {p for folder in (source, destination) for p in folder.rglob("*") if p.is_file()}
    assert after_paths - set(before) == {summary_path}
    assert all(path.read_bytes() == content for path, content in before.items())
    serialized = summary_path.read_text(encoding="utf-8")
    assert PRIVATE not in serialized
    assert "hmac-sha256:" not in serialized
    assert verifier.verify(source, destination, KEY, category_catalog={}) == result


def test_receipt_source_hash_checked_before_comparison(verifier, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "ocorrencias_20240101_a_20240107.json"
    path.write_text(json.dumps([record()]), encoding="utf-8")
    destination = tmp_path / "minimized"
    migrate(source, destination, KEY, category_catalog={})
    changed = deepcopy(record())
    changed["descricao"] = "CHANGED_SYNTHETIC_ONLY"
    path.write_text(json.dumps([changed]), encoding="utf-8")
    with pytest.raises(verifier.VerificationError, match="source_hash_mismatch"):
        verifier.verify(source, destination, KEY, category_catalog={})
    assert not (destination / "metadata" / "verification_summary.json").exists()
