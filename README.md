# Totens de segurança em Rondônia — case de Data Science

**English summary.** A retrospective data science case on public-safety records classified as high risk in Rondônia, Brazil. It reconciles 1.2 million records into one unit per protocol, measures geometric proximity to 19 security totems, explores density clusters and evaluates 30-day daily forecasts against simple baselines; the weekday baseline won on the held-out test. Real data stay private, and a fully synthetic demo reproduces the method. The project began as a 2024 group capstone at FGV; this rebuild is my engineering and analytics work.

Case retrospectivo sobre concentração de registros classificados como alto risco, proximidade dos totens e previsão do volume diário para 30 dias. Reúne engenharia de dados, qualidade, análise geoespacial e avaliação temporal.

Minha contribuição abrange a estrutura de engenharia e analytics: minimização do acervo, consolidação auditável, referências institucionais, análise espacial, avaliação preditiva e comunicação dos resultados. O projeto nasceu como TCC em grupo na FGV, em 2024; a [origem acadêmica](docs/20_origem_tcc.md) registra o que mudou desde então.

Leia a [narrativa do portfólio](docs/17_portfolio_case.md) ou explore o [relatório de demonstração sintética](docs/assets/synthetic_demo/report.md), com gráficos e resultados fictícios reproduzíveis.

A unidade analítica é o protocolo pseudonimizado. Datas e classificação precisam concordar entre versões. Alto risco usa a classificação revisada da fonte, nível alto ou superior. Localização inválida exclui somente a análise espacial quando data e risco permitem uso temporal.

## Executar a demonstração

Use a versão de Python definida em `.python-version` e as dependências fixadas pelo `uv.lock`:

```powershell
uv sync --locked
python scripts/analysis/40_portfolio_case.py --mode synthetic
```

O modo padrão gera dados, equipamentos e geografia sintéticos, com semente fixa. Não exige dados reais, API ou chave privada. Produz relatório Markdown, sete figuras, tabelas e metadados da execução em `data/portfolio_synthetic/`.

O [notebook de apresentação](notebooks/99_storytelling_resultados.ipynb) usa esse mesmo fluxo e já aparece executado com os dados sintéticos da demonstração.

## Estudo privado

Os resultados reais vêm do mesmo código, executado sobre um acervo privado que não faz parte deste repositório:

```powershell
python scripts/analysis/40_portfolio_case.py --mode private --config config/settings.minimized.toml
```

O acervo ativo contém Bronze minimizado, Silver/Gold verificados e referências versionadas. As ocorrências não contêm os documentos, nomes, contatos ou relatos removidos na minimização. Coordenadas e horários precisos continuam privados; pseudonimização não é anonimização.

O runner verifica o contrato e os insumos autenticados, registra hashes, versões e parâmetros efetivos e não sobrescreve execuções. Unidades de PC, PM e CB compartilham um cadastro; equipamentos têm cadastro separado. Cadastros e calendário são referências versionadas, com procedência e limitações explícitas.

A versão revisada `cadastros_v2` preserva os UUIDs e os pontos de totens da análise. Origem do arquivo e vínculo institucional são campos distintos; classificação descritiva, corporação não comprovada e identidade física pendente ficam explícitas. Sete municípios ausentes receberam atribuição geométrica retrospectiva; isso não comprova a localização física das unidades.

A pesquisa histórica abrange 52 municípios e seis anos (2019–2024). As 312 combinações foram investigadas e as 480 entradas municipais antigas receberam uma decisão. Há evidência municipal parcial em seis combinações; nenhuma tem cobertura integral comprovada. Os atos estaduais permanecem restritos ao expediente do Executivo estadual. A referência diária usa valores desconhecidos para ausências sem cobertura e não integra o modelo estadual principal.

## Resultados do estudo privado

O acervo contém 1.248.082 linhas e 1.215.776 protocolos distintos. As 32.306 repetições excedentes incluem 31.956 registros minimizados idênticos e 350 com diferenças. Há 100.607 protocolos classificados como alto risco; 100.509 são elegíveis espacialmente.

Na janela de novembro de 2023 a outubro de 2024, os 16.073 registros de alto risco geraram quatro clusters com 150 m e mínimo de 50, com cerca de 98% de ruído. Nenhuma célula de 1 km com cluster se repete entre os dois semestres da janela (Jaccard 0), resultado também influenciado pelo limiar fixo aplicado a cada metade. A grade de 1 km e a sensibilidade ajudam a investigar o resultado; clusters não constituem evidência de áreas persistentes ou efeito de equipamentos.

O HGB Poisson selecionado pela validação teve MAE de teste de aproximadamente 8,69 registros/dia; a média por dia da semana teve aproximadamente 8,39. O modelo escolhido não superou esse baseline no teste. A projeção é um exercício histórico para novembro de 2024.

O experimento adicional de feriados é separado, usa somente validação e preserva o teste já examinado. Feriados municipais e pontos facultativos sem verificação não entram nas features.

## Método e limitações

Proximidade dos equipamentos representa cobertura geométrica do cadastro disponível. Não há avaliação causal, estimativa de crimes evitados ou comprovação de funcionamento por equipamento. A disponibilidade histórica das classificações nas origens de previsão não foi comprovada.

Lacunas identificadas ficam ausentes; dias sem registros no acervo são zeros observados. A avaliação usa seis blocos de validação e três de teste, com horizonte de 30 dias, treinamento crescente, features calculadas com o passado e previsão recursiva. Um baseline vencedor é um resultado válido.

## Documentação e verificações

- [Contribuição, guard rails e validação](CONTRIBUTING.md)
- [Metodologia do case](docs/15_case_data_science.md)
- [Revisão das referências e calendário histórico](docs/18_revisao_referencias.md)
- [Arquitetura atual](docs/02_arquitetura.md)
- [Dicionário e referências](docs/04_dicionario_dados.md)
- [Proposta não iniciada: sugestão de locais por cobertura máxima](docs/19_proposta_cobertura_mclp.md)
- [Origem acadêmica: TCC de maio de 2024](docs/20_origem_tcc.md)

```powershell
python -m pytest tests -q -p no:cacheprovider
```

Este repositório é o recorte público de um projeto privado maior. Ele contém o case, a engenharia de privacidade e de referências e a demonstração sintética. O ETL histórico, as ferramentas de migração local e os dados reais permanecem privados.

Licença: [MIT](LICENSE).
