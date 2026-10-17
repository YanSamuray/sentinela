"""Export only verified synthetic report/figures to the public documentation."""
import argparse
import json
from pathlib import Path
import re
import shutil

from totens.acervo import digest
from totens.local_migration import reject_links
from totens.portfolio.case import load_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--name", default="synthetic_demo")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    run = args.run.resolve()
    reject_links(run)
    if not run.is_relative_to((project / "data/portfolio_synthetic").resolve()):
        parser.error("Only runs inside the synthetic output root may be exported.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("Invalid demonstration name.")
    summary = load_case(run, project)
    if summary["mode"] != "synthetic":
        parser.error("Synthetic mode required.")
    metadata = json.loads((run / "meta.json").read_bytes())
    report = (run / "report.md").read_text(encoding="utf8")
    if "DEMONSTRAÇÃO SINTÉTICA" not in report:
        raise ValueError("synthetic_report_label_missing")
    output = project / "docs/assets" / args.name
    reject_links(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "figures").mkdir()
    names = ("quality", "monthly", "spatial", "coverage", "forecast_comparison", "forecast_test", "forecast_horizon")
    for name in names:
        shutil.copyfile(run / "figures" / (name + ".png"), output / "figures" / (name + ".png"))
    (output / "report.md").write_text(report, encoding="utf8")
    provenance = {"mode": "synthetic", "seed": 42, "real_data_exported": False,
        "run_id": summary["run_id"], "case_version": summary["case_version"],
        "git_commit_at_execution": metadata["git_commit"], "git_dirty_at_execution": metadata["git_dirty"],
        "versions": metadata["versions"], "effective_parameters": metadata["effective_parameters"],
        "implementation_sha256": metadata["implementation_sha256"],
        "source_metadata_sha256": digest(run / "meta.json"),
        "artifacts": {p.relative_to(output).as_posix(): digest(p) for p in output.rglob("*") if p.is_file()}}
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"mode": "synthetic", "figures": len(names), "output": output.relative_to(project).as_posix()}))


if __name__ == "__main__":
    main()
