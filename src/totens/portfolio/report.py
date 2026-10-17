"""Private aggregate figures and a report generated from measured results."""
from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd
import shapely

matplotlib.use("Agg")


def _save(fig, target: Path):
    fig.tight_layout()
    fig.savefig(target, dpi=160, bbox_inches="tight")
    plt.close(fig)


def figures(output: Path, summary: dict, daily: pd.DataFrame, tables: dict, boundary, totems: pd.DataFrame):
    folder = output / "figures"
    folder.mkdir()
    prefix = "SINTÉTICO — " if summary["mode"] == "synthetic" else "PRIVADO — "
    partition = summary["quality"]["partition"]
    names = {"date_invalid_or_conflicting": "Data inválida/conflitante", "risk_conflicting": "Risco conflitante",
             "risk_unknown": "Risco desconhecido", "known_high": "Risco alto da fonte", "known_other": "Outros níveis"}
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh([names[k] for k in partition], list(partition.values()), color="#177d89")
    ax.set(xlabel="Protocolos distintos", title=prefix + "Consolidação e elegibilidade")
    ax.set_xlim(0, max(partition.values()) * 1.2)
    for i, value in enumerate(partition.values()):
        ax.text(value, i, f" {value:,}", va="center", fontsize=9)
    _save(fig, folder / "quality.png")
    fig, ax = plt.subplots(figsize=(11, 4))
    monthly = daily.set_index("date").resample("MS")[["known_high", "risk_unknown", "risk_conflicting"]].sum()
    ax.plot(monthly.index, monthly.known_high, label="Alto risco observado", color="#177d89")
    ax.plot(monthly.index, monthly.known_high + monthly.risk_unknown + monthly.risk_conflicting,
            label="Limite por classificação desconhecida/conflitante", color="#b07c20", linestyle="--")
    ax.set(ylabel="Protocolos / mês", title=prefix + "Evolução da demanda registrada")
    partial = daily.groupby(daily.date.dt.to_period("M")).complete_month.all()
    for period in partial[~partial].index:
        ax.axvspan(period.start_time, period.end_time, color="#b07c20", alpha=.15,
                   label="Mês parcial" if period == partial[~partial].index[0] else None)
    ax.legend(fontsize=8)
    ax.grid(alpha=.15)
    _save(fig, folder / "monthly.png")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    polygons = list(boundary.geoms) if hasattr(boundary, "geoms") else [boundary]
    grid, clusters = tables["grid"], tables["clusters"]
    for ax in axes:
        for polygon in polygons:
            x, y = polygon.exterior.xy
            ax.plot(x, y, color="#62737c", linewidth=.7)
        ax.scatter(totems.longitude, totems.latitude, marker="^", s=24, color="#a52c3e", label="Totens do cadastro")
        ax.set(xlabel="Longitude", ylabel="Latitude")
        ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.set_aspect(1 / max(.1, np.cos(np.radians(shapely.centroid(boundary).y))))
    plot_grid = grid.sort_values("n")
    scatter = axes[0].scatter(plot_grid.longitude, plot_grid.latitude, c=np.log1p(plot_grid.n), s=9, cmap="viridis", alpha=.8)
    axes[0].set_title(prefix + "Grade de 1 km")
    fig.colorbar(scatter, ax=axes[0], label="log(1 + registros)", shrink=.6)
    if len(clusters):
        axes[1].scatter(clusters.longitude, clusters.latitude, s=15 + 90 * clusters.n / clusters.n.max(),
                        facecolor="#177d89", alpha=.65, label="Centros ponderados dos clusters")
    axes[1].set_title(prefix + f"DBSCAN: {summary['spatial']['default_clustering']['eps_m']} m / {summary['spatial']['default_clustering']['min_samples']} registros")
    axes[1].legend(fontsize=7, loc="upper right")
    _save(fig, folder / "spatial.png")
    coverage = summary["spatial"]["coverage"]
    fig, ax = plt.subplots(figsize=(8, 4))
    values = [100 * item["fraction"] for item in coverage.values()]
    ax.bar([f"{r} m" for r in coverage], values, color="#177d89")
    ax.set(ylabel="% do conjunto espacial elegível", title=prefix + "Proximidade geométrica dos totens",
           ylim=(0, min(100, max(10, max(values) * 1.2))))
    for i, value in enumerate(values):
        ax.text(i, value, f"{value:.2f}%", ha="center", va="bottom", fontsize=10)
    _save(fig, folder / "coverage.png")
    scores = tables["forecast_performance"]
    models = scores.model.unique()
    fig, ax = plt.subplots(figsize=(11, 4))
    for i, split in enumerate(("validation", "test")):
        values = scores[scores.split.eq(split)].set_index("model").reindex(models).mae
        ax.bar(np.arange(len(models)) + i * .35, values, width=.35, label={"validation": "Validação", "test": "Teste"}[split])
    ax.set_xticks(np.arange(len(models)) + .175, models, rotation=20, ha="right", fontsize=8)
    ax.set(ylabel="MAE: registros por dia", title=prefix + "Comparação de previsão")
    ax.legend()
    _save(fig, folder / "forecast_comparison.png")
    test = tables["forecast_predictions"]
    test = test[test.split.eq("test") & test.model.eq(summary["forecast"]["selected_model"])]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(test.date, test.observed, color="#4a4a4a", label="Observado")
    ax.plot(test.date, test.predicted, color="#177d89", label="Previsão fora do treinamento")
    for origin in test.origin.unique():
        ax.axvline(origin, color="#b07c20", alpha=.4, linestyle="--")
    ax.set(ylabel="Alto risco / dia", title=prefix + f"Teste temporal: {summary['forecast']['test_blocks']} origens, {summary['forecast']['horizon_days']} dias cada")
    ax.legend(fontsize=8)
    _save(fig, folder / "forecast_test.png")
    fig, ax = plt.subplots(figsize=(8, 4))
    errors = tables["forecast_error_by_horizon"]
    ax.plot(errors.horizon, errors.mae, color="#177d89")
    ax.set(xlabel="Dias desde a origem da previsão", ylabel="MAE", title=prefix + "Erro por horizonte")
    _save(fig, folder / "forecast_horizon.png")


def render_report(summary: dict) -> str:
    quality, temporal, spatial, forecast = (summary[k] for k in ("quality", "temporal", "spatial", "forecast"))
    synthetic = summary["mode"] == "synthetic"
    label = "DEMONSTRAÇÃO SINTÉTICA — resultados sem interpretação operacional" if synthetic else "ANÁLISE PRIVADA — registros reais pseudonimizados"
    rows = "\n".join(f"| {name} | {count:,} |" for name, count in quality["partition"].items())
    coverage = "\n".join(f"| {radius} m | {v['inside']:,} / {v['denominator']:,} | {v['fraction']:.1%} |" for radius, v in spatial["coverage"].items())
    window_coverage = "\n".join(f"| {radius} m | {v['inside']:,} / {v['denominator']:,} | {v['fraction']:.1%} |"
                                if v['fraction'] is not None else f"| {radius} m | 0 / 0 | indefinida |"
                                for radius, v in spatial["window_coverage"].items())
    sensitivity = "\n".join(f"| {r['eps_m']} | {r['min_samples']} | {r['clusters']} | {r['noise_records']:,} / {r['records']:,} |"
                             for r in spatial["sensitivity"])
    semesters = "\n".join(f"| {r['period']} | {r['records']:,} | {r['clusters']} | {r['noise_records']:,} |"
                          for r in spatial["semesters"])
    risk_mapping = summary["source"].get("risk_mapping", {"Alto": 3, "Baixo": 1} if synthetic else {})
    taxonomy = "\n".join(f"| {label} | {level if level is not None else 'desconhecido'} |" for label, level in risk_mapping.items())
    if forecast["selected_test_mae"] > forecast["best_baseline_test_mae"]:
        predictive_conclusion = "O candidato escolhido na validação teve erro maior que o melhor baseline no teste. Este acervo não evidencia ganho consistente da alternativa selecionada sobre a referência simples; a avaliação deve ser ampliada antes de uso operacional."
    elif forecast["selected_model"] in ("seasonal_week", "weekday_84"):
        predictive_conclusion = "Um baseline foi selecionado na validação e permaneceu competitivo no teste. A complexidade adicional dos modelos não é necessária para justificar esse resultado."
    else:
        predictive_conclusion = "O modelo selecionado apresentou MAE menor que os baselines no teste. Esse resultado se restringe aos três blocos históricos avaliados e precisa de confirmação com dados atualizados."
    legacy = summary["legacy_reconciliation"]
    reconciliation = (f"Gold histórica: {legacy['legacy_rows']:,} linhas; diferença líquida de {legacy['net_difference']:,}. "
                      f"{legacy['excess_multiplicity_same_key_date']:,} excedentes compartilham protocolo e instante com o acervo atual; "
                      f"{legacy['surplus_with_different_holiday_labels']:,} estão associados a rótulos de feriado diferentes. "
                      "Essa associação descreve a diferença; não demonstra sua causa histórica.") if legacy.get("status") == "complete" else "Comparação histórica indisponível ou não aplicável à demonstração sintética."
    performance = "\n".join(f"| {r['split']} | {r['model']} | {r['mae']:.3f} | {r['rmse']:.3f} | {r['mase']:.3f} |"
                            if r['mase'] is not None else f"| {r['split']} | {r['model']} | {r['mae']:.3f} | {r['rmse']:.3f} | indefinido |"
                            for r in summary["forecast_metrics"])
    reference = summary.get("references") or {}
    reference_note = ("Cadastros, equipamentos e geografia desta demonstração são sintéticos; nenhuma referência real foi lida."
        if synthetic else f"Referências autenticadas: {reference.get('version', 'contrato histórico')}. "
        "IDs identificam registros cadastrais; identidade física e funcionamento histórico podem permanecer não comprovados. "
        "O calendário municipal distingue cobertura desconhecida de ausência de feriado e preserva o alcance de cada ato. "
        "As referências municipais não entram nesta previsão estadual. O experimento separado de calendário original usa regras da biblioteca, "
        "sem incorporar silenciosamente transferências administrativas dos atos oficiais.")
    return f"""# Alto risco, cobertura territorial e previsão

**{label}**

## Problema e unidade de análise

Onde se concentram registros classificados como alto risco, qual sua proximidade dos totens e com que precisão prever o volume diário? A unidade é um protocolo consolidado, classificado pelo nível da fonte (alto ou superior), com data de criação convertida para America/Porto_Velho. O alvo representa demanda registrada no acervo.

{reference_note}

## Qualidade e consolidação

Foram lidas **{quality['input_rows']:,} linhas**, correspondentes a **{quality['distinct_protocols']:,} protocolos**. As **{quality['excess_rows']:,} linhas excedentes** compreendem {quality['identical_excess_rows']:,} repetições idênticas e {quality['different_excess_rows']:,} versões com diferenças. Campos usados na análise exigem concordância entre versões; a ordem dos arquivos não determina uma versão preferida.

| Classificação de elegibilidade | Protocolos |
|---|---:|
{rows}

![Qualidade](figures/quality.png)

| Rótulo revisado da fonte | Nível analítico |
|---|---:|
{taxonomy}

Rótulos sem mapeamento permanecem desconhecidos; nunca são tratados como baixo risco. Protocolos sem data válida são contabilizados na exclusão global e não recebem um dia artificial no calendário.

{reconciliation}

Modo da evidência: {legacy.get('evidence_mode', 'recalculada na execução original ou sintética')}.

## Exploração temporal

O calendário vai de {temporal['start_date']} a {temporal['end_date']}; há {temporal['gap_days']} dias fora das janelas declaradas. Meses parciais: {', '.join(temporal['partial_months']) or 'nenhum'}. Zero significa zero observado no acervo. Os nomes dos arquivos não comprovam completude de extração. Alterações mensais grandes são sinalizadas somente entre meses adjacentes completos, sem exclusão automática de picos. `summary.json` registra os meses sinalizados; `daily.csv` preserva os volumes, desconhecidos e ausências.

![Evolução](figures/monthly.png)

## Geografia e proximidade

Dos {spatial['high_risk_protocols']:,} protocolos de alto risco, {spatial['high_risk_coordinate_invalid_or_conflicting']:,} têm coordenadas ausentes, inválidas ou conflitantes e {spatial['high_risk_outside_ro']:,} ficam fora do polígono. O denominador espacial é **{spatial['spatial_eligible']:,}**. A janela de clustering vai de {spatial['window_start']} a {spatial['window_end']}, com {spatial['window_records']:,} registros.

| Raio | Dentro / denominador espacial | Proporção |
|---|---:|---:|
{coverage}

Essa tabela usa todo o acervo espacial elegível. Na janela de 12 meses usada para clustering:

| Raio | Dentro / denominador da janela | Proporção |
|---|---:|---:|
{window_coverage}

![Cobertura](figures/coverage.png)
![Grade e clustering](figures/spatial.png)

A referência de grade tem células de {spatial['grid_size_m']} metros em projeção equivalente local. DBSCAN usa Haversine, coordenadas coincidentes com pesos e vizinhanças esparsas em blocos. A configuração previamente fixada ({spatial['default_clustering']['eps_m']} m, mínimo {spatial['default_clustering']['min_samples']}) encontrou **{spatial['default_clustering']['clusters']} clusters**, com fração de ruído {spatial['default_clustering']['noise_fraction'] if spatial['default_clustering']['noise_fraction'] is not None else 'indefinida'}. A tabela `cluster_sensitivity.csv` contém {len(spatial['sensitivity'])} configurações. A persistência entre semestres é medida pela sobreposição das células da grade configurada com pontos agrupados: Jaccard = {spatial['semester_clustered_grid_jaccard']}.

| Distância (m) | Mínimo de registros | Clusters | Ruído / volume da janela |
|---:|---:|---:|---:|
{sensitivity}

| Semestre | Registros | Clusters | Registros em ruído |
|---|---:|---:|---:|
{semesters}

As configurações e os semestres usam populações com volumes diferentes; o limiar configurado de registros pode afetar a comparação entre semestres. Baixa sobreposição e sensibilidade a parâmetros impedem tratar os clusters como áreas estáveis sem investigação adicional. As células mais frequentes e os centros ponderados para essa investigação estão nas tabelas privadas `grid.csv` e `clusters.csv`.

Proximidade é geométrica e usa o cadastro disponível de {spatial['totem_points']} pontos. As datas de operação por equipamento e outros fatores de cobertura não foram identificados. Clusters são áreas para investigar, sem estimativa de ocorrências evitadas ou efeito causal.

## Previsão retrospectiva

A série modelada vai de {forecast['series_start']} a {forecast['series_end']}. O horizonte é de {forecast['horizon_days']} dias. {forecast['validation_blocks']} blocos anteriores ao teste foram usados para validação, com treino crescente; {forecast['test_blocks']} blocos formam o teste. A seleção foi congelada antes de ler os resultados do teste. Lags e médias móveis usam apenas o passado; durante cada horizonte, lags futuros usam previsões anteriores.

Foram comparados repetição semanal, média por dia da semana dos últimos 84 dias e quatro configurações de boosting Poisson, com validação aleatória interna desativada. Modelo selecionado: **{forecast['selected_model']}**. Seu MAE de teste é **{forecast['selected_test_mae']:.3f} registros/dia**, frente a {forecast['best_baseline_test_mae']:.3f} do melhor baseline no teste. Essa comparação não altera a seleção feita na validação.

{predictive_conclusion}

| Conjunto | Modelo | MAE | RMSE médio dos blocos | MASE médio dos blocos |
|---|---|---:|---:|---:|
{performance}

![Comparação](figures/forecast_comparison.png)
![Teste](figures/forecast_test.png)
![Horizonte](figures/forecast_horizon.png)

A projeção de {forecast['historical_projection_start']} a {forecast['historical_projection_end']} é um exercício a partir do corte histórico, e não uma previsão da demanda atual. Intervalos probabilísticos não foram estimados. A disponibilidade das classificações de risco nas origens históricas não foi comprovada: a avaliação usa um snapshot consolidado retrospectivo.

## Conclusões e limites

O case demonstra consolidação auditável, análise geoespacial com sensibilidade e avaliação preditiva contra baselines. {predictive_conclusion} Permanecem limitações de cobertura do acervo, classificações desconhecidas, atualização de registros, localização e validade externa. O número de registros descreve a demanda observada; a interpretação sobre segurança pública exige fontes e desenho adicionais.

## Reprodução

Execução `{summary['run_id']}`, referência Git `{summary['git_commit']}` (alterações locais presentes: {summary['git_dirty']}), versão do case `{summary['case_version']}`. Os hashes da implementação registram o código efetivamente executado, inclusive quando ainda não commitado. O manifesto registra configuração, ambiente, hashes dos insumos e artefatos. O notebook padrão executa uma demonstração sintética com semente 42; os dados reais e os mapas detalhados ficam privados. O código versionado do notebook permanece sem outputs.

Referências metodológicas: [DBSCAN e memória](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html), [features defasadas e avaliação temporal](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html).
"""
