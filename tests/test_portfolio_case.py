import json
from pathlib import Path

import pytest

import totens.portfolio.case as case


def project(tmp_path):
    root = tmp_path / "p"
    (root / "config").mkdir(parents=True)
    (root / "config/settings.minimized.toml").write_text('[paths]\ndata_root="data/private_minimized_v1"\n')
    return root


def test_synthetic_complete_case_is_reproducible_without_private_inputs(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(case, "load_local_key", lambda *args, **kwargs: pytest.fail("Synthetic mode accessed a real key"))
    monkeypatch.setattr(case, "private_ledger", lambda *args, **kwargs: pytest.fail("Synthetic mode accessed private data"))
    output = case.run_case(mode="synthetic", project=root, run_id="it")
    summary = case.load_case(output, project=root)
    assert summary["mode"] == "synthetic" and summary["source"]["seed"] == 42
    assert summary["forecast"]["selection_frozen_before_test"]
    assert summary["forecast"]["test_blocks"] == 3
    assert len(list((output / "figures").glob("*.png"))) == 7
    assert (output / "report.md").exists()
    assert not (root / ".secrets").exists()
    metadata = json.loads((output / "meta.json").read_bytes())
    assert metadata["status"] == "complete" and metadata["artifacts"]
    with pytest.raises(case.CaseError, match="run_destination_exists"):
        case.run_case(mode="synthetic", project=root, run_id="it")
    (output / "daily.csv").write_text("tampered")
    with pytest.raises(case.CaseError, match="case_artifact_changed"):
        case.load_case(output, project=root)


@pytest.mark.parametrize("run_id", ["../escape", "/outside", "a/b", "a\\b"])
def test_run_id_cannot_escape_output_root(tmp_path, run_id):
    root = project(tmp_path)
    with pytest.raises(case.CaseError, match="invalid_run_id"):
        case.run_case(mode="synthetic", project=root, run_id=run_id)
    assert not (root / "data").exists()


def test_legacy_source_cannot_be_selected_as_private_dataset(tmp_path):
    root = project(tmp_path)
    config = root / "config/settings.minimized.toml"
    config.write_text('[paths]\ndata_root="data"\n')
    with pytest.raises(case.CaseError):
        case.run_case(mode="private", project=root, run_id="it")


def test_versioned_notebook_has_only_synthetic_outputs_and_defaults_to_synthetic():
    root = Path(__file__).resolve().parents[1]
    notebook = json.loads((root / "notebooks/99_storytelling_resultados.ipynb").read_bytes())
    outputs = json.dumps([o for c in notebook["cells"] for o in c.get("outputs") or []], ensure_ascii=False)
    if outputs != "[]":
        assert "Modo: synthetic" in outputs and "Modo: private" not in outputs
    source = "\n".join("".join(c["source"]) for c in notebook["cells"])
    assert "mode='synthetic'" in source and "TOTENS_PORTFOLIO_RUN" in source


def test_configuration_changes_actual_forecast_and_default_cluster(tmp_path):
    root = project(tmp_path)
    with (root / "config/settings.minimized.toml").open("a") as stream:
        stream.write('\n[portfolio.forecast]\nhorizon=14\nvalidation_blocks=2\ntest_blocks=1\n'
                     '\n[portfolio.spatial]\neps=100\nmin_samples=25\n')
    output = case.run_case(mode="synthetic", project=root, run_id="configured")
    summary = case.load_case(output, project=root)
    import pandas as pd
    forecast = pd.read_csv(output / "historical_projection.csv")
    assert len(forecast) == 14 and forecast.horizon.max() == 14
    assert summary["forecast"]["validation_blocks"] == 2
    assert summary["forecast"]["test_blocks"] == 1
    assert summary["spatial"]["default_clustering"]["eps_m"] == 100
    assert summary["spatial"]["default_clustering"]["min_samples"] == 25
