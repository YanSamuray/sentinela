# Arquitetura atual

O código permanece no repositório e os dados privados ativos ficam em `data/`, ignorados pelo Git. O Bronze contém somente ocorrências minimizadas e autenticadas. Silver e Gold preservam a reconstrução verificada de engenharia. O case produz uma camada própria por protocolo e resultados em `data/analytics/<run_id>/`.

Referências públicas geográficas ficam em `data/reference/geospatial/`. Cadastros e calendário têm versões assinadas em `data/reference/<versao>/`, com registro persistente de entidades. Unidades e equipamentos são entidades distintas. A mesma fonte e o mesmo cadastro versionado permitem reproduzir os vínculos.

A revisão `cadastros_v2` consome a versão anterior explicitamente autenticada e um pacote de evidências em `data/reference_sources/`. Não consulta a quarentena. As correções referenciam UUID, campo, valor anterior/novo, documento, localização da evidência e justificativa. A versão é publicada por renomeação após validação; versões completas anteriores permanecem intactas.

A aquisição de documentos oficiais é uma etapa online separada. A construção das referências e o case executam offline. O calendário distingue regras da biblioteca de atos oficiais, alcance administrativo, vigência e cobertura municipal. `municipal_daily` fornece uma linha por data local/município, com indicadores anuláveis e filtro de público abrangido.

A chave cifrada pelo Windows DPAPI fica em `.secrets/`; não é versionada nem copiada em manifests. Os recibos históricos são preservados. A promoção do acervo acrescenta um recibo com mapeamento de caminhos e hashes, sem reescrever os documentos anteriores.

O fluxo atual é `40_portfolio_case.py`, com modo sintético padrão e modo privado explícito. A execução valida assinatura, inventário, contrato, filtros, parâmetros e integridade dos insumos ao final. A demonstração sintética não acessa entradas ou chaves privadas. Ter o código instalado não fornece o acervo privado.

A preparação de referências é separada da execução analítica. Bibliotecas e fontes são versionadas; a execução do case é offline. Parâmetros usados são os grupos `portfolio.spatial` e `portfolio.forecast`. A previsão principal preserva suas features originais; o experimento de calendário usa somente validação.

O orquestrador histórico `run_etl.py` é bloqueado diante do recibo de promoção. Scripts de migração e reconstrução antiga permanecem como ferramentas históricas, com destinos explícitos e recibos próprios. Não executar scripts antigos individualmente sobre o acervo ativo para sobrescrever seus arquivos autenticados.

As bases completas substituídas ficam em quarentena temporária. O fluxo ativo não a consulta. A exclusão depende de nova confirmação do usuário após receber inventário e resultados de validação. A pasta sincronizada pelo OneDrive foi mantida por decisão do usuário.
