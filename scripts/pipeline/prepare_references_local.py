"""Prepare versioned institutional references; no occurrence API access."""
import argparse
import json
from pathlib import Path

from totens.local_secrets import load_local_key
from totens.references import prepare_references
from totens.reference_review import revise_references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Historical initial import only; read only.")
    parser.add_argument("--version", default="cadastros_v2")
    parser.add_argument("--previous-version", help="Explicit authenticated version; required for revisions.")
    parser.add_argument("--evidence-dir", type=Path, help="Archived review bundle; revision runs offline.")
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2026)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    key = load_local_key(project / ".secrets/local_minimized_v1.dpapi", create=False)
    if args.evidence_dir:
        if not args.previous_version or args.source or args.start_year > args.end_year:
            parser.error("Revision requires previous version, valid years, and no legacy source.")
        receipt = revise_references(project / "data", key, previous_version=args.previous_version,
            version=args.version, evidence_dir=args.evidence_dir.resolve(), years=range(args.start_year, args.end_year + 1))
    else:
        if not args.source:
            parser.error("Initial historical import requires --source; revisions require --evidence-dir.")
        receipt = prepare_references(project, args.source.resolve(), project / "data", key, args.version,
                                     previous_version=args.previous_version)
    print(json.dumps({name: receipt[name] for name in ("status", "units", "equipment", "totems",
                     "ambiguous_equipment_rows", "municipal_coverage_complete")}), flush=True)


if __name__ == "__main__":
    main()
