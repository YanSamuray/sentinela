"""Minimize local historical occurrences without API access or source writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from totens.local_migration import MigrationError, compile_catalog, migrate, publish_new
from totens.local_secrets import SecretError, load_local_key
from totens.privacy import PrivacyError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/bronze/ocorrencia"))
    parser.add_argument("--destination", type=Path, default=Path("data/private_minimized_v1"))
    parser.add_argument("--catalog", type=Path, required=True, help="Explicitly reviewed category catalog.")
    parser.add_argument("--key-file", type=Path, default=Path(".secrets/local_minimized_v1.dpapi"))
    parser.add_argument("--create-key", action="store_true", help="Create a sealed key only if missing.")
    parser.add_argument("--prepare-catalog", type=Path,
                        help="Only compile reviewed categories + HMAC category tokens to this new file.")
    args = parser.parse_args()
    try:
        key = load_local_key(args.key_file, create=args.create_key)
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        def progress(value: dict) -> None:
            done = value.get("files_done", value.get("catalog_files_done", 0))
            if done % 25 == 0 or done == value.get("files_total"):
                print(json.dumps(value), flush=True)
        if args.prepare_catalog:
            compiled = compile_catalog(args.source, key, reviewed_catalog=catalog, on_progress=progress)
            publish_new(args.prepare_catalog, compiled)
            print(json.dumps({"status": "catalog_prepared", "category_paths": len(compiled)}), flush=True)
            return 0
        summary = migrate(args.source, args.destination, key, category_catalog=catalog, on_progress=progress)
        print(json.dumps({"status": summary["status"], "files": summary["source_files"],
                          "records": summary["counts"]["records"]}), flush=True)
        return 0
    except (MigrationError, PrivacyError, SecretError) as exc:
        print(json.dumps({"status": "blocked", "code": exc.code}), flush=True)
    except Exception:
        # Never expose raw JSON, remote strings, paths or secrets in tracebacks.
        print(json.dumps({"status": "blocked", "code": "local_migration_failed"}), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
