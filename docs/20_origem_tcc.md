# Origem acadêmica: TCC de maio de 2024

> Registro histórico da origem do projeto. O fluxo atual está no [README](../README.md) e em [15_case_data_science.md](15_case_data_science.md).

## O trabalho entregue

O projeto nasceu como trabalho de conclusão do curso de Data Analytics & Big Data da FGV, com o título "Avaliação da eficácia do projeto piloto de totens implantados pela Secretaria de Segurança Pública do Estado de Rondônia". Foi desenvolvido pelo Grupo 03 (Herbert Melo, Luiz Gustavo Almas, Nathalia Ribeiro, Rafael Favoretto, Virgílio Guerreiro e eu, Yan Samuray) e entregue em maio de 2024 com notebooks, slides e defesa oral. Minha responsabilidade foi a engenharia e o analytics.

O objetivo era avaliar se os totens instalados em Porto Velho em abril de 2023 reduziram as ocorrências e propor locais para novos totens. A versão final da apresentação não foi preservada.

## A versão preliminar

Uma versão preliminar recuperada registra uma fase intermediária do trabalho. Ela seguia as etapas do CRISP-DM:

- **Dados:** ocorrências extraídas pela API da Secretaria, polígonos do IBGE e totens geocodificados manualmente no Google Earth Pro e no GeoDa.
- **Recorte:** Porto Velho, 13 meses antes (março de 2022 a março de 2023) e 13 meses depois (abril de 2023 a abril de 2024) da instalação, com raio de 100 metros ao redor de cada totem.
- **Avaliação:** teste t pareado entre os períodos, com critério de aceitação de redução próxima ou superior a 10% nas áreas cobertas.
- **Expansão:** DBSCAN sobre as ocorrências fora do raio de 100 metros; os cinco maiores clusters foram indicados como locais candidatos.
- **Implantação:** proposta de arquitetura em nuvem, com visualização em Power BI.

Nessa versão, a conclusão era que os totens tiveram impacto positivo na redução das ocorrências, com p-valor muito inferior a 5%, tanto no total quanto em cinco grupos de ocorrências.

## Leitura retrospectiva

Revendo essa versão depois, identifiquei limitações no desenho que impedem sustentar essa conclusão. Entendê-las orientou o case atual:

- A série mensal já caía de forma contínua antes da instalação. A comparação entre os períodos atribui essa tendência aos totens.
- Os cinco grupos foram escolhidos por terem caído mais dentro do raio do que fora, e depois testados nos mesmos dados. Selecionar pelo resultado infla a significância.
- Contagens consecutivas são autocorrelacionadas. Isso viola a independência suposta pelo teste e reduz artificialmente o p-valor.
- Parte dos clusters candidatos ficava junto a unidades policiais e de saúde, onde o endereço do registro pode não coincidir com o local do fato. O próprio trabalho listou o tratamento desses locais como melhoria.

## O que foi preservado

O material recuperado reúne notebooks, mapas HTML e uma versão preliminar dos slides. Ele permanece privado e fora do Git, porque contém dados reais, dados pessoais, mapas detalhados e credenciais, hoje revogadas. Nenhum notebook recuperado executa de ponta a ponta: os resultados dependiam de planilhas locais e passos manuais.

Depois da entrega, tentei melhorar o projeto com ajuda do ChatGPT, ainda sem domínio de versionamento, e parte das versões originais se perdeu. As versões posteriores preservadas foram feitas com essa ajuda. Por isso a origem é registrada com o material disponível, sem reconstrução executável.

## Do TCC ao case atual

| Período | Etapa |
|---|---|
| Maio de 2024 | Entrega do TCC: notebooks, slides e defesa. |
| 2024 a 2025 | Tentativas de melhoria sem versionamento, em parte perdidas; a mais antiga preservada é um repositório de fevereiro de 2025. |
| Dezembro de 2025 | Rebuild: pipeline Bronze/Silver/Gold e o repositório atual. |
| Setembro e outubro de 2026 | Case de Data Science: minimização, reconciliação e execução reproduzível. |

O case atual responde às limitações acima. Substitui notebooks avulsos por uma execução única, testada e rastreável; a base completa, por um acervo minimizado e pseudonimizado; e linhas repetidas, por protocolos reconciliados. Descreve proximidade geométrica, sem afirmações causais, e mantém a sugestão de novos locais como [proposta separada](19_proposta_cobertura_mclp.md).

Os parâmetros de clustering mudaram entre as versões. Os notebooks recuperados do TCC usam 5 metros e mínimo de 5 ocorrências, enquanto a versão preliminar dos slides descreve agrupamentos em raios de 100 metros; não foi possível confirmar a configuração final. O notebook piloto posterior usou 50 metros, convertidos em graus, e mínimo de 500. A configuração do case, 150 metros e 50 registros, foi registrada durante o rebuild, em dezembro de 2025, como regra herdada do piloto, mas não aparece em nenhum notebook recuperado. Ela continua previamente fixada em relação ao case: foi definida antes da análise, e não ajustada aos resultados.
