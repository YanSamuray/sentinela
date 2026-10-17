"""Synthetic regressions for immutable migration identity and manifest paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from totens.local_migration import MigrationError, migrate


KEY = bytes(range(32))


def _write_source(source: Path, day: int) -> Path:
    path = source / f"ocorrencias_202401{day:02d}_a_202401{day:02d}.json"
    record = {
        "protocolo": f"SYNTHETIC-PROTOCOL-{day}",
        "dataCriacao": f"2024-01-{day:02d}T10:00:00Z",
        "descricao": "SYNTHETIC-PRIVATE-TEXT",
    }
    path.write_text(json.dumps([record]), encoding="utf-8")
    return path


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("change", ["added", "deleted"])
def test_completed_migration_rejects_changed_source_set_before_writes(
    tmp_path: Path, change: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, 1)
    second = _write_source(source, 2)
    destination = tmp_path / "isolated"
    summary = migrate(source, destination, KEY, category_catalog={})
    assert summary["status"] == "complete"
    before = _snapshot(destination)

    if change == "added":
        _write_source(source, 3)
    else:
        second.unlink()
    source_after_intentional_change = _snapshot(source)

    with pytest.raises(MigrationError, match="^migration_identity_changed$"):
        migrate(source, destination, KEY, category_catalog={})

    assert _snapshot(destination) == before
    assert _snapshot(source) == source_after_intentional_change


def test_manifest_symlink_is_rejected_without_touching_target_when_supported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source, 1)
    destination = tmp_path / "isolated"
    migrate(source, destination, KEY, category_catalog={})
    before = _snapshot(destination)
    manifest = destination / "metadata" / "migration_manifest.jsonl"
    outside = tmp_path / "outside-manifest.jsonl"
    original_bytes = manifest.read_bytes()
    outside.write_bytes(original_bytes)
    manifest.unlink()
    try:
        manifest.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This environment does not allow creating symbolic links")

    with pytest.raises(MigrationError, match="^linked_path_forbidden$"):
        migrate(source, destination, KEY, category_catalog={})

    assert outside.read_bytes() == original_bytes
    assert _snapshot(destination) == before
