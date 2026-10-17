"""Run the offline Data Science case; default to entirely synthetic inputs."""
import argparse
import json
from pathlib import Path

from totens.portfolio.case import run_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "private"), default="synthetic")
    parser.add_argument("--config", type=Path, default=Path("config/settings.minimized.toml"))
    parser.add_argument("--run-id", help="Exclusive output directory; an existing run is never replaced.")
    args = parser.parse_args()
    try:
        output = run_case(mode=args.mode, config=args.config, run_id=args.run_id,
                          progress=lambda item: print(json.dumps(item, ensure_ascii=False), flush=True))
        print(json.dumps({"status": "complete", "mode": args.mode, "report": str(output / "report.md")}, ensure_ascii=False))
        return 0
    except Exception as error:
        # Paths, raw identifiers and records must never appear in error text.
        print(json.dumps({"status": "failed", "code": "portfolio_case_failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
