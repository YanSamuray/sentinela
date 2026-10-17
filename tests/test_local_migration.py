"""Synthetic-only tests of the offline migration boundary and resumability."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from totens.local_migration import MigrationError, compile_catalog, migrate, sanitize_legacy_payload, verify_document
from totens.privacy import PrivacyError, validate_sanitized_payload

KEY = b"local-migration-synthetic-key-32!"
PRIVATE = "PRIVATE_SYNTHETIC_PERSON_MARKER"
FILENAME = "ocorrencias_20240101_a_20240107.json"
CATALOG = {"grupo.descricao": ["PATRIMONIAL"], "unidades[].descricao": ["UNIDADE TESTE"]}


def record():
    return {"protocolo": "00123456789", "dataCriacao": "2024-01-01T12:34:56",
            "descricao": PRIVATE, "pessoas": [{"nome": PRIVATE}],
            "grupo": {"descricao": "PATRIMONIAL", "id": PRIVATE, "nome": PRIVATE},
            "unidades": [{"descricao": "UNIDADE TESTE", "id": PRIVATE, "operador": PRIVATE}],
            "localizacao": {"latitude": -8.75, "longitude": -63.9, "endereco": PRIVATE},
            "dadosEncerramento": [{"dataHoraFato": "2024-01-01T11:00:00", "relatoPolicial": PRIVATE,
                                    "houveDisparoArmaFogo": False}],
            "perguntasNivelRisco": [{"pergunta": "Autor do fato está armado?", "resposta": "Há_Suspeita"}]}


def source_file(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / FILENAME
    path.write_text(json.dumps([record(), record()]), encoding="utf-8")
    return source, path


def test_minimization_precedes_disk_preserves_rows_and_is_resumable(tmp_path):
    source, original = source_file(tmp_path)
    before = original.read_bytes()
    destination = tmp_path / "isolated"
    summary = migrate(source, destination, KEY, category_catalog=CATALOG)
    assert summary["counts"]["records"] == 2
    assert summary["counts"]["coordinate_pairs_preserved"] == 2
    assert summary["counts"]["creation_dates_without_timezone"] == 2
    assert summary["counts"]["risk_answers_non_boolean_omitted"] == 2
    assert original.read_bytes() == before
    generated = destination / "bronze" / "ocorrencia" / FILENAME
    data = json.loads(generated.read_bytes())
    assert data["params"][0]["protocolo"] == data["params"][1]["protocolo"]
    assert data["params"][0]["localizacao"] == {"latitude": -8.75, "longitude": -63.9}
    assert data["params"][0]["perguntasNivelRisco"] == []  # Suspicion never becomes False.
    assert data["params"][0]["unidades"] == [{"descricao": "UNIDADE TESTE"}]
    validate_sanitized_payload(data, KEY, category_catalog=CATALOG)
    for path in destination.rglob("*"):
        if path.is_file():
            assert PRIVATE.encode() not in path.read_bytes()
            assert b"00123456789" not in path.read_bytes()
    snapshot = {str(p): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    assert migrate(source, destination, KEY, category_catalog=CATALOG) == summary
    assert snapshot == {str(p): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    signed_summary = json.loads((destination / "metadata/migration_summary.json").read_bytes())
    assert verify_document(signed_summary, KEY) == summary


def test_legacy_invalid_coordinates_are_missing_and_counted():
    item = record()
    item["localizacao"]["latitude"] = 1000
    output, counts = sanitize_legacy_payload([item], KEY, category_catalog=CATALOG)
    assert "latitude" not in output["params"][0]["localizacao"]
    assert counts["coordinates_invalid_omitted"] == 1
    assert counts.get("coordinate_pairs_preserved", 0) == 0


def test_unknown_category_not_automatically_approved():
    item = record()
    item["grupo"]["descricao"] = PRIVATE
    with pytest.raises(PrivacyError, match="unknown_category"):
        sanitize_legacy_payload([item], KEY, category_catalog=CATALOG)


def test_unreviewed_categories_are_tokenized_without_cleartext_catalog(tmp_path):
    source, original = source_file(tmp_path)
    items = [record(), record()]
    for item in items:
        item["fatoComunicado"] = {"descricao": PRIVATE}
        item["fatoConstatado"] = [{"descricao": PRIVATE}]
    original.write_text(json.dumps(items), encoding="utf-8")
    compiled = compile_catalog(source, KEY, reviewed_catalog=CATALOG)
    assert PRIVATE not in json.dumps(compiled)
    output, counts = sanitize_legacy_payload(items, KEY, category_catalog=compiled)
    token = output["params"][0]["fatoComunicado"]["descricao"]
    assert token.startswith("category-hmac-sha256:")
    assert output["params"][1]["fatoComunicado"]["descricao"] == token
    assert output["params"][0]["fatoConstatado"][0]["descricao"] == token
    assert counts["categories_pseudonymized:fatoComunicado.descricao"] == 2
    assert PRIVATE not in json.dumps(output)
    # A raw input cannot masquerade as a pre-tokenized category.
    items[0]["fatoComunicado"]["descricao"] = token
    with pytest.raises(PrivacyError, match="unknown_category"):
        sanitize_legacy_payload(items, KEY, category_catalog=compiled)


def test_compact_legacy_offsets_preserve_instant_and_reject_overflow():
    from datetime import datetime
    item = record()
    value = "2024-01-01T12:34:56.123-0400"
    item["dataCriacao"] = value
    item["dadosEncerramento"][0]["dataHoraFato"] = value
    output, counts = sanitize_legacy_payload([item], KEY, category_catalog=CATALOG)
    assert datetime.fromisoformat(output["params"][0]["dataCriacao"]) == datetime.fromisoformat(value)
    assert counts["creation_compact_offsets_normalized"] == 1
    assert counts["closure_compact_offsets_normalized"] == 1
    assert "creation_dates_without_timezone" not in counts
    item["dataCriacao"] = "2024-01-01T12:34:56.123-0460"
    with pytest.raises(PrivacyError, match="invalid_datetime"):
        sanitize_legacy_payload([item], KEY, category_catalog=CATALOG)


@pytest.mark.parametrize("change", ["source", "output", "receipt", "key", "catalog"])
def test_tampering_and_identity_changes_are_blocked(tmp_path, change):
    source, original = source_file(tmp_path)
    destination = tmp_path / "isolated"
    migrate(source, destination, KEY, category_catalog=CATALOG)
    key, catalog = KEY, CATALOG
    if change == "source":
        original.write_bytes(original.read_bytes() + b" ")
    elif change == "output":
        (destination / "bronze/ocorrencia" / FILENAME).write_bytes(b"{}")
    elif change == "receipt":
        path = destination / "metadata/migration_manifest.jsonl"
        row = json.loads(path.read_bytes())
        row["counts"]["records"] = 100
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    elif change == "key":
        key = b"another-synthetic-key-for-test-32!"
    else:
        catalog = {**CATALOG, "nivelRisco": ["ALTO"]}
    with pytest.raises((MigrationError, PrivacyError)):
        migrate(source, destination, key, category_catalog=catalog)


def test_overlap_and_untrusted_destination_block_without_overwrite(tmp_path):
    source, original = source_file(tmp_path)
    before = original.read_bytes()
    with pytest.raises(MigrationError, match="source_destination_overlap"):
        migrate(source, source / "output", KEY, category_catalog=CATALOG)
    destination = tmp_path / "untrusted"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(MigrationError, match="untrusted_existing_destination"):
        migrate(source, destination, KEY, category_catalog=CATALOG)
    assert marker.read_text() == "preserve"
    assert original.read_bytes() == before


def test_source_changed_during_run_is_not_certified(tmp_path):
    source, original = source_file(tmp_path)
    destination = tmp_path / "isolated"
    def change_source(_):
        original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(MigrationError, match="source_changed_during_migration"):
        migrate(source, destination, KEY, category_catalog=CATALOG, on_progress=change_source)
    assert not (destination / "metadata/migration_summary.json").exists()
