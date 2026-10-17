import json

import pandas as pd
import pytest

from totens.local_migration import migrate
from totens.portfolio.io import _legacy_token, load_manifest, private_ledger, reconcile_legacy

KEY = b"synthetic-case-key-at-least-32-bytes"


def test_archived_reconciliation_needs_no_complete_source_and_rejects_different_snapshot(tmp_path):
    from totens.local_migration import publish_new, sign_document
    from totens.portfolio.io import archived_legacy
    path = tmp_path / "metadata/legacy_reconciliation.json"
    publish_new(path, sign_document({"status": "complete", "source_summary_sha256": "synthetic_hash",
                                    "original_run": "synthetic", "reconciliation": {"current_rows": 5, "status": "complete"}}, KEY))
    summary, _ = archived_legacy(tmp_path, KEY, {"migration_summary_sha256": "synthetic_hash", "source_rows": 5})
    assert summary["evidence_mode"] == "authenticated_historical_not_recalculated"
    with pytest.raises(ValueError, match="source_mismatch"):
        archived_legacy(tmp_path, KEY, {"migration_summary_sha256": "different", "source_rows": 5})


def migrated(tmp_path):
    source, output = tmp_path / "s", tmp_path / "d"
    source.mkdir()
    records = [{"protocolo": "SYNTHETIC_A", "dataCriacao": "2024-01-02T12:00:00-04:00", "nivelRisco": "Alto",
                "localizacao": {"latitude": -10., "longitude": -63.}}]
    (source / "ocorrencias_20240101_a_20240103.json").write_text(json.dumps(records), encoding="utf-8")
    migrate(source, output, KEY, category_catalog={"nivelRisco": ["Alto"]})
    return output


def test_private_projection_checks_manifest_and_uses_source_not_composite_flags(tmp_path):
    root = migrated(tmp_path)
    paths, days, summary, _ = private_ledger(root, tmp_path / "l", KEY)
    projected = pd.read_parquet(paths[0])
    assert summary["authenticated_migration"] and len(days) == 3
    assert projected.risk_level.iloc[0] == 3
    assert projected.protocolo.iloc[0] == _legacy_token("SYNTHETIC_A", KEY)
    assert "SYNTHETIC_A" not in paths[0].read_bytes().decode("utf-8", errors="ignore")


def test_modified_payload_is_rejected_before_persisting_a_projection(tmp_path):
    root = migrated(tmp_path)
    path = next((root / "bronze/ocorrencia").glob("*.json"))
    body = json.loads(path.read_bytes())
    body["params"][0]["nivelRisco"] = "Baixo"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="bronze_hash_mismatch"):
        private_ledger(root, tmp_path / "l", KEY)
    assert not list((tmp_path / "l").glob("*.parquet"))


def test_manifest_hash_aggregate_cannot_be_replaced_by_another_valid_file_set(tmp_path):
    root = migrated(tmp_path)
    manifest = root / "metadata/migration_manifest.jsonl"
    manifest.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="migration_manifest_mismatch"):
        load_manifest(root, KEY)


def test_legacy_reconciliation_hashes_identifiers_and_accounts_for_multiplicity(tmp_path):
    root = migrated(tmp_path)
    ledgers, _, _, _ = private_ledger(root, tmp_path / "l", KEY)
    folder = tmp_path / "data/gold/analitica/f_ocorrencia"
    folder.mkdir(parents=True)
    pd.DataFrame({"protocolo": ["SYNTHETIC_A", "SYNTHETIC_A"],
        "dataCriacao": pd.to_datetime(["2024-01-02T16:00:00Z"] * 2),
        "nome_feriado": ["SYNTHETIC_HOLIDAY_A", "SYNTHETIC_HOLIDAY_B"]}).to_parquet(folder / "p.parquet", index=False)
    summary, _ = reconcile_legacy(tmp_path, ledgers, tmp_path / "x", KEY)
    assert summary["net_difference"] == 1 and summary["excess_multiplicity_same_key_date"] == 1
    assert summary["surplus_with_different_holiday_labels"] == 1
    assert "SYNTHETIC_A" not in next((tmp_path / "x").glob("*.parquet")).read_bytes().decode("utf-8", errors="ignore")
