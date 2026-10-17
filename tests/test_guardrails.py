"""Adversarial staged-index checks, using only disposable synthetic repositories."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "check_index", Path(__file__).resolve().parents[1] / "scripts/checks/check_index.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "core.autocrlf", "false")
    yield tmp_path
    # Git marks loose objects read-only on Windows. Restore write access only in
    # this disposable test repository so the existing safe tmp_path cleanup works.
    if os.name == "nt":
        for item in (tmp_path / ".git" / "objects").rglob("*"):
            if item.is_file() and not item.is_symlink():
                item.chmod(stat.S_IREAD | stat.S_IWRITE)


def stage(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    git(root, "add", "--", path)


@pytest.mark.parametrize("path", [
    "data/minimized.json", "temp/quarentena/file.json", "archive/recovered.json",
    "bi/report.pbix", ".secrets/key.dpapi", ".env", ".env.production",
    "config/settings.local.toml", "nested/DATA/file.json", "export.csv",
    "copy.parquet", "backup.zip",
])
def test_private_files_blocked_even_when_force_added(repo, path):
    stage(repo, path, b"synthetic")
    assert guard.check_index(repo)


def test_staged_notebook_is_checked_instead_of_clean_working_copy(repo):
    dirty = json.dumps({"cells": [{"outputs": [{"text": "SYNTHETIC"}], "execution_count": 1}]}).encode()
    stage(repo, "analysis.ipynb", dirty)
    (repo / "analysis.ipynb").write_text('{"cells": []}')
    assert ("analysis.ipynb", "notebook_outputs_or_attachments") in guard.check_index(repo)
    git(repo, "add", "analysis.ipynb")
    assert not guard.check_index(repo)


@pytest.mark.parametrize("notebook", [
    {"cells": [{"attachments": {"image.png": {"image/png": "synthetic"}}}]},
    {"cells": [], "metadata": {"widgets": {"state": "synthetic"}}},
    {"cells": "not-a-list"},
])
def test_embedded_content_and_invalid_notebooks_blocked(repo, notebook):
    stage(repo, "analysis.ipynb", json.dumps(notebook).encode())
    assert guard.check_index(repo)


def test_unstaged_private_file_is_not_read(repo):
    stage(repo, "example.py", b"print('synthetic')\n")
    (repo / ".env").write_bytes(b"SYNTHETIC_PRIVATE_UNTRACKED")
    assert not guard.check_index(repo)


def test_binary_and_oversized_text_are_rejected(repo):
    stage(repo, "payload.txt", b"\x00\xff")
    stage(repo, "large.txt", b"x" * (guard.MAX_BYTES + 1))
    assert set(guard.check_index(repo)) == {
        ("payload.txt", "unreviewed_binary"), ("large.txt", "oversized_blob"),
    }


def test_symlink_index_mode_is_blocked_without_following_target(repo):
    stage(repo, "pointer", b"../../private")
    oid = git(repo, "rev-parse", ":pointer").decode().strip()
    git(repo, "update-index", "--cacheinfo", f"120000,{oid},pointer")
    assert ("pointer", "link_or_submodule") in guard.check_index(repo)


def test_deleted_private_file_does_not_block_commit(repo):
    stage(repo, ".env", b"SYNTHETIC")
    git(repo, "rm", "--cached", "--force", ".env")
    assert not guard.check_index(repo)


def test_template_and_clean_notebook_are_allowed(repo):
    stage(repo, ".env.example", b"TOKEN=\n")
    stage(repo, "analysis.ipynb", b'{"cells": [{"outputs": [], "execution_count": null}]}')
    assert not guard.check_index(repo)


STORYTELLING = "notebooks/99_storytelling_resultados.ipynb"
SYNTHETIC_DISPLAY = {"output_type": "display_data", "data": {"text/markdown": "**Modo: synthetic — execução: demo**"}}


def executed_notebook(*outputs):
    return json.dumps({"cells": [{"cell_type": "code", "execution_count": 1, "outputs": list(outputs)}]}).encode()


def test_storytelling_notebook_accepts_synthetic_outputs(repo):
    stage(repo, STORYTELLING, executed_notebook(SYNTHETIC_DISPLAY))
    assert not guard.check_index(repo)


@pytest.mark.parametrize("outputs", [
    [],
    [{"output_type": "stream", "name": "stdout", "text": "unmarked execution"}],
    [SYNTHETIC_DISPLAY, {"output_type": "display_data", "data": {"text/markdown": "**Modo: private — execução: real**"}}],
    [SYNTHETIC_DISPLAY, {"output_type": "stream", "name": "stderr", "text": "warning"}],
    [SYNTHETIC_DISPLAY, {"output_type": "stream", "name": "stdout", "text": "C:\\Users\\someone\\run"}],
    [SYNTHETIC_DISPLAY, {"output_type": "error", "ename": "ValueError", "evalue": "synthetic", "traceback": []}],
])
def test_storytelling_notebook_rejects_non_synthetic_outputs(repo, outputs):
    stage(repo, STORYTELLING, executed_notebook(*outputs))
    assert (STORYTELLING, "notebook_outputs_or_attachments") in guard.check_index(repo)


def test_synthetic_outputs_are_rejected_in_other_notebooks(repo):
    stage(repo, "notebooks/01_eda.ipynb", executed_notebook(SYNTHETIC_DISPLAY))
    assert ("notebooks/01_eda.ipynb", "notebook_outputs_or_attachments") in guard.check_index(repo)


def test_secret_hook_checks_staged_content_and_redacts_output(repo):
    executable = shutil.which("pre-commit")
    if executable is None:
        pytest.skip("Install pre-commit 4.5.1 to exercise the real hook (required in CI)")
    project = Path(__file__).resolve().parents[1]
    for name in (".pre-commit-config.yaml",):
        stage(repo, name, (project / name).read_bytes())
    baseline = json.loads((project / ".secrets.baseline").read_bytes())
    baseline["results"] = {}
    stage(repo, ".secrets.baseline", json.dumps(baseline).encode())
    stage(repo, "example.py", b"value = 'safe'\n")
    git(repo, "-c", "user.name=Synthetic Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "synthetic fixture")

    # Assemble an inert detector-shaped token so the test source is not itself
    # allowlisted or indistinguishable from a hardcoded credential.
    token = "gh" + "p_" + hashlib.sha256(b"synthetic-guardrail").hexdigest()[:36]
    staged = f"credential = '{token}'\n".encode()
    stage(repo, "example.py", staged)
    safe = b"value = 'safe working copy'\n"
    (repo / "example.py").write_bytes(safe)
    result = subprocess.run(
        [executable, "run", "detect-secrets"], cwd=repo, capture_output=True,
    )
    assert result.returncode == 1, result.stdout.decode(errors="replace")
    assert b"Failed" in result.stdout and b'"type": "GitHub Token"' in result.stdout
    assert token.encode() not in result.stdout + result.stderr
    assert git(repo, "show", ":example.py") == staged
    assert (repo / "example.py").read_bytes() == safe

    # Conversely, an unsafe unstaged edit must not contaminate the staged scan.
    git(repo, "add", "example.py")
    (repo / "example.py").write_bytes(staged)
    result = subprocess.run(
        [executable, "run", "detect-secrets"], cwd=repo, capture_output=True,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    assert git(repo, "show", ":example.py") == safe
    assert (repo / "example.py").read_bytes() == staged
