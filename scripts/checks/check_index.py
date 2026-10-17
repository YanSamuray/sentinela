"""Check the complete Git index without reading private working-tree files."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import subprocess


PRIVATE_ROOTS = {
    "data", "temp", "archive", "bi", "legacy", ".secrets", "reports",
    "predictions", "metadata", ".venv", "venv",
}
PRIVATE_FILES = {"config/settings.local.toml", "config/privacy_categories.local.json"}
PRIVATE_SUFFIXES = {
    ".parquet", ".feather", ".arrow", ".pbix", ".pbit", ".dpapi", ".pfx",
    ".p12", ".key", ".pem", ".sqlite", ".sqlite3", ".db", ".zip", ".7z",
    ".xlsx", ".xls", ".csv", ".jsonl", ".pkl", ".pickle",
}
MAX_BYTES = 5 * 1024 * 1024
PUBLIC_FIGURES = "docs/assets/synthetic_demo/figures/"
# The storytelling notebook is versioned with its synthetic execution only.
SYNTHETIC_NOTEBOOK = "notebooks/99_storytelling_resultados.ipynb"
SYNTHETIC_MARKER = "Modo: synthetic"
NON_SYNTHETIC_OUTPUT = re.compile(r"(?i)Modo: private|[a-z]:[\\/]{1,2}users|/home/|onedrive")


def git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, input=input_data,
    ).stdout


def path_issue(path: str) -> str | None:
    normalized = path.lower()
    parts = PurePosixPath(normalized).parts
    if not parts or any(ord(c) < 32 for c in path) or "\\" in path:
        return "invalid_path"
    if any(p in PRIVATE_ROOTS for p in parts):
        return "private_directory"
    if normalized in PRIVATE_FILES:
        return "private_configuration"
    if parts[-1] != ".env.example" and (
        parts[-1] == ".env" or parts[-1].startswith(".env.")
    ):
        return "environment_secret_file"
    if PurePosixPath(normalized).suffix in PRIVATE_SUFFIXES:
        return "private_or_unreviewed_data_format"
    return None


def _synthetic_outputs_only(cells: list[dict]) -> bool:
    """Outputs must carry the synthetic marker and no error, stderr, private mode or local path."""
    texts = []
    for cell in cells:
        for output in cell.get("outputs") or []:
            if output.get("output_type") == "error" or output.get("name") == "stderr":
                return False
            data = {k: v for k, v in (output.get("data") or {}).items() if k != "image/png"}
            texts.append(json.dumps([data, output.get("text", "")], ensure_ascii=False))
    text = "\n".join(texts)
    return SYNTHETIC_MARKER in text and not NON_SYNTHETIC_OUTPUT.search(text)


def content_issues(path: str, content: bytes) -> list[str]:
    if len(content) > MAX_BYTES:
        return ["oversized_blob"]
    if path.lower().endswith(".ipynb"):
        try:
            notebook = json.loads(content)
            cells = notebook["cells"]
            if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
                return ["invalid_notebook"]
            if any(c.get("attachments") for c in cells):
                return ["notebook_outputs_or_attachments"]
            if any(c.get("outputs") or c.get("execution_count") is not None for c in cells):
                if path != SYNTHETIC_NOTEBOOK or not _synthetic_outputs_only(cells):
                    return ["notebook_outputs_or_attachments"]
            if notebook.get("metadata", {}).get("widgets"):
                return ["notebook_widget_state"]
        except (ValueError, KeyError, TypeError, AttributeError):
            return ["invalid_notebook"]
    try:
        content.decode("utf-8-sig")
        if b"\x00" not in content:
            return []
    except UnicodeDecodeError:
        pass
    if path.startswith(PUBLIC_FIGURES) and path.endswith(".png") and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return []  # Generated synthetic figures still require human/provenance review.
    return ["unreviewed_binary"]


def check_index(root: Path) -> list[tuple[str, str]]:
    issues = []
    candidates = []
    entries = git(root, "ls-files", "--stage", "-z").split(b"\0")
    for entry in filter(None, entries):
        header, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = header.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if stage != "0":
            issues.append((path, "unmerged_index"))
            continue
        if mode not in {"100644", "100755"}:
            issues.append((path, "link_or_submodule"))
            continue
        issue = path_issue(path)
        if issue:
            issues.append((path, issue))
            continue  # Never read content from forbidden paths.
        candidates.append((path, oid))
    if not candidates:
        return issues
    request = "".join(f"{oid}\n" for _, oid in candidates).encode("ascii")
    sizes = git(root, "cat-file", "--batch-check", input_data=request).splitlines()
    if len(sizes) != len(candidates):
        raise ValueError("incomplete_index_metadata")
    readable = []
    for (path, oid), metadata in zip(candidates, sizes):
        found_oid, kind, size = metadata.decode("ascii").split()
        if found_oid != oid or kind != "blob":
            raise ValueError("invalid_index_blob")
        if int(size) > MAX_BYTES:
            issues.append((path, "oversized_blob"))
            continue
        readable.append((path, oid, int(size)))
    if not readable:
        return issues
    request = "".join(f"{oid}\n" for _, oid, _ in readable).encode("ascii")
    stream = BytesIO(git(root, "cat-file", "--batch", input_data=request))
    for path, oid, size in readable:
        if stream.readline() != f"{oid} blob {size}\n".encode("ascii"):
            raise ValueError("invalid_index_content")
        content = stream.read(size)
        if len(content) != size or stream.read(1) != b"\n":
            raise ValueError("incomplete_index_content")
        issues.extend((path, issue) for issue in content_issues(path, content))
    return issues


def main() -> int:
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        issues = check_index(root)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("Index check failed: unable to inspect the complete index.")
        return 1
    for path, issue in issues:
        # JSON escapes control characters; do not print file contents or secrets.
        print(json.dumps({"path": path, "issue": issue}, ensure_ascii=True))
    print(f"Index privacy check: {len(issues)} issue(s).")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
