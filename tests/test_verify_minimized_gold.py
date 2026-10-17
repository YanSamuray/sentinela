"""Synthetic failures exercise the independent Gold verifier."""
from __future__ import annotations

import importlib.util
import hashlib
import hmac
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from totens.local_migration import json_bytes, verify_document

KEY = b"synthetic-gold-verification-key-32"


@pytest.fixture(scope="module")
def verifier():
    path = Path(__file__).resolve().parents[1] / "scripts/pipeline/verify_minimized_gold.py"
    spec = importlib.util.spec_from_file_location("independent_gold_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dataset(root: Path) -> tuple[Path, pd.DataFrame]:
    silver = root / "silver/normalized/f_ocorrencia"
    gold = root / "gold/analitica/f_ocorrencia"
    auxiliary = root / "silver/cadastros"
    for folder in (silver, gold, auxiliary):
        folder.mkdir(parents=True)
    frame = pd.DataFrame({
        "protocolo": ["SYNTHETIC-A", "SYNTHETIC-B", "SYNTHETIC-C"],
        "dataCriacao": pd.to_datetime(["2024-01-01T00:00:00Z"] * 3),
        "localizacao_latitude": [0.0, 0.005, None],
        "localizacao_longitude": [0.0, 0.0, None],
    })
    frame.to_parquet(silver / "synthetic.parquet", index=False)
    result = frame.copy()
    result["distancia_totem_m"] = [0.0, 6_371_000 * math.radians(0.005), None]
    result["id_totem_prox"] = ["TOTEM-A", "TOTEM-A", None]
    result["flag_dentro_raio"] = pd.Series([True, False, pd.NA], dtype="boolean")
    gold_file = gold / "synthetic.parquet"
    result.to_parquet(gold_file, index=False)
    # Identical points deliberately exercise permitted nearest-point ties.
    pd.DataFrame({"id_unidade": ["TOTEM-A", "TOTEM-B"], "latitude": [0.0, 0.0],
                  "longitude": [0.0, 0.0]}).to_parquet(auxiliary / "cadastro_totens.parquet", index=False)
    return gold_file, result


def test_correct_gold_and_nearest_point_ties_pass(verifier, tmp_path):
    path, frame = _dataset(tmp_path)
    frame.loc[0, "id_totem_prox"] = "TOTEM-B"
    frame.to_parquet(path, index=False)
    result = verifier.audit_gold(tmp_path, expected_totems=2)
    assert result["status"] == "complete"
    assert result["exact_occurrence_multiset_matches"]
    assert result["silver_rows"] == result["gold_rows"] == 3
    assert result["null_distances"] == result["null_radius_flags"] == 1
    assert result["maximum_distance_error_meters"] < 1e-4


@pytest.mark.parametrize("change", ["coordinates", "distance", "flag", "missing_flag", "nearest_id"])
def test_changed_coordinates_distance_or_flag_fail(verifier, tmp_path, change):
    path, frame = _dataset(tmp_path)
    if change == "coordinates":
        frame.loc[1, "localizacao_latitude"] = 0.006
    elif change == "distance":
        frame.loc[1, "distancia_totem_m"] += 1.0
    elif change == "flag":
        frame.loc[1, "flag_dentro_raio"] = True
    elif change == "missing_flag":
        frame.loc[2, "flag_dentro_raio"] = False
    else:
        frame.loc[1, "id_totem_prox"] = "UNKNOWN-SYNTHETIC-ID"
    frame.to_parquet(path, index=False)
    result = verifier.audit_gold(tmp_path, expected_totems=2)
    assert result["status"] == "failed"
    if change == "coordinates":
        assert not result["exact_occurrence_multiset_matches"]
    elif change == "distance":
        assert result["errors"]["distance_errors"] == 1
    elif change == "flag":
        assert result["errors"]["radius_flag_errors"] == 1
    elif change == "missing_flag":
        assert result["errors"]["missing_coordinate_output_errors"] == 1
    else:
        assert result["errors"]["nearest_id_errors"] == 1


def _sign_snapshot(root: Path, *, radius: float = 300.0) -> dict:
    work = root / "metadata/rebuild"
    work.mkdir(parents=True)
    (work / "settings.toml").write_text("# synthetic configuration\n")
    outputs = []
    for folder in (root / "silver", root / "gold", work):
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                raw = path.read_bytes()
                outputs.append({"path": path.relative_to(root).as_posix(),
                                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    receipt = {"status": "complete", "outputs": outputs,
               "request": {"radius_meters": radius},
               "auxiliary": {"totems": {"retained_rows": 2}}}
    signature = hmac.new(KEY, b"totens-rebuild-receipt-v1\0" + json_bytes(receipt), hashlib.sha256).hexdigest()
    (work / "receipt.json").write_bytes(json_bytes({**receipt, "receipt_hmac": signature}))
    return receipt


def test_completed_snapshot_authenticates_every_output_and_uses_recorded_radius(verifier, tmp_path):
    path, frame = _dataset(tmp_path)
    frame.loc[1, "flag_dentro_raio"] = True
    frame.to_parquet(path, index=False)
    receipt = _sign_snapshot(tmp_path, radius=600.0)
    assert verifier.audit_gold(tmp_path)["status"] == "failed"
    result = verifier.verify_completed_gold(tmp_path, KEY)
    assert result["status"] == "complete"
    assert result["radius_meters"] == 600.0
    assert result["authenticated_output_inventory"] is True
    assert result["verified_output_files"] == len(receipt["outputs"])


def test_risk_field_mutation_fails_even_when_geographical_audit_passes(verifier, tmp_path, monkeypatch):
    path, frame = _dataset(tmp_path)
    frame["flag_crime_violento"] = pd.Series([False, True, pd.NA], dtype="boolean")
    frame.to_parquet(path, index=False)
    _sign_snapshot(tmp_path)
    frame.loc[0, "flag_crime_violento"] = True
    frame.to_parquet(path, index=False)
    assert verifier.audit_gold(tmp_path)["status"] == "complete"
    monkeypatch.setattr(verifier, "audit_gold", lambda *a, **kw: pytest.fail("Do not audit an unauthenticated snapshot"))
    with pytest.raises(ValueError, match="^rebuild_output_hash_mismatch$"):
        verifier.verify_completed_gold(tmp_path, KEY)


@pytest.mark.parametrize("change", ["added", "removed", "configuration"])
def test_inventory_rejects_added_removed_and_nonparquet_changes(verifier, tmp_path, change):
    path, _ = _dataset(tmp_path)
    _sign_snapshot(tmp_path)
    if change == "added":
        (path.parent / "unexpected.txt").write_text("synthetic")
    elif change == "removed":
        path.unlink()
    else:
        (tmp_path / "metadata/rebuild/settings.toml").write_text("# changed synthetic configuration\n")
    with pytest.raises(ValueError, match="^rebuild_output_hash_mismatch$"):
        verifier.verify_completed_gold(tmp_path, KEY)


def test_snapshot_changed_during_audit_is_rejected(verifier, tmp_path, monkeypatch):
    path, frame = _dataset(tmp_path)
    _sign_snapshot(tmp_path)
    original_audit = verifier.audit_gold

    def change_after_audit(*args, **kwargs):
        result = original_audit(*args, **kwargs)
        frame["flag_crime_violento"] = False
        frame.to_parquet(path, index=False)
        return result

    monkeypatch.setattr(verifier, "audit_gold", change_after_audit)
    with pytest.raises(ValueError, match="^rebuild_output_hash_mismatch$"):
        verifier.verify_completed_gold(tmp_path, KEY)


def test_receipt_authentication_precedes_inventory_read(verifier, tmp_path, monkeypatch):
    _dataset(tmp_path)
    _sign_snapshot(tmp_path)
    path = tmp_path / "metadata/rebuild/receipt.json"
    receipt = json.loads(path.read_bytes())
    receipt["request"]["radius_meters"] = 600.0
    path.write_bytes(json_bytes(receipt))
    monkeypatch.setattr(verifier, "verify_rebuild_inventory", lambda *a: pytest.fail("Receipt must authenticate first"))
    with pytest.raises(ValueError, match="^completed_rebuild_required$"):
        verifier.verify_completed_gold(tmp_path, KEY)


@pytest.mark.parametrize("radius", [0, -1, float("nan"), float("inf"), True, "300"])
def test_invalid_radius_fails_before_reading_data(verifier, tmp_path, radius):
    with pytest.raises(ValueError, match="^invalid_verification_radius$"):
        verifier.audit_gold(tmp_path, radius_meters=radius)


def test_explicit_report_preserves_previous_report(verifier, tmp_path, monkeypatch, capsys):
    _dataset(tmp_path)
    _sign_snapshot(tmp_path)
    previous = tmp_path / "metadata/gold_verification_summary.json"
    previous.write_bytes(b'{"historical_synthetic_report": true}')
    original = previous.read_bytes()
    report = tmp_path / "metadata/gold_verification_v2.json"
    monkeypatch.setattr(verifier, "load_local_key", lambda *args, **kwargs: KEY)
    assert verifier.main(["--root", str(tmp_path), "--report", str(report)]) == 0
    result = verify_document(json.loads(report.read_bytes()), KEY)
    assert result["status"] == "complete"
    assert result["authenticated_output_inventory"] is True
    assert previous.read_bytes() == original
    assert verifier.main(["--root", str(tmp_path), "--report", str(previous)]) == 1
    assert previous.read_bytes() == original
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "blocked"


def test_report_cannot_be_added_to_authenticated_dataset(verifier, tmp_path, monkeypatch):
    _dataset(tmp_path)
    _sign_snapshot(tmp_path)
    monkeypatch.setattr(verifier, "load_local_key", lambda *args, **kwargs: pytest.fail("No key needed for invalid report path"))
    report = tmp_path / "gold/new_report.json"
    assert verifier.main(["--root", str(tmp_path), "--report", str(report)]) == 1
    assert not report.exists()
