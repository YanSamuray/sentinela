# Case de Data Science: alto risco, cobertura territorial e previsão

O case investiga concentração espacial dos registros classificados como alto risco, sua proximidade dos totens e a previsão de volume diário para 30 dias. A apresentação combina um notebook reproduzível e um relatório Markdown com figuras. O acesso à API não participa desse fluxo.

## Execução

Com a `.venv` ativada, a demonstração padrão usa somente dados sintéticos, incluindo geografia e equipamentos, com semente 42:

```powershell
python scripts/analysis/40_portfolio_case.py --mode synthetic
```

O estudo privado usa o Bronze minimizado autenticado, o cadastro minimizado autenticado de totens e os polígonos públicos do IBGE:

```powershell
python scripts/analysis/40_portfolio_case.py --mode private --config config/settings.minimized.toml
```

`--run-id` permite identificar uma execução explicitamente. Um diretório existente é recusado. Os resultados sintéticos ficam em `data/portfolio_synthetic/<run_id>/`; os resultados reais ficam em `data/analytics/<run_id>/`. Ambos estão ignorados pelo Git. As camadas Bronze/Silver/Gold existentes são somente leitura.

O notebook `notebooks/99_storytelling_resultados.ipynb` executa o mesmo fluxo sintético por padrão. Para consumir um estudo privado já concluído, forneça seu diretório na variável `TOTENS_PORTFOLIO_RUN`. Isso exige a chave DPAPI original. O leitor verifica estado, assinatura privada e hashes dos artefatos antes de exibir resultados. A versão rastreada do notebook permanece sem outputs; cópias executadas e resultados detalhados são privados.

## Unidade, alvo e elegibilidade

- Uma unidade por protocolo pseudonimizado. A comparação de repetição usa o hash canônico do registro **minimizado**; igualdade aqui não prova igualdade dos campos pessoais removidos na migração.
- Datas e níveis de risco precisam concordar entre versões. Não existe seleção pela ordem ou pelo nome do arquivo. Conflitos são contabilizados e excluídos da análise temporal principal.
- Alto risco é o nível da fonte alto ou superior, mapeado somente a partir de rótulos exatos revisados. O indicador composto `flag_ocorrencia_alto_risco` não define o alvo deste case. Rótulos não revisados ou sem mapeamento ficam desconhecidos.
- A data é `dataCriacao`, convertida para `America/Porto_Velho`. Datas sem fuso não recebem um fuso inventado e ficam fora da seleção temporal.
- Coordenadas precisam concordar entre versões, ser numéricas e estar dentro do polígono de Rondônia, incluindo a fronteira. Falhas de localização excluem somente a análise espacial quando data e risco ainda forem elegíveis.
- Município é derivado pelo polígono público. Pontos em mais de um município permanecem ambíguos e não recebem um município arbitrário.

O calendário distingue risco alto observado, demais níveis, classificação desconhecida/conflitante e cobertura declarada. Um zero representa zero observado no acervo. Os nomes das janelas não comprovam completude de extração. Lacunas de janela permanecem ausentes. Protocolos sem data válida entram na exclusão global, sem atribuição artificial a um dia. Meses parciais são relatados e não compõem o último mês completo de modelagem. Mudanças mensais acima de duas vezes ou abaixo de metade do mês anterior são sinalizadas somente entre meses adjacentes completos, sem exclusão automática.

## Análise espacial

A janela usa os últimos 12 meses completos declarados, com comparação dos dois semestres. Uma grade de 1 km em projeção Lambert azimutal equivalente centrada em Rondônia fornece a referência descritiva. Os raios de 150, 300 e 500 metros descrevem proximidade geométrica ao cadastro disponível, com denominadores separados para todo o acervo espacial e para a janela de 12 meses; operação e efetividade dos equipamentos não são inferidas.

DBSCAN usa Haversine, configuração previamente fixada de 150 metros e mínimo de 50 registros, e nove combinações de sensibilidade: distâncias de 100/150/300 metros e mínimos de 25/50/100 registros. Coordenadas exatamente coincidentes recebem pesos, preservando densidade e volume. O grafo de vizinhança é esparso, construído em blocos, com limite explícito de recursos; exceder o limite interrompe o estudo, sem amostrar silenciosamente ou produzir um resultado parcial como completo. O processamento usa uma thread para compatibilidade com o ambiente Windows.

A comparação entre semestres mede Jaccard das células de 1 km ocupadas por pontos agrupados. Não compara os números dos rótulos de clusters. O limiar fixo de registros é aplicado a cada semestre, cujos volumes podem diferir; portanto, baixa sobreposição também pode refletir esse limiar. Ausência de clusters é um resultado válido. As tabelas registram volume, ruído, sensibilidade e centros ponderados. Mapas detalhados e centros permanecem privados.

Referência: [documentação do DBSCAN](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html).

## Previsão retrospectiva

O alvo é a contagem diária de protocolos elegíveis classificados como alto risco. A série é o segmento contínuo mais recente de janelas declaradas, terminado no último mês completo. A classificação consolidada pode conter atualizações posteriores ao registro; a disponibilidade dos rótulos nas origens históricas não foi comprovada.

São seis candidatos:

1. Repetição da última semana.
2. Média por dia da semana nos últimos 84 dias.
3. Quatro configurações de `HistGradientBoostingRegressor`, perda Poisson, 200 iterações, semente 42, validação aleatória interna desativada: taxas de aprendizado 0,05/0,1 e 15/31 folhas.

Features: lags de 1/7/14/28 dias, médias móveis de 7/28 dias deslocadas em um dia e calendário conhecido (dia da semana, mês e dia do ano). Cada horizonte de 30 dias é recursivo: previsões anteriores entram nos lags dos dias seguintes. O modelo não recebe observações futuras desse horizonte.

Os últimos 90 dias constituem três blocos de teste de 30 dias. Antes deles, seis blocos de 30 dias avaliam treinamento crescente, com pelo menos 365 dias utilizáveis depois da criação das features. O menor MAE médio da validação seleciona o modelo, com preferência pela alternativa mais simples em empate. A escolha é congelada antes do teste; comparar resultados de teste não reabre a seleção. Falta de histórico, lacunas ou dados inválidos interrompem a avaliação com motivo explícito.

O relatório apresenta MAE, RMSE médio dos blocos, MASE sazonal médio dos blocos e erro por horizonte. MASE usa a variação absoluta de sete dias do treinamento e fica indefinido quando essa escala é zero. Um baseline vencedor permanece vencedor; melhorar seu resultado não é requisito para comunicar o estudo. Não são reutilizadas as antigas bandas `média ± 1,96 × desvio-padrão` como intervalos de previsão.

A projeção após o corte histórico é um exercício retrospectivo. Ela não representa demanda na data atual nem demonstra prontidão operacional.

Referência: [features defasadas e avaliação temporal do scikit-learn](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html).

## Artefatos e validação

Cada execução produz `meta.json`, `summary.json`, `report.md`, sete figuras, cohort Parquet e tabelas CSV de calendário, municípios, grade, clusters, sensibilidade, semestres, previsões, métricas e erro por horizonte. Os ledgers armazenam somente projeções minimizadas; IDs da Gold histórica são pseudonimizados em memória antes de qualquer gravação. Rótulos de feriados antigos entram na reconciliação apenas como hashes. São comparadas multiplicidades por protocolo e instante UTC; uma associação com feriados diferentes não prova a causa histórica da diferença.

O manifesto registra ambiente, configuração, referência e estado do Git, hashes do código efetivamente executado, hashes dos insumos e hashes dos artefatos. Isso documenta também execuções com alterações locais ainda não commitadas. Insumos e implementação são conferidos novamente antes do estado completo. Uma falha preserva a execução parcial com estado `failed`.

Os testes cobrem consenso de versões, risco não revisado, fuso, lacunas, fronteira geográfica, pesos coincidentes, ausência de clusters, seleção independente do teste, MASE indefinido e proteção dos diretórios. Alterações deliberadas do futuro devem manter as features e previsões de uma origem anterior iguais. Uma integração completa verifica que o modo sintético não acessa chaves nem insumos privados e que artefatos alterados são recusados.

```powershell
python -m pytest tests -q -p no:cacheprovider
```

A divulgação dos resultados reais exige revisão das evidências agregadas e do histórico do Git. O modo sintético oferece reprodução pública sem acesso à base operacional.

## Verificação histórica da implementação inicial

A execução privada `ds_private_20261005_v2` concluiu todas as etapas e conferiu novamente os hashes de 508 insumos e sete módulos de implementação, sem mudanças. A revisão adicional conciliou a partição de qualidade, os denominadores espaciais, os pesos da grade e os registros em ruído, além das origens de previsão.

O notebook foi executado em cópias privadas nos modos sintético e privado: seis células de código, sete figuras incorporadas e zero erros em cada modo. A versão rastreada permaneceu sem outputs. A suíte completa aprovou 259 testes, com três ignorados por limitações de links simbólicos no Windows; Ruff e a revisão de whitespace também passaram.

O relatório registra a escolha congelada pela validação. No teste, o candidato selecionado teve MAE de 8,691 registros/dia, frente a 8,385 do baseline de média por dia da semana. O resultado não demonstra ganho consistente da alternativa mais complexa. A janela espacial também apresenta forte sensibilidade ao limiar de registros e aos raios; a comparação semestral considera essa limitação.

## Acervo ativo e referências versionadas

Após a promoção, `data/` contém exclusivamente as camadas minimizadas ativas. O runner autentica o contrato e os arquivos, em vez de confiar no nome do diretório. Os recibos antigos permanecem intactos; `metadata/promotion_receipt.json` registra a troca. A evidência histórica da Gold é assinada e vinculada ao hash do resumo da migração; o case não consulta a quarentena para recalculá-la.

Os parâmetros consumidos estão em `portfolio.spatial` e `portfolio.forecast`, na configuração selecionada. Parâmetros desconhecidos ou inválidos interrompem a execução. Valores efetivos integram o relatório de execução. Os defaults preservam o protocolo metodológico descrito acima.

O cadastro de unidades reúne PC, PM e CB. Equipamentos têm cadastro separado; identidades ambíguas permanecem sinalizadas. UUIDs são mantidos em registro versionado, não recalculados por posição ou localização. Unidades sem coordenadas continuam no cadastro.

`cadastros_v2` preserva UUIDs e os pontos de totens, separa origem do arquivo de vínculo institucional, mantém classificações e identidades físicas não comprovadas e registra atribuições geométricas retrospectivas dos municípios. A classificação das unidades não constitui feature da previsão.

`holidays==0.87` gera regras nacionais e estaduais. Essas regras não representam automaticamente o expediente de cada administração. O calendário revisado acrescenta atos oficiais com vigência e público abrangido, distingue feriados de facultativos/suspensões e registra cobertura municipal em 312 combinações município–ano. Nenhuma tem completude municipal comprovada. As 480 entradas municipais legadas receberam uma decisão individual; referências não comprovadas permanecem na auditoria e não produzem indicadores verdadeiros. Veja a [revisão das referências](18_revisao_referencias.md).

## Experimento adicional de calendário

`41_calendar_experiment.py --run <execucao_privada>` compara os mesmos modelos com indicadores nacionais/estaduais, véspera e dia seguinte, exclusivamente nos blocos originais de validação. Não usa feriados municipais ou pontos facultativos. A comparação é exploratória: o teste final já foi examinado e permanece preservado. Não há promoção automática de um novo vencedor para o case original.

Esses indicadores usam exclusivamente `source=holidays:BR:RO`. Eventos administrativos documentados na nova referência não entram silenciosamente no experimento. Qualquer agregação de calendário municipal para previsão estadual exigirá desenho e avaliação separados.
