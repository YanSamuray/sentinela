"""Execute the storytelling notebook in place against a verified synthetic run.

The notebook is versioned with synthetic outputs only. Execution happens in this
process (IPython InteractiveShell, history disabled) because separate kernels hit
Windows ACL restrictions. Any error, stderr, private-mode marker or absolute path
in the outputs aborts before the notebook is written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import nbformat
from IPython import InteractiveShell
from IPython.utils.capture import capture_output
from traitlets.config import Config

from totens.local_migration import reject_links
from totens.portfolio.case import load_case

NOTEBOOK = Path("notebooks/99_storytelling_resultados.ipynb")
FORBIDDEN = re.compile(r"(?i)Modo: private|[a-z]:[\\/]{1,2}users|/home/|onedrive")


def execute(project: Path, run: Path) -> dict:
    source = project / NOTEBOOK
    notebook = nbformat.read(source, as_version=4)
    config = Config()
    config.HistoryManager.enabled = False
    shell = InteractiveShell.instance(config=config)
    os.environ["TOTENS_PORTFOLIO_RUN"] = str(run)
    count = figures = 0
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        count += 1
        with capture_output() as captured:
            result = shell.run_cell(cell.source, store_history=False)
        if result.error_in_exec or result.error_before_exec:
            raise RuntimeError("notebook_cell_failed")
        if captured.stderr:
            raise RuntimeError("notebook_stderr_output")
        cell.execution_count = count
        cell.outputs = []
        if captured.stdout:
            cell.outputs.append(nbformat.v4.new_output("stream", name="stdout", text=captured.stdout))
        for output in captured.outputs:
            cell.outputs.append(nbformat.v4.new_output("display_data", data=output.data, metadata=output.metadata))
            figures += "image/png" in output.data
    text = json.dumps([o.get("data", {}).get("text/markdown", "") or o.get("text", "")
                       for c in notebook.cells if c.cell_type == "code" for o in c.outputs])
    text += json.dumps([{k: v for k, v in o.get("data", {}).items() if k != "image/png"}
                        for c in notebook.cells if c.cell_type == "code" for o in c.outputs])
    if "Modo: synthetic" not in text or FORBIDDEN.search(text):
        raise RuntimeError("notebook_outputs_not_publishable")
    nbformat.write(notebook, source)
    return {"status": "complete", "mode": "synthetic", "run_id": run.name, "code_cells": count, "figures": figures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Synthetic run directory under data/portfolio_synthetic.")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    run = args.run.resolve()
    reject_links(run)
    if not run.is_relative_to((project / "data/portfolio_synthetic").resolve()):
        parser.error("Only runs inside the synthetic output root may be used.")
    if load_case(run, project)["mode"] != "synthetic":
        parser.error("Synthetic mode required.")
    os.chdir(project)
    print(json.dumps(execute(project, run)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
