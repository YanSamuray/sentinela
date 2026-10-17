"""Portable temporary directories for synthetic integration tests.

On Windows, the default pytest directory can exceed legacy path limits after
the partitioned Arrow dataset paths are appended. Its explicit private mkdir
mode can also produce inaccessible directories in the desktop sandbox. Use a
short, exclusively created directory in the ignored repository temp folder,
inheriting that folder's permissions. Other platforms retain pytest's original
tmp_path/tmp_path_factory implementation without any override.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid
import warnings

import pytest


def _has_link_in_path(path: Path) -> bool:
    return any(part.is_symlink() or part.is_junction() for part in (path, *path.parents))


def _tree_has_link(path: Path) -> bool:
    pending = [path]
    while pending:
        current = pending.pop()
        if current.is_symlink() or current.is_junction():
            return True
        with os.scandir(current) as entries:
            for entry in entries:
                child = Path(entry.path)
                if child.is_symlink() or child.is_junction():
                    return True
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)
    return False


def _cleanup_owned_directory(path: Path, parent: Path) -> None:
    # Never remove the shared parent, a redirected path, or linked descendants.
    # Link-rejection tests may deliberately leave such a tree behind.
    try:
        if _has_link_in_path(path):
            raise ValueError("linked temporary path")
        resolved = path.resolve()
        if resolved == parent or resolved.parent != parent:
            raise ValueError("temporary path outside its owner directory")
        if not resolved.exists():
            return
        if _tree_has_link(resolved):
            raise ValueError("linked temporary descendant")
        shutil.rmtree(resolved)
    except (OSError, ValueError):
        warnings.warn(
            "Synthetic temporary directory retained because safe cleanup could not be verified: "
            + str(path),
            pytest.PytestWarning,
            stacklevel=2,
        )


if os.name == "nt":
    @pytest.fixture
    def tmp_path():
        repository = Path(__file__).absolute().parents[1]
        parent = repository / "temp"
        if _has_link_in_path(parent):
            raise RuntimeError("Windows test temp parent must not contain links")
        parent.mkdir(exist_ok=True)
        parent = parent.resolve()
        if parent.parent != repository.resolve():
            raise RuntimeError("Windows test temp parent escaped the repository")
        # 64 random bits keep paths short. Exclusive mkdir also handles collision
        # safely, including when several pytest workers run concurrently.
        for _ in range(10):
            path = parent / ("t" + uuid.uuid4().hex[:16])
            try:
                path.mkdir()  # Default mode deliberately inherits the parent ACL.
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Could not reserve a unique Windows test directory")
        try:
            yield path
        finally:
            _cleanup_owned_directory(path, parent)
