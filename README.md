# Hidrelétricas no Brasil — UHE, PCH e CGH

Manual interativo de geração hidrelétrica brasileira, com banco de dados consultável
de todas as usinas cadastradas na ANEEL.

Material educacional. Não constitui recomendação de investimento.

## O que tem aqui

**Manual** — 20 seções interativas, da física da turbina ao valuation do ativo:
anatomia clicável, curva de permanência, matriz de segurança de barragens, linha do
tempo regulatória filtrável, simuladores de garantia física, GSF, decomposição de
preço e retorno de brownfield, glossário e quiz.

**Ferramenta de research** — banco de todas as usinas hidrelétricas do país:

- **Abre mostrando tudo** — UHE, PCH e CGH, todas as fases, o Brasil inteiro. Os
  filtros só restringem o que você pedir; nada vem pré-filtrado.
- **Resumo em português** sempre visível no topo: "Mostrando 3.842 de 4.010 usinas —
  PCH, em operação, no Sul. Somam 2,1 GW", atualizado a cada filtro.
- **Atalhos grandes** — Tudo / UHE / PCH / CGH — e um interruptor "só em operação"
  para quem não quer mexer em filtro nenhum. Os filtros detalhados (território,
  potência, sub-bacia, datas, sinalizadores) ficam recolhidos, sob demanda.
- **Teses prontas** com um clique: alvos de consolidação, outorga curta, candidatas a
  repotenciação, risco de barragem, faixa incentivada de 30–50 MW
- **Sinalizadores automáticos** por usina, calculados sobre campos publicados:
  outorga curta ou vencida, garantia física ausente, potência fiscalizada abaixo da
  outorgada, mais de 40 anos de operação, proprietário de ativo único, barragem classe A
- **Mapa de usinas no território** por coordenada real do SIGA, dimensionado por
  potência, com alternância para agregado coroplético por estado
- **Muro de vencimentos** — MW por janela de expiração da outorga
- **Ficha da usina** — cadastro completo, linha da outorga, composição societária,
  usinas irmãs do mesmo proprietário e vizinhas de sub-bacia
- **Comparar lado a lado** — marque até 6 usinas com a ★ e veja uma tabela transposta
  com os 16 indicadores que mais importam, para decidir entre alternativas antes de
  comprometer tempo de due diligence numa só
- **Carteira em estudo** com índice Herfindahl de concentração por sub-bacia e por
  submercado — mede diversificação real, não número de ativos
- **Painel de qualidade dos dados** — cobertura de cada campo no recorte filtrado
- **Tudo é lembrado no seu navegador** — filtro ativo, carteira montada e progresso do
  checklist de due diligence continuam de onde pararam na próxima visita
- Link compartilhável do filtro, exportação em CSV do resultado e da carteira
- **Busca rápida na navegação** — tecle `/` em qualquer lugar do site e digite o que
  procura ("gsf", "barragem", "outorga") para pular direto à seção

## Por que este repositório existe

A API de dados abertos da ANEEL nem sempre responde a chamadas feitas direto do
navegador, por política de origem cruzada. A solução é buscar os dados no servidor:
o workflow `atualizar-dados.yml` roda diariamente, baixa a base SIGA, cruza com o
conjunto FSB de segurança de barragens e grava `data/usinas.json` no próprio repositório. O site carrega esse arquivo da mesma
origem, sem CORS, e fica sempre atualizado sem intervenção.

## Como publicar

1. Crie um repositório e envie estes arquivos.
2. Em **Settings › Pages**, defina *Source: GitHub Actions*.
3. Em **Settings › Actions › General**, em *Workflow permissions*, marque
   **Read and write permissions** e salve. Sem isso o robô não consegue gravar os dados.
4. Em **Actions**, rode `Atualizar dados e publicar` uma vez, manualmente.
5. O site sobe em `https://<usuario>.github.io/<repositorio>/`.

Para rodar localmente sem publicar:

```bash
python3 scripts/atualizar_dados.py     # gera data/usinas.json
python3 -m http.server 8000            # abra http://localhost:8000
```

Abrir o `index.html` com duplo clique **não** carrega a base automaticamente:
navegadores bloqueiam qualquer requisição a partir de `file://`. Nesse caso o site
mostra o diagnóstico do que tentou e você pode arrastar o CSV do SIGA para a caixa de
carga — funciona igual.

## Se o banco não carregar

O painel de carga mostra exatamente o que foi tentado e por quê falhou. Os três casos:

| Sintoma | Causa | Solução |
|---|---|---|
| `bloqueado pelo navegador` em tudo, origem `file://` | abriu por duplo clique | use o endereço do Pages, ou `python3 -m http.server` |
| `arquivo não existe (HTTP 404)` no snapshot | o workflow ainda não rodou ou falhou | confira *Read and write permissions* e rode `Atualizar dados e publicar` |
| snapshot 404 mas a página está no Pages | Pages publicou antes do commit dos dados | rode o workflow de novo; ele publica depois de gravar |

Em qualquer um deles, arrastar o CSV do SIGA para a caixa resolve na hora.

## Estrutura

```
index.html                          site completo, arquivo único, sem dependências
data/usinas.json                    snapshot da base SIGA (gerado pelo workflow)
scripts/atualizar_dados.py          baixa e filtra a base da ANEEL
.github/workflows/build.yml         atualiza os dados e publica, num passo só
```

O workflow é único de propósito. Commits feitos com `GITHUB_TOKEN` não disparam
outros workflows — é a proteção do GitHub contra recursão. Se "atualizar dados" e
"publicar" fossem workflows separados, o Pages nunca republicaria com a base nova.

## Fontes

Todos os dados vêm de fontes públicas primárias. Nenhum valor é estimado.

| Fonte | Uso |
|---|---|
| ANEEL — SIGA | cadastro de usinas, potência, garantia física, outorga, proprietários, coordenadas |
| ANEEL — FSB | classe de barragem, categoria de risco e dano potencial, cruzados por CEG e nome |
| ANEEL — RALIE | expansão da oferta e adições por ano |
| ANEEL — FSB | fiscalização de segurança de barragens |
| ONS | geração, disponibilidade, ENA e EAR |
| CCEE | PLD, MRE, InfoMercado |
| ANA — SNIRH, HidroWeb, SNISB | hidrologia e segurança de barragens |
| EPE | PDE, BEN, WebMap |
| IBGE | malha territorial das unidades federativas |

As curvas de permanência, a relação entre armazenamento e PLD e as faixas de capex
e O&M dos simuladores são **ilustrativas**, para ensino do mecanismo, e estão
identificadas como tal em cada gráfico.
