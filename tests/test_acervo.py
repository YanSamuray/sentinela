import pytest

from totens.acervo import checked_copy, digest, promote


def test_copy_verifies_bytes_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_bytes(b"synthetic minimized bytes")
    expected = digest(source)
    checked_copy(source, target, expected)
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="destination_exists"):
        checked_copy(source, target, expected)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source_changed"):
        checked_copy(source, tmp_path / "other", expected)
    assert not (tmp_path / "other").exists()


def test_promotion_cannot_escape_or_proceed_without_authentication(tmp_path):
    with pytest.raises(ValueError, match="invalid_promotion_id"):
        promote(tmp_path, "../outside", b"x" * 32)
    with pytest.raises(FileNotFoundError):
        promote(tmp_path, "valid", b"x" * 32)
    assert not (tmp_path / "temp/quarentena").exists()


def test_promotion_keeps_legacy_and_old_snapshot_and_authenticates_copy(tmp_path, monkeypatch):
    from totens.local_migration import publish_new, sign_document, verify_document
    import json
    key = b"synthetic-key-for-test-at-least-32-bytes"
    for layer in ("bronze", "silver", "gold"):
        old = tmp_path / "data" / layer / "synthetic.bin"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"synthetic legacy")
        new = tmp_path / "data/private_minimized_v1" / layer / "synthetic.bin"
        new.parent.mkdir(parents=True)
        new.write_bytes(b"synthetic minimized")
    source = tmp_path / "data/private_minimized_v1"
    hashes = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob("*") if p.is_file()}
    publish_new(tmp_path / "temp/test_preflight.json", sign_document(
        {"status": "complete", "source_root": "data/private_minimized_v1", "authenticated_sha256": hashes}, key))
    monkeypatch.setattr("totens.acervo.subprocess.check_output", lambda *args, **kwargs: b"")
    result = promote(tmp_path, "test", key)
    for layer in ("bronze", "silver", "gold"):
        assert (tmp_path / "data" / layer / "synthetic.bin").read_bytes() == b"synthetic minimized"
        assert (tmp_path / "temp/quarentena/test/data" / layer / "synthetic.bin").read_bytes() == b"synthetic legacy"
        assert (source / layer / "synthetic.bin").read_bytes() == b"synthetic minimized"
    receipt = verify_document(json.loads((tmp_path / "data/metadata/promotion_receipt.json").read_bytes()), key)
    assert receipt["status"] == "complete" and not receipt["deletion_authorized"]
    assert result["copied_files"] == 3
    with pytest.raises(ValueError, match="quarantine_already_exists"):
        promote(tmp_path, "test", key)
