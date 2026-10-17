# Qualidade, território e previsão da demanda registrada

Este case de Data Science investiga três perguntas: onde se concentram os registros classificados como alto risco, qual sua proximidade dos totens cadastrados e com que precisão prever o volume diário para um horizonte de 30 dias.

Minha responsabilidade abrange a estrutura de engenharia e analytics. O trabalho conecta preparação segura do acervo, contratos de dados, análise geoespacial, avaliação temporal e comunicação dos resultados. O estudo usa a classificação de risco da fonte; não trata todo registro como crime confirmado.

## Do acervo à unidade analítica

O acervo privado contém 1.248.082 linhas e 1.215.776 protocolos distintos. A primeira decisão foi reconciliar as versões antes de modelar. Das 32.306 repetições excedentes, 31.956 são idênticas após minimização e 350 apresentam diferenças. Datas e riscos precisam concordar; a ordem dos arquivos não decide qual versão é válida.

Documentos pessoais, nomes, contatos e relatos foram removidos da base ativa. Protocolos recebem pseudônimos protegidos. Horários e coordenadas precisos continuam privados. As camadas de origem verificadas permanecem imutáveis, e o case produz uma unidade por protocolo em uma camada própria.

A engenharia de referências também faz parte da qualidade: IDs manuais foram preservados como legado, UUIDs persistentes identificam registros e evidências sustentam correções institucionais. O calendário municipal mantém incerteza, vigência e alcance de cada ato, em vez de preencher ausências como dias comuns.

## Concentração e proximidade

Na janela de novembro de 2023 a outubro de 2024 há 16.073 protocolos de alto risco elegíveis espacialmente. A configuração previamente definida de DBSCAN — 150 metros e 50 registros — produz quatro clusters e aproximadamente 98% de ruído. A grade de 1 km, a sensibilidade a nove configurações e a comparação semestral ajudam a interpretar esse resultado.

O percentual elevado de ruído mostra o limite dessa configuração para o acervo. Não significa que os registros fora de clusters sejam irrelevantes. Os clusters são pontos de partida para investigação, e sua estabilidade depende da janela, do volume e dos parâmetros.

A proximidade em 150, 300 e 500 metros representa cobertura geométrica do cadastro disponível. Os dados não comprovam datas de funcionamento, alcance efetivo ou redução de ocorrências causada pelos equipamentos.

## Previsão com avaliação temporal

A previsão compara repetição semanal, média por dia da semana dos últimos 84 dias e quatro configurações de boosting Poisson. Lags e médias móveis usam somente o passado; o horizonte de 30 dias é recursivo. Seis blocos de validação selecionam o candidato antes de três blocos finais de teste.

O candidato selecionado pela validação teve MAE de aproximadamente 8,69 registros/dia no teste. A média por dia da semana teve aproximadamente 8,39. O modelo mais complexo não demonstrou ganho consistente sobre o baseline nessa avaliação. Esse resultado orienta a decisão sobre complexidade e evita escolher novamente o modelo pelo teste já examinado.

Um experimento adicional com regras nacionais e estaduais de calendário encontrou melhora de cerca de 1,55% no MAE de validação para uma configuração do boosting. Trata-se de uma comparação separada, sem reavaliar o teste ou substituir o resultado original. Regras da biblioteca e expediente administrativo observado em decretos têm alcances distintos.

## O que o case demonstra

- Engenharia com minimização antes da persistência, contratos autenticados e rastreabilidade por execução.
- Reconciliação de versões, denominadores explícitos e tratamento de desconhecidos.
- Análise geoespacial com pesos, controle de memória e avaliação de sensibilidade.
- Previsão avaliada contra referências simples, com proteção contra vazamento temporal.
- Comunicação de resultados negativos, incertezas e limites de aplicação.

A avaliação é retrospectiva: a disponibilidade dos rótulos nas origens históricas não foi comprovada. A projeção para novembro de 2024 é um exercício histórico. Uma aplicação operacional exigiria dados atualizados e avaliação da disponibilidade dos atributos no momento da previsão.

Para reproduzir o método sem acesso ao acervo privado, use a [demonstração sintética](assets/synthetic_demo/report.md) e o notebook principal. As figuras dessa demonstração usam registros, equipamentos e geografia fictícios; seus valores não representam Rondônia. O [método completo](15_case_data_science.md) descreve critérios, modelos e testes.
