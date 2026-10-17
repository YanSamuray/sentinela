# Revisão de referências — 07/10/2026

`cadastros_v2` foi construída offline a partir de `cadastros_v1` autenticada e de documentos oficiais arquivados. A versão anterior, seu registro de entidades e os pontos de totens foram preservados. A preparação não consulta a quarentena nem a API de ocorrências.

## Cadastros

Foram preservados 95 UUIDs de unidades, 82 de equipamentos e os 19 pontos de totens, estes com arquivos idênticos em bytes. Três atribuições indevidas à origem PM receberam correção para PC, sustentada pelo diretório oficial e por uma relação institucional histórica. Outras contradições permanecem não determinadas; o diretório atual não comprova funcionamento histórico.

Todos os nomes institucionais receberam revisão descritiva. Três registros continuam com tipo policial exato desconhecido e cinco com natureza não determinada. Instalações compartilhadas não receberam uma corporação exclusiva presumida. Fundos, conselhos, ensino e administração foram distinguidos das unidades operacionais.

Os sete municípios ausentes foram resolvidos por pertencimento a um único polígono municipal IBGE de 2024. A atribuição é retrospectiva e geométrica. Uma unidade fica fora de Rondônia e uma é virtual; ambas permanecem no cadastro com sua limitação espacial. Nenhuma localização física foi declarada comprovada apenas por corresponder ao polígono.

Os 54 registros de equipamentos com IDs antigos ambíguos permanecem separados. Os UUIDs identificam registros cadastrais e não certificam 54 equipamentos físicos distintos ou 54 duplicatas. Correções futuras usam UUID e evidência, preservando a identidade independentemente da posição, nome ou coordenada.

## Calendário histórico e pesquisa

Foram arquivadas duas rodadas de buscas para as 312 combinações de 52 municípios e seis anos (2019–2024). A primeira rodada restritiva não encontrou resultados; a segunda ampliou as consultas. Resultados de busca permanecem candidatos, e documentos sem relação com o calendário não são usados como regras. O pacote privado registra tentativas indisponíveis, redirecionamentos, documentos recuperados, URLs e hashes.

A transcrição conferida produziu 450 eventos com documentação oficial e público abrangido. A maioria comprova o expediente do Executivo estadual na localidade; isso não comprova a aplicabilidade geral de um feriado municipal. As versões compiladas da Casa Civil registram alterações e as transferências examinadas. Exemplos são o [calendário estadual de 2024](https://rondonia.ro.gov.br/wp-content/uploads/2023/12/SEI_0043603679_Decreto_N__28.680_.pdf) e o [calendário municipal de Porto Velho para 2023](https://www.portovelho.ro.gov.br/uploads/editor/files/DECRETO%2018_730%20-%20FERIADOS%202023.pdf).

Nenhuma combinação tem completude municipal confirmada: seis têm evidência parcial e 306 não têm cobertura comprovada para o Executivo municipal. Isso não significa que esses municípios não tenham feriados. A investigação foi concluída para a matriz proposta, com lacunas documentadas; completar sua cobertura demandaria recuperar e conferir mais leis, calendários e alterações.

As 480 entradas antigas receberam decisão individual: 313 coincidem em data com alguma observância oficial, sem validar automaticamente o rótulo ou alcance original; duas exigem correção de escopo/data por transferência; 165 continuam não comprovadas. O calendário ativo não reutiliza as entradas desconhecidas como feriados confirmados.

`holidays==0.87` continua gerando regras nacionais e estaduais para 2019–2026. Atos oficiais e regras da biblioteca ficam identificados separadamente. A interface `municipal_daily` agrega por dia civil e município, permite selecionar o público abrangido e mantém ausências desconhecidas como NA. Ponto facultativo parcial conserva a restrição na descrição; não equivale a suspensão por todo o dia.

## Reprodução e interfaces

Aquisição online de fontes selecionadas:

```powershell
python scripts/pipeline/archive_reference_sources.py --targets data/reference_sources/<pesquisa>/acquisition_targets.json --output data/reference_sources/<pesquisa>/archive
```

Construção offline a partir de um pacote revisado e arquivado:

```powershell
python scripts/pipeline/prepare_references_local.py --previous-version cadastros_v1 --version cadastros_v2 --evidence-dir data/reference_sources/review_20261007 --start-year 2019 --end-year 2026
```

A versão já existente é recusada. Para uma nova revisão, indicar explicitamente a versão anterior e um novo nome. `review.json` contém correções por UUID, regras com vigência/documento/localizador e eventuais evidências de cobertura integral. O construtor confere hashes, preservação de contagens e IDs antes de publicar a versão assinada. Os pacotes de pesquisa e cadastros reais permanecem privados.

Exportação privada da auditoria:

```powershell
python scripts/analysis/43_reference_review_report.py --version cadastros_v2
```

Tabelas legíveis e recibo ficam em `data/reference_review_reports/cadastros_v2/`. Os testes incluem revisão offline sem rede, alteração de fonte rejeitada, preservação dos recibos anteriores, fronteiras, coordenadas ausentes, unidade virtual, cobertura desconhecida, transferência, fuso e preservação dos UUIDs após reordenação.

## Apresentação e limites

A narrativa do portfólio liga engenharia, decisões analíticas e resultados. O relatório e as figuras públicos de demonstração são inteiramente sintéticos. Artefatos reais, documentos externos, UUIDs dos cadastros, mapas detalhados e auditorias permanecem fora do Git.

O modelo principal conserva seus atributos originais. A reexecução com a nova referência verifica equivalência com o baseline e bloqueia leituras do legado. O teste final continua preservado. O experimento separado de calendário continua baseado nas regras da biblioteca e somente na validação; os novos atos administrativos não alteram suas features silenciosamente.

A quarentena e as cópias recuperadas aguardam confirmação explícita. O inventário atualizado apresenta caminhos individuais, hashes e pendências. Não foram excluídos arquivos nem tratado o histórico/lixeira do OneDrive. Publicação externa permanece uma etapa posterior.

## Validação concluída

A suíte completa aprovou 284 testes, com três ignorados pelas restrições de links simbólicos do Windows. Duas verificações adicionais de correções municipais, com conflito geométrico e valor anterior ausente, também passaram, junto com a repetição dos 15 testes direcionados. Ruff, whitespace e conferência offline do lock passaram. A implementação final reproduziu novamente os 95 registros publicados, com evidência assinada em `data/metadata/reference_v2_implementation_verification.json`.

A execução sintética final `portfolio_final_20261007` concluiu o fluxo e selecionou o baseline `weekday_84`. Seu relatório e sete figuras foram exportados para a documentação com identificação de dados fictícios e hashes de procedência. A revisão visual ajustou a quantidade de marcas no eixo de longitude e a posição da legenda; resultados analíticos coincidiram com a primeira demonstração dentro de `1e-10`.

O notebook final executou as seis células de código, com sete figuras e zero erros, nos modos privado e sintético. As cópias executadas ficam em `data/notebook_executions/`; a fonte versionada permanece sem outputs. A execução usou IPython no próprio processo, com histórico desativado, sem alterar a proteção dos arquivos de conexão de kernels separados.

A execução privada `ds_refs_v2_20261007` preservou protocolos, contagens, análises espaciais, cohort e previsões de `ds_active_20261006`, com tolerância absoluta/relativa de `1e-10`. A execução bloqueou o legado e não registrou tentativas de leitura. A evidência assinada está em `data/metadata/case_references_v2_verification.json`. A configuração ativa passou a `cadastros_v2` após essa validação; voltar a `cadastros_v1` exige somente selecionar a versão anterior na configuração.

O inventário atualizado conferiu 5.010 arquivos: 4.740 têm hashes inalterados e 270 continuam apenas na nuvem. Dos inalterados, 3.236 são candidatos à revisão de exclusão por serem camadas substituídas ou cópias exatas; a candidatura não representa autorização. Os caminhos exatos estão em `data/metadata/discard_review_20261007.csv`, acompanhados do relatório e recibo assinado.
