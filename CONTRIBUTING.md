# Contribuir e verificar mudanças

Este é um case retrospectivo de Data Science. Leia o [README](README.md) e a
[metodologia](docs/15_case_data_science.md) antes de alterar código ou documentação.

## Preparar o ambiente

Execute na raiz do repositório, usando a versão de Python de `.python-version`:

```powershell
uv sync --locked
uv tool install pre-commit==4.5.1
pre-commit install --install-hooks
```

O pre-commit fica em ambiente próprio. Os hooks instalam versões explícitas de
Ruff e detect-secrets em ambientes isolados; não alteram `pyproject.toml` ou
`uv.lock`. A preparação inicial requer internet. Os checks preparados operam
localmente; o scanner usa `--no-verify` e não testa credenciais em serviços externos.
As dependências transitivas dos hooks são resolvidas na instalação; esses ambientes
não têm a mesma garantia de lock completo do ambiente analítico.

## Fluxo de trabalho

1. Inspecione `git status --short`; identifique alterações anteriores e o objetivo.
2. Defina o resultado e verificações proporcionais ao risco. Decisões metodológicas
   duradouras vão na documentação.
3. Faça alterações pequenas. Mantenha upgrades e refatorações fora de correções
   pontuais, salvo quando necessários e explicitamente explicados.
4. Execute os testes pertinentes e revise o diff. Mudanças em contratos compartilhados,
   guard rails ou preparação do ambiente exigem a suíte completa.
5. Prepare somente arquivos da tarefa, usando caminhos explícitos, e revise o índice.

```powershell
uv run --locked python -m pytest tests -q -p no:cacheprovider
pre-commit run --all-files
git diff --check
git diff --cached --stat
git diff --cached
pre-commit run
```

`pre-commit run` verifica as alterações preparadas e isola mudanças não preparadas
durante a execução. `--all-files` também verifica os arquivos rastreados na cópia de
trabalho; é útil antes de preparar o commit e na CI. O check de privacidade sempre
lê o **índice completo**. Arquivos novos só entram nessa verificação depois de
`git add`. Não use `--no-verify` do Git para contornar uma falha. O `--no-verify` do
scanner tem outro significado: desativar a verificação remota de credenciais.

## O que bloqueia a entrega

- Caminhos privados no índice, inclusive arquivos adicionados com `git add -f`.
- Formatos de dados/exportação sem revisão, links simbólicos, submódulos, binários
  não previstos ou blobs maiores que 5 MiB. PNGs são admitidos apenas na pasta da
  demonstração sintética; isso não comprova por si só a origem de uma imagem.
- Outputs, contagens de execução, anexos ou estado de widgets em notebooks. A única
  exceção é `notebooks/99_storytelling_resultados.ipynb` com a execução sintética: os
  outputs precisam do rótulo "Modo: synthetic" e não podem conter modo privado,
  stderr, erros ou caminhos locais.
- Novos indícios de segredos, sujeitos às limitações heurísticas do detect-secrets.
- Erros críticos de Python identificados pelo Ruff: `E9`, `F63`, `F7`, `F82`.
- Falha nos testes da CI ou alteração de arquivos rastreados durante sua execução.

O scanner de segredos usa seus filtros padrão, incluindo exclusões de arquivos lock
e formatos binários. Ele não é uma ferramenta de DLP, não reconhece todo dado pessoal
e não audita o histórico Git. A revisão de conteúdo e da proveniência de figuras
permanece necessária.

## Exceções do scanner

`.secrets.baseline` contém **25 achados revisados**, todos marcados como falsos
positivos: 18 hashes de commit e de artefatos no JSON de procedência da demonstração
sintética e as 7 figuras PNG embutidas em base64 na execução sintética do notebook. As exceções são vinculadas a arquivo, tipo e hash do valor; não liberam
diretórios inteiros nem novos valores. Nenhum segredo real é aceito no baseline.

Não regenere o baseline para silenciar falhas. Examine cada achado localmente,
registre a justificativa e revise a diferença. Não imprima os valores candidatos nos logs.
Reexecutar o notebook muda as linhas das figuras; no Windows, o hook também reescreve os
caminhos com barra invertida. Aproveite apenas os novos números de linha e mantenha os
caminhos com barra normal.

## Lint

O gate usa configuração explícita em `config/ruff-guardrails.toml`, para produzir o
mesmo conjunto de regras na máquina e na CI. Não usa `--fix` nem reformata arquivos.
O lint amplo tem dívida conhecida, não corrigida em massa; ampliar as regras requer
uma tarefa de limpeza separada.

## Ciência de dados

O teste de integração executa o case sintético completo e verifica sete figuras,
relatório, integridade e rejeição de sobrescrita. A CI roda no Linux e no Windows,
sem base real, chave protegida ou API de ocorrências. Os testes de DPAPI são
específicos do Windows; outros skips devem ser explicados.

Mudanças estruturais devem preservar protocolos, contagens e resultados nas
tolerâncias documentadas (absoluta e relativa de `1e-10`). Experimentos novos não
sobrescrevem o baseline nem reutilizam o teste final para seleção.

A CI tem permissão somente de leitura, actions fixadas por SHA e não publica artefatos.

## Ambiente validado

Os resultados foram produzidos com Python 3.12.7 e o lock deste repositório. Nesse
ambiente, a demonstração sintética reproduz a versão publicada em
`docs/assets/synthetic_demo/` dentro de `1e-10`.

## Referências

- [pre-commit](https://pre-commit.com/)
- [detect-secrets](https://github.com/Yelp/detect-secrets)
- [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final)
