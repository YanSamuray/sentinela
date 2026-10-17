"""Export a private, authenticated review of institutional references."""
import argparse
import json
from pathlib import Path

from totens.acervo import digest
from totens.local_migration import publish_new, reject_links, sign_document, verify_document
from totens.local_secrets import load_local_key
from totens.references import authenticated_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="cadastros_v2")
    args = parser.parse_args()
    if Path(args.version).name != args.version or args.version in (".", ".."):
        parser.error("Invalid reference version")
    project = Path(__file__).resolve().parents[2]
    root = project / "data"
    key = load_local_key(project / ".secrets/local_minimized_v1.dpapi", create=False)
    folder = root / "reference" / args.version
    receipt = verify_document(json.loads((folder / "receipt.json").read_bytes()), key)
    output = root / "reference_review_reports" / args.version
    reject_links(output)
    output.mkdir(parents=True, exist_ok=False)
    tables = {}
    inputs = {str(folder / "receipt.json"): digest(folder / "receipt.json")}
    for name in ("unit_review", "equipment_review", "municipal_coverage", "legacy_calendar_review", "unidades"):
        tables[name], hashes = authenticated_reference(root, args.version, name + ".parquet", key)
        inputs.update(hashes)
        tables[name].to_csv(output / (name + ".csv"), index=False)
    units = tables["unidades"]
    summary = {name: receipt[name] for name in ("units", "equipment", "totems", "corporation_corrections",
        "municipality_unresolved", "municipality_years_researched", "coverage_status", "legacy_decisions", "official_events")}
    summary.update({"status": "complete", "version": args.version,
        "units_exact_type_unknown": int(units.tipo_unidade.eq("UNIDADE_POLICIAL_TIPO_NAO_DETERMINADO").sum()),
        "nature_unknown": int(units.natureza.eq("NAO_DETERMINADA").sum()),
        "location_status": units.location_status.value_counts().to_dict(),
        "corporation_status": units.corporation_status.value_counts().to_dict(),
        "geometric_municipality_resolutions": int(units.municipality_method.str.startswith("retrospective").sum()),
        "implementation_sha256": {p.relative_to(project).as_posix(): digest(p) for p in
            (project / "src/totens/reference_review.py", project / "src/totens/reference_calendar.py", project / "src/totens/references.py")},
        "reference_receipt_sha256": digest(folder / "receipt.json")})
    report = "# Revisão privada das referências\n\n" + json.dumps(summary, ensure_ascii=False, indent=2)
    report += ("\n\nOs 95 registros receberam revisão descritiva. Três tipos exatos e algumas naturezas permanecem desconhecidos. "
        "Classificação pelo nome não comprova operação, efetivo ou disponibilidade histórica. "
        "Município derivado do polígono é uma atribuição geométrica retrospectiva; localização física não foi comprovada.\n\n"
        "As buscas abrangem 312 combinações município–ano. As evidências municipais são parciais: "
        "os atos estaduais comprovam somente o expediente de seu âmbito, não a aplicabilidade geral nem a completude municipal. "
        "A concordância de data com a base antiga não confirma sua descrição. Ausência permanece desconhecida.\n\n"
        "Os arquivos CSV contêm UUIDs e nomes institucionais e permanecem privados. Nenhuma exclusão foi realizada.\n")
    (output / "report.md").write_text(report, encoding="utf8")
    for path, expected in inputs.items():
        if digest(Path(path)) != expected:
            raise ValueError("review_input_changed")
    summary["inputs"] = {str(Path(p).relative_to(project)): h for p, h in inputs.items()}
    summary["outputs"] = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    publish_new(output / "receipt.json", sign_document(summary, key))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("inputs", "outputs", "implementation_sha256")}), flush=True)


if __name__ == "__main__":
    main()
