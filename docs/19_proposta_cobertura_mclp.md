# Proposta: sugestão de locais por cobertura máxima

> Proposta registrada em 16/10/2026. Não iniciada; aguarda as pendências atuais. Não altera o fluxo, os parâmetros nem os resultados do baseline.

## Motivação

O DBSCAN do case (150 metros e 50 registros) é descritivo: identifica concentrações densas e admite ruído. Não foi desenhado para escolher locais. O script legado `30_sugestao_novos_pontos_dbscan.py` usava centros ponderados dos clusters como sugestões, com limites: um único centro por cluster, independentemente da extensão; centros de clusters não convexos podem cair fora deles; o limiar de densidade global favorece municípios com maior volume.

O k-means foi considerado e descartado como método principal. Ele minimiza a soma dos quadrados das distâncias de todos os registros, enquanto a cobertura é uma função degrau no raio. Aproximar um registro rural de 8 para 6 km reduz o custo em 28 km²; aproximar cem registros urbanos de 400 para 100 metros reduz 15 km². Os centros seriam atraídos por registros dispersos, podem cair em locais inviáveis e ignoram a cobertura do cadastro atual. Na descrição, o k-means atribuiria os registros dispersos a algum grupo e ocultaria a dispersão observada.

## Formulação

Maximal Covering Location Problem (MCLP): dados registros de alto risco ponderados, locais candidatos, raio R e quantidade k, escolher k locais que maximizem os registros a até R, descontando os já cobertos pelo cadastro atual.

- Solução gulosa: a cobertura é submodular, com garantia de (1 − 1/e) do ótimo. Implementável com o `BallTree` haversine já usado em `nearest_totems`, sem nova dependência. Uma solução exata por programação inteira exigiria solver e alteração do lock: decisão separada.
- Candidatos: locais viáveis, quando houver cadastro; na falta, grade métrica de 50 a 100 metros em `GRID_CRS`.
- Raios de 150, 300 e 500 metros, como na cobertura atual.
- Saída principal: curva de cobertura acumulada por k, que expõe o retorno marginal decrescente.

## Avaliação

- Seleção no primeiro semestre da janela espacial e avaliação no segundo. A cobertura no próprio período de seleção é apenas descritiva.
- Mesmo protocolo para os baselines: top-k células da grade de 1 km, centros de k-means ponderado e centros DBSCAN da configuração previamente fixada.
- Testes sintéticos: cobertura monotônica em k, ausência de ganho para registros já cobertos, desempate determinístico e ausência de candidatos.

## Limites

- Experimento novo e exploratório; não substitui a configuração DBSCAN previamente fixada nem reescreve resultados.
- Cobertura é proximidade geométrica ao cadastro disponível. Não indica funcionamento dos equipamentos, operação em tempo real ou redução de ocorrências. Os registros refletem protocolos, sujeitos a viés de registro. Com dispersão elevada, mesmo a solução ótima tende a cobrir fração pequena, e isso deve constar do resultado.
- Locais sugeridos e mapas detalhados permanecem privados; demonstrações públicas usam dados sintéticos.

Referência: Church, R.; ReVelle, C. [The maximal covering location problem](https://doi.org/10.1007/BF01942293). *Papers of the Regional Science Association*, 32, 101–118, 1974.
