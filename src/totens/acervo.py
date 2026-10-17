"""Auditable promotion of a minimized lake; never delete legacy files."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from totens.local_migration import publish_new, reject_links, sign_document, verify_document


def digest(path: Path) -> str:
    reject_links(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files_under(root: Path):
    reject_links(root)
    for path in sorted(root.rglob("*")):
        reject_links(path)
        if path.is_file():
            yield path


def inventory(project: Path, folders: tuple[str, ...], progress=None) -> list[dict]:
    entries = []
    for folder in folders:
        for path in files_under(project / folder):
            attrs = getattr(path.stat(), "st_file_attributes", 0)
            cloud_only = bool(attrs & (0x00400000 | 0x00001000 | 0x00040000))
            required = folder in ("data/bronze", "data/silver", "data/gold", "data/private_minimized_v1")
            entries.append({"path": path.relative_to(project).as_posix(),
                            "bytes": path.stat().st_size,
                            "sha256": digest(path) if required or not cloud_only else None,
                            "hash_status": "verified" if required or not cloud_only else "cloud_placeholder_not_read",
                            "classification": "minimized_keep" if folder.endswith("private_minimized_v1")
                            else "quarantine_replaced" if folder in ("data/bronze", "data/silver", "data/gold")
                            else "review_before_discard"})
        if progress:
            progress({"event": "folder_inventoried", "folder": folder, "files_so_far": len(entries)})
    return entries


def checked_copy(source: Path, destination: Path, expected: str) -> None:
    reject_links(source)
    reject_links(destination)
    if destination.exists():
        raise ValueError("promotion_destination_exists")
    if digest(source) != expected:
        raise ValueError("promotion_source_changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive destination: a partial copy is preserved for explicit recovery.
    with source.open("rb") as origin, destination.open("xb") as target:
        shutil.copyfileobj(origin, target, length=1024 * 1024)
    if digest(destination) != expected or digest(source) != expected:
        raise ValueError("promotion_copy_mismatch")


def promote(project: Path, run_id: str, key: bytes, progress=None) -> dict:
    """Require authenticated preflight; journal before every confined move/copy."""
    project = project.resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid_promotion_id")
    source = project / "data/private_minimized_v1"
    target = project / "data"
    quarantine = project / "temp/quarentena" / run_id
    for path in (source, target, quarantine):
        reject_links(path)
        if not path.resolve().is_relative_to(project):
            raise ValueError("promotion_path_escape")
    preflight_path = project / "temp" / f"{run_id}_preflight.json"
    preflight = verify_document(json.loads(preflight_path.read_bytes()), key)
    if preflight.get("status") != "complete" or preflight.get("source_root") != "data/private_minimized_v1":
        raise ValueError("promotion_preflight_required")
    if quarantine.exists():
        raise ValueError("quarantine_already_exists")
    entries = inventory(project, ("data/bronze", "data/silver", "data/gold", "data/private_minimized_v1",
                                  "archive", "bi", "reports", "metadata", "predictions", "temp"), progress)
    source_entries = [e for e in entries if e["path"].startswith("data/private_minimized_v1/")]
    # Recheck the exact signed snapshot that was validated before publication.
    source_hashes = {e["path"].removeprefix("data/private_minimized_v1/"): e["sha256"] for e in source_entries}
    if any(source_hashes.get(p) != h for p, h in preflight["authenticated_sha256"].items()):
        raise ValueError("promotion_preflight_source_changed")
    quarantine.mkdir(parents=True)
    state = {"version": 1, "status": "prepared", "run_id": run_id,
             "created_at": datetime.now(timezone.utc).isoformat(), "source_root": "data/private_minimized_v1",
             "active_root": "data", "quarantine": quarantine.relative_to(project).as_posix(),
             "deletion_authorized": False, "entries": entries,
             "local_changes": subprocess.check_output(["git", "status", "--porcelain"], cwd=project).decode(),
             "preflight_sha256": digest(preflight_path)}
    publish_new(quarantine / "inventory.json", sign_document(state, key))
    journal = quarantine / "operations.jsonl"
    def operation(body):
        with journal.open("ab") as stream:
            stream.write(json.dumps(sign_document(body, key), sort_keys=True).encode() + b"\n")
            stream.flush()
    # These are explicitly inventoried replacement roots, never computed from file contents.
    for layer in ("bronze", "silver", "gold"):
        old = target / layer
        destination = quarantine / "data" / layer
        if old.exists():
            for e in entries:
                if e["path"].startswith(f"data/{layer}/") and digest(project / e["path"]) != e["sha256"]:
                    raise ValueError("quarantine_source_changed")
            destination.parent.mkdir(parents=True, exist_ok=True)
            operation({"action": "move_intent", "source": f"data/{layer}", "destination": destination.relative_to(project).as_posix()})
            old.rename(destination)
            operation({"action": "move_complete", "source": f"data/{layer}", "destination": destination.relative_to(project).as_posix()})
    for e in source_entries:
        relative = e["path"].removeprefix("data/private_minimized_v1/")
        destination = target / relative
        operation({"action": "copy_intent", "source": e["path"], "destination": destination.relative_to(project).as_posix(), "sha256": e["sha256"], "bytes": e["bytes"]})
        checked_copy(project / e["path"], destination, e["sha256"])
    # Public boundaries are references, not disposable legacy occurrence data.
    for path in files_under(quarantine / "data/silver/geospatial"):
        relative = path.relative_to(quarantine / "data/silver/geospatial")
        destination = target / "reference/geospatial" / relative
        checked_copy(path, destination, digest(path))
    state = {k: v for k, v in state.items() if k != "entries"}
    state.update(status="complete", copied_files=len(source_entries),
                 inventory_sha256=digest(quarantine / "inventory.json"),
                 source_hashes=source_hashes, deletion_authorized=False)
    publish_new(target / "metadata/promotion_receipt.json", sign_document(state, key))
    publish_new(quarantine / "promotion_receipt.json", sign_document(state, key))
    return state
