> A Gold minimizada preservada é um snapshot de engenharia. O alvo atual de alto risco vem do consenso de protocolos e rótulos revisados, descrito em `15_case_data_science.md`. Indicadores históricos de feriado não incorporam a nova referência versionada.

# Dicionário de dados: Gold e camada do case de Data Science

## Gold minimizada

| Campo | Tipo | Significado e limite |
|---|---|---|
| `protocolo` | String | Pseudônimo HMAC estável. Há repetições entre arquivos; não é uma chave globalmente única da Gold. |
| `dataCriacao` | Datetime | Instante do registro; o case faz agregação diária em `America/Porto_Velho`. |
| `nivelRisco` | String | Classificação da fonte. O case usa apenas rótulos exatos revisados. |
| `nivel_risco_num` | Inteiro anulável | Conversão histórica da Gold; o case calcula seu próprio mapeamento revisado a partir do Bronze autenticado. |
| `flag_crime_violento` | Boolean anulável | Regra derivada que combina nível de risco e sinais de violência. Não confirma ocorrência de crime. |
| `flag_crime_patrimonial` | Boolean anulável | Regra baseada em categorias revisadas; categoria desconhecida pode produzir nulo. |
| `flag_ocorrencia_alto_risco` | Boolean anulável | Indicador composto de violência/intervenção do pipeline. Não define o alvo de previsão deste case. |
| `localizacao_latitude`, `localizacao_longitude` | Float anulável | Coordenadas precisas privadas. Validade numérica não comprova localização em Rondônia. |
| `id_totem_prox` | String anulável | Pseudônimo do ponto mais próximo no cadastro minimizado. |
| `distancia_totem_m` | Float anulável | Distância Haversine em metros. Permanece nula sem coordenadas válidas. |
| `flag_dentro_raio` | Boolean anulável | Proximidade no raio configurado, 300 m na reconstrução. Distância desconhecida permanece nula. |

`geo_valida_uf` e `municipio_ibge` constavam da documentação anterior, mas não são saídas do enriquecimento Gold atual. A verificação por polígono e a agregação municipal pertencem à nova camada analítica descrita abaixo.

## Cohort do case

`cohort.parquet` contém uma linha por protocolo e é produzido em uma execução própria, preservando a Gold.

| Campo | Significado |
|---|---|
| `source_rows`, `source_files`, `record_variants` | Quantidade de linhas, arquivos e hashes canônicos distintos do registro minimizado. |
| `date_variants`, `risk_variants` | Quantidade de valores distintos usados no consenso; ausência participa da comparação. |
| `timestamp_utc`, `local_date` | Instante e dia local concordantes; conflitos não recebem um instante arbitrário. |
| `risk_level` | Nível de risco da fonte mapeado a partir de rótulos revisados; desconhecido ou conflitante fica nulo. |
| `temporal_eligible`, `high_risk` | Elegibilidade e pertencimento ao alvo de nível alto ou superior. |
| `classification` | Partição exclusiva: data inválida/conflitante, risco conflitante, risco desconhecido, alto conhecido ou demais níveis. |
| `coordinates_agree`, `coordinates_valid` | Consenso e validade numérica do par de coordenadas. |
| `inside_ro`, `spatial_eligible` | Predicado geográfico e seleção de alto risco com coordenadas válidas dentro do polígono. O denominador de registros fora do estado exige `coordinates_valid=True`. |
| `distance_totem_m` | Distância ao cadastro para o conjunto espacial elegível; outros protocolos permanecem sem distância. |

Municípios são derivados pelos polígonos públicos e apresentados em `municipality_counts.csv`. Correspondências ambíguas de fronteira não recebem um município arbitrário.

## Calendário e previsão

| Campo | Significado |
|---|---|
| `known_high`, `known_other` | Contagens diárias por classificação concordante e revisada. |
| `risk_unknown`, `risk_conflicting` | Contagens separadas das classificações sem decisão válida. |
| `coverage_declared`, `coverage_status` | Presença em janela declarada; não comprova completude de extração. |
| `y` | Contagem observada de alto risco, com zero observado nas janelas e ausência nas lacunas. |
| `unknown_risk_upper_bound` | Soma de alto risco conhecido e registros com risco desconhecido/conflitante; é um limite descritivo de classificação. |
| `complete_month` | Mês integralmente dentro das janelas declaradas, usado para definir o corte histórico. |
| `origin`, `horizon`, `predicted`, `observed` | Origem de avaliação, passo de 1–30 dias, previsão e contagem observada. |
| `split`, `fold`, `model` | Validação ou teste, bloco temporal e candidato avaliado. |

Consulte o [contrato do case](15_case_data_science.md) para métodos, denominadores e reprodução.

## Referências atuais

Unidades: `unidade_id`, `id_legado`, `origin`, `corporacao`, `nome_unidade`, `tipo_unidade`, `codigo_municipio_ibge`, coordenadas, `coordinates_valid`, `identity_status` e hash da fonte. Equipamentos: `equipamento_id`, atributos institucionais, tipo, `is_totem`, localização e vínculo `unidade_id` anulável. Identificadores não são features numéricas do modelo.

Calendário: `data` civil, `descricao`, `tipo` (NACIONAL/ESTADUAL/MUNICIPAL/FACULTATIVO), município/código IBGE, fonte, versão, `verified`, `status` e vigência. `verified` deve ser interpretado com `verification_basis`: regras da biblioteca e atos oficiais conferidos têm fundamentos distintos. `verified=False` sinaliza uma referência não comprovada. A cobertura municipal é incompleta.

Na versão revisada, `event_kind` diferencia FERIADO, FACULTATIVO e SUSPENSAO_EXPEDIENTE; `audience` restringe o alcance, como EXECUTIVO_ESTADUAL ou EXECUTIVO_MUNICIPAL. `publication_date`, `source_id`, `source_version`, `rule_id` e `locator` identificam o documento arquivado e sua evidência. `original_date` registra a data anterior quando há transferência comprovada. Datas facultativas parcialmente observadas trazem a restrição na descrição e não representam encerramento por 24 horas.

Unidades preservam `corporacao_origem`, `nome_original` e `tipo_original`. `corporation_status` distingue legado, contradição pendente, instalação compartilhada e vínculo documental. `natureza`, `classification_method`, `classification_rule`, `classification_source` e `classification_status` registram a classificação descritiva. O rótulo UNIDADE_POLICIAL_TIPO_NAO_DETERMINADO mantém o tipo exato desconhecido.

`codigo_municipio_original` preserva a atribuição anterior; `codigo_municipio_geometrico`, `municipality_method` e `municipality_conflict` registram a conferência por polígono. `coordinates_valid` verifica somente números e limites globais; `location_status` distingue fronteira ambígua, fora de RO, divergência e localização virtual não comprovada. `physical_location_verified=False` impede confundir pertencimento ao polígono com endereço institucional confirmado. `spatial_eligible` expressa a elegibilidade descritiva do cadastro, sem determinar o funcionamento da unidade.

Equipamentos mantêm `physical_identity_status` e `legacy_group_size`. O UUID identifica o registro cadastral; o mesmo ID legado em vários registros não prova duplicidade física.

`municipal_coverage.parquet` tem uma linha por município/ano, fontes e buscas arquivadas, com cobertura confirmed, partial ou not_proven para o público declarado. Na versão atual não existe confirmed. `municipal_daily.parquet` agrega por data/município antes de qualquer join: True indica evento documentado; False exige cobertura confirmada; NA mantém a ausência desconhecida. Consumir o calendário com join many-to-one e pelo dia civil de Porto Velho.
