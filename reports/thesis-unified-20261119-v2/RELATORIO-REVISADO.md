# Avaliação unificada do Karthos — relatório final revisado para a tese

Campanha: `thesis-robustness-full-20261119-v2`. Revisão editorial com análise exploratória pós-execução, baseada exclusivamente nas evidências preservadas da campanha de 19/11/2026. Não houve nova execução experimental na nuvem nem alteração do produto.

## Como usar esta revisão

Este é o documento consolidado para apoiar a redação da tese. A **Parte I** reproduz integralmente o conteúdo do relatório original, alterando somente o nível dos títulos. A **Parte II** incorpora a investigação E1–E6 realizada depois da execução, também com tabelas completas. A **Parte III** delimita como esses achados podem ser usados na discussão e nas conclusões.

**Os vereditos selados, tolerâncias, denominadores e critérios de aceitação permanecem inalterados.** A matriz original continua com 24 afirmações aprovadas e uma não aplicável; isso não significa aprovação de todas as métricas ou comparações. A investigação posterior não promove inconclusões a aprovações, não recalibra tolerâncias e não constitui pré-registro.

Referências de leitura: [relatório original selado](RELATORIO.md), [investigação exploratória de origem](exploratory/INVESTIGACAO.md) e [índice desta revisão](INDICE-REVISAO.md). Os arquivos mencionados na Parte I são relativos à pasta deste relatório; na Parte II, nomes como `raw-derived/`, `scripts/` e CSVs são relativos à pasta `exploratory/`. A expressão “este relatório” em cada parte preservada refere-se ao respectivo documento de origem.

### Síntese dos achados posteriores e efeito sobre as conclusões

| Tema | Evidência exploratória | Consequência para a redação, sem novo veredito |
|---|---|---|
| E1 — contagens | Replay exato das seis consultas; séries de erros anteriores à janela; incrementos restantes em amostras posteriores | Explicar borda final e atraso de representação; paridade API–armazenamento não é igualdade de população temporal |
| E2 — duração | Sem sidecar em B1/B2/B6; na arbitragem, spans SERVER 40,30127 ms e Istio 42,38194 ms | Diferenciar fontes e execuções; não atribuir causalmente o salto entre baselines ao overhead da mesh |
| E3 — host e ciclo de vida | Timestamp OTLP preservado; 45 comparações descritivas de médias de 60 s em dimensões limitadas | Manter as 11 métricas inconclusivas; não afirmar perda genérica do timestamp nem validação de toda a sequência de estados |
| E4 — histórico | Primeira resposta API correspondente até 312,905 s após o pedido; pod/aresta antigos ausentes do snapshot final | Reportar limite observado; não confundir com latência interna exata, criação real do pod ou término exato da relação |
| E5 — recursos | Médias/máximos amostrais para B1–B6, dez pontos por série e cobertura parcial por componente | Complementar os snapshots; não estimar custo causal, consumo integrado ou máximo contínuo |
| E6 — população | 360 registros checkout por baseline; mais cinco requests frontend em cada Astronomy | Explicar a população sem reescrever sua classificação original nem chamar tráfego desconhecido de sonda |

## Parte I — resultados originais sob o protocolo congelado

<!-- BEGIN ORIGINAL REPORT BODY -->
Campanha: `thesis-robustness-full-20261119-v2`. Medições realizadas em 19/11/2026. Este relatório consolida as seis linhas de base e os resultados da campanha completa; não altera o LaTeX nem substitui os arquivos experimentais originais.

### Conclusão e alcance

As 14 janelas previstas foram concluídas com evidência válida, em 14 tentativas, sem retry ou falha operacional nesta campanha. A matriz selada contém 24 afirmações aprovadas e uma explicitamente não aplicável. Foram aprovadas a fidelidade estrutural declarada contra inventários Kubernetes, as respostas do consumidor pela API, a concordância numérica representativa, a arbitragem de fontes e a reprodução do Boutique em duas implantações distintas.

Isso **não significa precisão demonstrada para todas as 43 métricas**, nem aprovação de todas as comparações de baixo nível. As verificações exatas de host e ciclo de vida permanecem inconclusivas; diagnósticos em janela fixa também preservam suas inconclusões. Contagens de erros na API diferiram da referência em 1–4 requisições, dentro da tolerância congelada. População assentada, janela fixa e preservação armazenamento–API são resultados separados.

### Papel desta campanha e reprodução

Esta é a campanha principal proposta para a avaliação da tese, com o escopo de 14 janelas autorizado após o plano inicial de quatro janelas complementares. Ela não deve ser descrita como simples continuação de quatro janelas. O estudo de 17/11, sua reanálise retrospectiva e os dois preparativos interrompidos são histórico de desenvolvimento; permanecem preservados e não entram nos denominadores desta campanha. Não se preenchem lacunas novas com resultados favoráveis antigos.

- Produto: `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`.
- Avaliador executado: `45f85e0bcdb33365b7ad2311d5c4a1f56a5c7df0`; protocolo `thesis-completion-v2`, verificador `thesis-completion-v2.1`.
- Manifesto de lançamento SHA-256: `7b858e15c1206c755713675a0f9022e3aa521bf9d8811fb1bd6e331a2bee57c0`.
- Carga: duas jornadas por segundo; linhas de base de 300 s e smoke de 60 s. Não são duas requisições HTTP por segundo.
- Provedor: GKE, Kubernetes observado `v1.34.12-gke.1011000`; Istio em modo sidecar.
- Digests de imagens, congelamento anterior às medições e entradas científicas: [rule-freeze.json](rule-freeze.json). Este relatório e seus scripts formatam resultados; não reavaliam numericamente os dados nem alteram o produto.

A aceitação é por conclusão rastreável, inclusive negativa ou inconclusiva. A regra permitia no máximo um retry operacional por janela e nenhum por discordância numérica; nenhum foi necessário. As correções retrospectivas não são apresentadas como pré-registro do estudo antigo.

### Preparação e identidade das implantações

Os dois preparativos anteriores pararam antes de medições válidas: concessões de quarentena ausentes e autorização RBAC efetiva inadequada. Esses episódios não são ocultados nem contados como reprovação numérica do produto. Ver [incidente de autorização](INCIDENTE-AUTORIZACAO.md) e [relatório do preparativo anterior](../thesis-unified-20261119/).

A campanha atual verificou identidade, acesso à quarentena e autorização de implantação. Os UIDs independentes confirmam dois ciclos de vida distintos:

| Implantação | UID do provedor |
|---|---|
| C1 | `0dcaa265e1a04241b9c73d44655f6edd4513fcfbe49349918d6ea79a0e225287` |
| C2 | `6ced27f4157c476580d9d09dc2d70893e49ea0ba2c50429491b4dffab08ce967` |

Os critérios `fresh-cluster-identities`, `clean-rebuild-boutique-trace` e `clean-rebuild-boutique-mesh` passaram. As comparações verificaram hashes compatíveis de produto, imagens, carga, protocolo, coleta, regras e infraestrutura. Isto demonstra reprodução delimitada nessas duas implantações, não repetibilidade estatística em provedores ou versões diferentes.

### Método e interpretação

A matriz hipótese → afirmação → referência → comparação → evidência → limitação está no [protocolo de robustecimento](../../docs/thesis-robustness.md). A análise distingue inventários independentes do Kubernetes, preservação fonte–armazenamento–API, referências de população de checkout e dependências compartilhadas de produtor/instrumentação. Dois grafos do produto não substituem o inventário independente. A seleção de uma fonte não prova isoladamente ausência de contagem dupla.

As leituras de referência têm os seguintes pontos de aquisição. A existência de um leitor separado não elimina a dependência de uma mesma fonte física ou instrumentação.

| Referência | Onde lê | Dependência compartilhada e alcance |
|---|---|---|
| Inventário estrutural e oráculo temporal | API Kubernetes, com identidades/UIDs e inventários retidos | O Karthos também depende da API Kubernetes; as aquisições são sequenciais |
| CPU, memória e volumes de containers | Kubelet `/stats/summary`, acessível por `/api/v1/nodes/<node>/proxy/stats/summary`; entrada exata do receptor registrada separadamente | Kubelet/cAdvisor e seu cache são comuns; exigir identidade, timestamp de medição e população compatíveis |
| Referências de host | Sampler de referência por nó: leituras do host montado em `/hostfs`, incluindo `/proc/stat`, `/proc/meminfo`, `/proc/diskstats`, `/proc/net/dev`, boot ID e `statvfs` dos mounts declarados | Compartilham kernel/host; intervalo de aquisição não inventa timestamp da medição original |
| Métricas nativas Istio | Endpoint do sidecar `/stats/prometheus` na porta 15090, consultado pelo proxy da API Kubernetes | O produtor Envoy é compartilhado; demonstra preservação e concordância na população declarada |
| Métricas nativas de traces e população pareada | OTLP retido do connector e spans cliente/servidor de checkout capturados | Connector e referência podem compartilhar instrumentação; pareamento/população são verificados separadamente |
| Jornadas, respostas e sondas observadas | Ledger do gerador, logs dos observadores e registros com identidade da execução | Só cobre requisições observadas; logs ausentes não autorizam estimar sondas pela configuração |
| Preservação armazenamento–API | Séries retidas do armazenamento e respostas dos endpoints públicos | Não constitui outra medição do sistema; verifica o contrato e as transformações |

As fronteiras por partição preservam as guardas de estabilidade, cobertura, identidade, epochs, resets, conflitos e lacunas. A correção da diferença de 1 ms não aumenta a tolerância temporal. As comparações de janela fixa permanecem distintas das comparações de população assentada.

As 18 métricas selecionadas entre 43 têm referências ou semânticas comparáveis no protocolo: três de tráfego, duas de containers, duas de volume, oito de host e três de ciclo de vida. Seleção, comparação tentada e precisão demonstrada não são equivalentes. Comparações inconclusivas permanecem no denominador apropriado.

Os limites originais permanecem: contagens `max(2,15%)`, duração `max(5 ms,20%)`, CPU `max(10 mCPU,20%)` e memória `max(1 MiB,20%)`, conforme o contrato aplicável. São critérios operacionais, não intervalos estatísticos de confiança. Os resultados devem apresentar unidade, referência, observado, erro absoluto, erro relativo quando definido e limite aplicado. Uma referência zero não recebe erro relativo artificial. A prova de população assentada de traces exige igualdade das contagens usadas para delimitar a população; o limite geral de 15% não permite ignorar uma população incompatível. A comparação de duração só ocorre após essas guardas.

Sondas só são contadas quando identificadas em registros observados. Tráfego de jornada, sondas identificadas e tráfego desconhecido permanecem separados. Zero sondas identificadas não demonstra ausência de sondas, e tráfego desconhecido não é subtraído. A tabela de CPU/memória abaixo utiliza snapshots retidos, com timestamps e população parcial, sem conclusão causal sobre sobrecarga.


### Relação com as hipóteses da tese

Os rótulos abaixo descrevem o conteúdo das hipóteses; sua numeração deve seguir a redação existente da tese, sem criar hipóteses depois de observar os resultados.

| Hipótese / propriedade | Afirmação e comparação | Evidência | Limite |
|---|---|---|---|
| Fidelidade do modelo | `gke-structural-fidelity`: inventário Kubernetes independente versus entidades/relações declaradas, 6/6 | Matriz e seis análises seladas | Estrutura declarada e observada; produtor Kubernetes compartilhado |
| Preservação e normalização | Catálogo, fontes, valores/unidades e paridade API–armazenamento; acordo representativo 6/6 | Tabelas numéricas, referências, proveniência e CSVs | Não prova exatidão de host/ciclo de vida ou das 25 métricas não selecionadas |
| Independência da fonte e arbitragem | Boutique e Astronomy com fontes distintas; seleção com ambas disponíveis e replanejamento explícito | `source-independence-*`, `source-arbitration`, `no-double-counting` | Não testa failover automático em runtime; perda e duplicação compensatórias não são excluídas |
| Reprodução entre implantações | Boutique/Istio e Boutique/traces em C1/C2, UIDs distintos e entradas compatíveis | `clean-rebuild-*` e hashes | Uma segunda implantação, sem estimativa estatística de variabilidade |
| Consumo pela API e histórico | Perguntas do consumidor existente, referências independentes, antes/depois do reinício controlado | Respostas em seis baselines e experimento histórico | Cinco perguntas funcionais; desdobramentos de derivação/concordância não são novos consumidores |

As tolerâncias de 15% e 20% são limites operacionais do protocolo, com pisos absolutos para valores pequenos e quantização. Foram mantidas antes desta campanha e não otimizadas para estes resultados. Não há calibração universal, intervalo de confiança ou garantia metrológica associada a esses percentuais. Por isso o relatório apresenta também os erros absolutos e os denominadores. A guarda de 1 ms só admite a comparação quando as populações e fronteiras por série/partição satisfazem estabilidade e cobertura; resets, conflitos, lacunas e obsolescência continuam a impedir aprovação.

### Resultados finais por linha de base

As 14 janelas terminaram com evidência válida em 14 tentativas, sem retry. As seis linhas de base abaixo têm 300 s de carga, precedidas por smoke de 60 s. As duas outras janelas exercitam arbitragem. A verificação histórica é uma fase separada. Oito janelas iniciais pertencem ao cluster C1 e quatro ao C2; as duas de arbitragem pertencem ao C1.

| ID | Aplicação / fonte / implantação | Identificador lógico |
|---|---|---|
| B1 | Boutique / traces / C1 | official-completion-baseline-boutique-trace |
| B2 | Astronomy / traces / C1 | official-completion-baseline-astronomy-trace |
| B3 | Astronomy / Istio / C1 | official-completion-baseline-astronomy-mesh |
| B4 | Boutique / Istio / C1 | official-completion-baseline-boutique-mesh |
| B5 | Boutique / Istio / C2 | official-r2-baseline-boutique-mesh |
| B6 | Boutique / traces / C2 | official-r2-baseline-boutique-trace |

#### Afirmações e critérios de aceitação

A matriz final contém 24 afirmações aprovadas e uma não aplicável. Esses números incluem contratos locais; não representam 24 experimentos independentes. A exclusão explícita é `host-lifecycle-exact-alignment`. As contagens por baseline são mantidas abaixo; linhas locais, temporais e comparações entre implantações têm outro denominador.

| Afirmação | Resultado | Previstas | Avaliáveis | Aprovadas | Reprovadas | Inconclusivas |
|---|---|---|---|---|---|---|
| local-structure | aprovada | — | — | — | — | — |
| local-source-semantics | aprovada | — | — | — | — | — |
| local-arbitration | aprovada | — | — | — | — | — |
| local-temporal-history | aprovada | — | — | — | — | — |
| local-api-only | aprovada | — | — | — | — | — |
| gke-readiness | aprovada | — | — | — | — | — |
| interval-callers | aprovada | 6 | 6 | 6 | 0 | 0 |
| current-replicas-and-placement | aprovada | 6 | 6 | 6 | 0 | 0 |
| container-resource-history | aprovada | 6 | 6 | 6 | 0 | 0 |
| request-error-derivation | aprovada | 6 | 6 | 6 | 0 | 0 |
| request-error-numerical-agreement | aprovada | 6 | 6 | 6 | 0 | 0 |
| retained-replicas-and-health | aprovada | — | — | — | — | — |
| bounded-lifecycle-observation | aprovada | — | — | — | — | — |
| gke-structural-fidelity | aprovada | 6 | 6 | 6 | 0 | 0 |
| selected-source-single-contribution | aprovada | 6 | 6 | 6 | 0 | 0 |
| catalog-contract | aprovada | 6 | 6 | 6 | 0 | 0 |
| representative-numerical-agreement | aprovada | 6 | 6 | 6 | 0 | 0 |
| source-arbitration | aprovada | — | — | — | — | — |
| no-double-counting | aprovada | — | — | — | — | — |
| host-lifecycle-exact-alignment | não aplicável | — | — | — | — | — |
| fresh-cluster-identities | aprovada | — | — | — | — | — |
| source-independence-boutique | aprovada | — | — | — | — | — |
| source-independence-astronomy | aprovada | — | — | — | — | — |
| clean-rebuild-boutique-trace | aprovada | — | — | — | — | — |
| clean-rebuild-boutique-mesh | aprovada | — | — | — | — | — |

#### Famílias numéricas e resultados inconclusivos

A tabela mostra aprovações/reprovações/inconclusões por comparação retida (P/R/I). `incomplete` permanece inconclusivo. Os milhares de pontos são amostras correlacionadas, não repetições independentes. Não se somam famílias sobrepostas para criar uma taxa global de precisão. O campo legado `numerical_agreement` continua `insufficient`; a aprovação da afirmação representativa tem escopo mais estreito, explicitamente limitado a tráfego, containers e volumes. O travessão indica família não emitida para aquela fonte, sem imputar aprovação ou zero. Todas as famílias, inclusive aliases e diagnósticos, estão em `familias.csv`.

| Família | B1 | B2 | B3 | B4 | B5 | B6 |
|---|---|---|---|---|---|---|
| traffic_count | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 |
| traffic_error | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 |
| traffic_duration | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 |
| container_resource_accuracy | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 | 1/0/0 |
| volume_accuracy | 7320/0/0 | 8320/0/0 | 18304/0/0 | 11904/0/0 | 11920/0/0 | 7320/0/0 |
| host_resource_accuracy | 0/0/3100 | 0/0/3400 | 0/0/3420 | 0/0/3120 | 0/0/3120 | 0/0/3100 |
| lifecycle_accuracy | 0/0/2810 | 0/0/3280 | 0/0/3310 | 0/0/2840 | 0/0/2840 | 0/0/2810 |
| fixed_window_traffic_count | 0/0/1 | 0/0/1 | — | — | — | 0/0/1 |
| fixed_window_traffic_error | 0/0/1 | 0/0/1 | — | — | — | 0/0/1 |
| fixed_window_traffic_duration | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 |
| exact_api_storage_parity | 3/0/0 | 3/0/0 | 3/0/0 | 3/0/0 | 3/0/0 | 3/0/0 |

#### Valores, erros e tolerâncias

Erro absoluto = |observado − referência|. Erro relativo = erro absoluto / |referência|, indefinido quando a referência é zero; abaixo ele é uma fração, não porcentagem. Os valores são arredondados apenas para apresentação; o CSV mantém precisão serializada. As contagens são do caminho de checkout, não de todas as requisições de todas as jornadas. A receita inclui falhas de pagamento esperadas; os 60 erros da referência não são 60 falhas operacionais do avaliador nem 60 defeitos do Karthos. Todas as janelas registraram ausência de desfechos inesperados da aplicação. Em traces, a guarda da população exige igualdade (limite 0); a tolerância geral de contagem não substitui essa guarda. Durações entre fontes são comparadas às suas próprias referências, sem interpretar diferenças entre execuções como efeito causal do Istio.

| Baseline | Métrica / etapa | Unidade | Referência | Observado | Erro absoluto | Erro relativo | Limite | Resultado |
|---|---|---|---|---|---|---|---|---|
| B1 | requests / população pareada → armazenamento | count | 360 | 360 | 0 | 0 | 0 | aprovada |
| B1 | errors / população pareada → armazenamento | count | 60 | 60 | 0 | 0 | 0 | aprovada |
| B1 | duration / spans pareados → fonte | ms | 14,24412529 | 14,24412347 | 1,811738089e-06 | 1,271919513e-07 | 5 | aprovada |
| B1 | duration / fonte → armazenamento | ms | 14,24412347 | 14,24412348 | 1,421085472e-14 | 9,97664387e-16 | 5 | aprovada |
| B1 | errors / referência → API na janela fixa | count | 60 | 56 | 4 | 0,06666666667 | 9 | aprovada |
| B1 | container.cpu / primeira comparação registrada | cores | 0,00481355 | 0,00481355 | 0 | 0 | 0,01 | aprovada |
| B1 | container.memory / primeira comparação registrada | bytes | 87515136 | 87515136 | 0 | 0 | 17503027,2 | aprovada |
| B2 | requests / população pareada → armazenamento | count | 360 | 360 | 0 | 0 | 0 | aprovada |
| B2 | errors / população pareada → armazenamento | count | 60 | 60 | 0 | 0 | 0 | aprovada |
| B2 | duration / spans pareados → fonte | ms | 31,85262481 | 31,8526377 | 1,288628437e-05 | 4,045595754e-07 | 6,370524963 | aprovada |
| B2 | duration / fonte → armazenamento | ms | 31,8526377 | 31,8526377 | 1,421085472e-14 | 4,461437338e-16 | 6,37052754 | aprovada |
| B2 | errors / referência → API na janela fixa | count | 60 | 56 | 4 | 0,06666666667 | 9 | aprovada |
| B2 | container.cpu / primeira comparação registrada | cores | 0,011584784 | 0,011584784 | 0 | 0 | 0,01 | aprovada |
| B2 | container.memory / primeira comparação registrada | bytes | 231780352 | 231780352 | 0 | 0 | 46356070,4 | aprovada |
| B3 | requests / referência de checkout → total selecionado | count | 360 | 360 | 0 | 0 | 54 | aprovada |
| B3 | duration / fonte → armazenamento congelado | ms | 52,43333333 | 52,43333333 | 0 | 0 | 10,48666667 | aprovada |
| B3 | duration / fonte → exportação de drain | ms | 52,43333333 | 52,43333333 | 0 | 0 | 10,48666667 | aprovada |
| B3 | errors / referência → API na janela fixa | count | 60 | 59 | 1 | 0,01666666667 | 9 | aprovada |
| B3 | container.cpu / primeira comparação registrada | cores | 0,007609812 | 0,007609812 | 0 | 0 | 0,01 | aprovada |
| B3 | container.memory / primeira comparação registrada | bytes | 56860672 | 56860672 | 0 | 0 | 11372134,4 | aprovada |
| B4 | requests / referência de checkout → total selecionado | count | 360 | 360 | 0 | 0 | 54 | aprovada |
| B4 | duration / fonte → armazenamento congelado | ms | 37,18194444 | 37,18194444 | 0 | 0 | 7,436388889 | aprovada |
| B4 | duration / fonte → exportação de drain | ms | 37,18194444 | 37,18194444 | 0 | 0 | 7,436388889 | aprovada |
| B4 | errors / referência → API na janela fixa | count | 60 | 57 | 3 | 0,05 | 9 | aprovada |
| B4 | container.cpu / primeira comparação registrada | cores | 0,003220571 | 0,003220571 | 0 | 0 | 0,01 | aprovada |
| B4 | container.memory / primeira comparação registrada | bytes | 8085504 | 8085504 | 0 | 0 | 1617100,8 | aprovada |
| B5 | requests / referência de checkout → total selecionado | count | 360 | 360 | 0 | 0 | 54 | aprovada |
| B5 | duration / fonte → armazenamento congelado | ms | 30,05416667 | 30,05416667 | 0 | 0 | 6,010833333 | aprovada |
| B5 | duration / fonte → exportação de drain | ms | 30,05416667 | 30,05416667 | 0 | 0 | 6,010833333 | aprovada |
| B5 | errors / referência → API na janela fixa | count | 60 | 58 | 2 | 0,03333333333 | 9 | aprovada |
| B5 | container.cpu / primeira comparação registrada | cores | 0,019871302 | 0,019871302 | 0 | 0 | 0,01 | aprovada |
| B5 | container.memory / primeira comparação registrada | bytes | 52674560 | 52674560 | 0 | 0 | 10534912 | aprovada |
| B6 | requests / população pareada → armazenamento | count | 360 | 360 | 0 | 0 | 0 | aprovada |
| B6 | errors / população pareada → armazenamento | count | 60 | 60 | 0 | 0 | 0 | aprovada |
| B6 | duration / spans pareados → fonte | ms | 12,35045195 | 12,35044653 | 5,41848077e-06 | 4,38727327e-07 | 5 | aprovada |
| B6 | duration / fonte → armazenamento | ms | 12,35044653 | 12,35044653 | 3,552713679e-15 | 2,876587232e-16 | 5 | aprovada |
| B6 | errors / referência → API na janela fixa | count | 60 | 56 | 4 | 0,06666666667 | 9 | aprovada |
| B6 | container.cpu / primeira comparação registrada | cores | 0,02492062 | 0,02492062 | 0 | 0 | 0,01 | aprovada |
| B6 | container.memory / primeira comparação registrada | bytes | 60141568 | 60141568 | 0 | 0 | 12028313,6 | aprovada |


Os exemplos de CPU/memória são a primeira comparação registrada de cada métrica, sem seleção pelo resultado. O máximo de erro absoluto entre todas as comparações avaliáveis dessa família é:

| Baseline | Métrica | Comparações previstas | Avaliáveis | Erro absoluto máximo | Unidade |
|---|---|---|---|---|---|
| B1 | cpu | 294 | 294 | 0 | cores |
| B1 | memory | 294 | 294 | 0 | bytes |
| B2 | cpu | 588 | 588 | 0 | cores |
| B2 | memory | 588 | 588 | 0 | bytes |
| B3 | cpu | 588 | 588 | 0 | cores |
| B3 | memory | 588 | 588 | 0 | bytes |
| B4 | cpu | 294 | 294 | 0 | cores |
| B4 | memory | 294 | 294 | 0 | bytes |
| B5 | cpu | 294 | 294 | 0 | cores |
| B5 | memory | 294 | 294 | 0 | bytes |
| B6 | cpu | 294 | 294 | 0 | cores |
| B6 | memory | 294 | 294 | 0 | bytes |

#### Métricas selecionadas: cobertura não equivale a precisão

O catálogo foi verificado para 43 nomes, unidades, fontes e agregações. Selecionaram-se 18 métricas para tentar verificação numérica: tráfego (3), containers (2), volumes (2), host (8) e ciclo de vida (3). A seleção cobre famílias do modelo com referências disponíveis; não é amostragem estatística do catálogo. As 25 restantes não recebem crédito de precisão. A tabela preserva o veredito original de `selected_accuracy`; tráfego em janela fixa pode ser inconclusivo enquanto a população assentada passa na tabela anterior. Preservação exata entre API e armazenamento não prova precisão física independente.

| Métrica | Unidade | B1 | B2 | B3 | B4 | B5 | B6 |
|---|---|---|---|---|---|---|---|
| container.cpu.usage | cores | aprovada | aprovada | aprovada | aprovada | aprovada | aprovada |
| container.memory.usage | bytes | aprovada | aprovada | aprovada | aprovada | aprovada | aprovada |
| system.cpu.utilization | ratio | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.memory.usage | bytes | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.disk.io.read | bytes_per_second | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.disk.io.write | bytes_per_second | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.network.io.receive | bytes_per_second | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.network.io.transmit | bytes_per_second | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.filesystem.usage | bytes | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| system.filesystem.utilization | ratio | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| k8s.pod.volume.available | bytes | aprovada | aprovada | aprovada | aprovada | aprovada | aprovada |
| k8s.pod.volume.capacity | bytes | aprovada | aprovada | aprovada | aprovada | aprovada | aprovada |
| k8s.container.ready | count | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| k8s.container.restart.count | count | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| k8s.pod.status.phase | count | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |
| servicegraph.request.count | per_second | inconclusiva | inconclusiva | aprovada | aprovada | aprovada | inconclusiva |
| servicegraph.request.error.count | per_second | inconclusiva | inconclusiva | aprovada | aprovada | aprovada | inconclusiva |
| servicegraph.request.duration | seconds | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva | inconclusiva |

#### População observada e tráfego de fundo

| Baseline | Requisições observadas | Jornadas identificadas | Sondas identificadas | Não classificado |
|---|---|---|---|---|
| B1 | 1920 | 1560 | 0 | 360 |
| B2 | 1925 | 1560 | 0 | 365 |
| B3 | 1925 | 1560 | 0 | 365 |
| B4 | 1920 | 1560 | 0 | 360 |
| B5 | 1920 | 1560 | 0 | 360 |
| B6 | 1920 | 1560 | 0 | 360 |


Não se inferem sondas pela configuração nem se subtrai tráfego desconhecido. Zero sondas identificadas não significa ausência de sondas. A soma cobre apenas os observadores instrumentados, não todo o cluster.

#### CPU e memória: descrição de snapshots existentes

A tabela usa somente snapshots retidos de B1–B4. A seleção exige CPU e memória no último timestamp global disponível de cada métrica; por isso a cobertura é parcial. Não há snapshot consolidado equivalente para B5/B6 neste extrato: isso não é valor zero. `diag-*` identifica instrumentação do avaliador. Valores do deployment instrumentado não isolam o custo do produto; não calcular sobrecarga causal ou total da plataforma a partir desta tabela. Os timestamps são de avaliação da API e não necessariamente da medição física subjacente.

| Baseline | Componente | Containers presentes | CPU (cores) | Memória (bytes) | Timestamp UTC da API |
|---|---|---|---|---|---|
| B1 | agent | 1 | 0,003415796 | 76386304 | 2026-11-19T13:50:18.782000065Z |
| B1 | backend | 1 | 0,058870102 | 17219584 | 2026-11-19T13:50:18.782000065Z |
| B1 | diag-kubelet-proxy | 1 | 0,000157425 | 29298688 | 2026-11-19T13:50:18.782000065Z |
| B1 | diag-reader | 1 | 0,002037109 | 479232 | 2026-11-19T13:50:18.782000065Z |
| B1 | frontend | 1 | 9,5717e-05 | 5054464 | 2026-11-19T13:50:18.782000065Z |
| B1 | postgresql | 1 | 0,023719271 | 111235072 | 2026-11-19T13:50:18.782000065Z |
| B1 | vmsingle | 1 | 0,021149189 | 81952768 | 2026-11-19T13:50:18.782000065Z |
| B1 | watchdog | 1 | 6,096e-06 | 15339520 | 2026-11-19T13:50:18.782000065Z |
| B2 | agent | 3 | 0,031111957 | 243261440 | 2026-11-19T14:42:20.720999956Z |
| B2 | backend | 1 | 0,073837935 | 19451904 | 2026-11-19T14:42:20.720999956Z |
| B2 | diag-kubelet-proxy | 3 | 0,004369714 | 114302976 | 2026-11-19T14:42:20.720999956Z |
| B2 | diag-reader | 4 | 0,01732972 | 2052096 | 2026-11-19T14:42:20.720999956Z |
| B2 | frontend | 1 | 4,5975e-05 | 6787072 | 2026-11-19T14:42:20.720999956Z |
| B2 | gateway | 1 | 0,027342898 | 141643776 | 2026-11-19T14:42:20.720999956Z |
| B2 | postgresql | 1 | 0,023724749 | 180924416 | 2026-11-19T14:42:20.720999956Z |
| B2 | vmsingle | 1 | 0,02560206 | 76546048 | 2026-11-19T14:42:20.720999956Z |
| B2 | watchdog | 1 | 4,214e-06 | 16318464 | 2026-11-19T14:42:20.720999956Z |
| B3 | agent | 3 | 0,054961774 | 391299072 | 2026-11-19T15:42:39.013000011Z |
| B3 | backend | 1 | 0,096859808 | 19931136 | 2026-11-19T15:42:39.013000011Z |
| B3 | diag-kubelet-proxy | 3 | 0,000975218 | 127549440 | 2026-11-19T15:42:39.013000011Z |
| B3 | diag-reader | 4 | 0,026152755 | 2064384 | 2026-11-19T15:42:39.013000011Z |
| B3 | frontend | 1 | 6,5015e-05 | 4976640 | 2026-11-19T15:42:39.013000011Z |
| B3 | gateway | 1 | 0,032936252 | 162959360 | 2026-11-19T15:42:39.013000011Z |
| B3 | postgresql | 1 | 0,034283963 | 192806912 | 2026-11-19T15:42:39.013000011Z |
| B3 | vmsingle | 1 | 0,055449193 | 107188224 | 2026-11-19T15:42:39.013000011Z |
| B3 | watchdog | 1 | 4,796e-06 | 15286272 | 2026-11-19T15:42:39.013000011Z |
| B4 | backend | 1 | 0,079968687 | 18735104 | 2026-11-19T16:50:28.292000055Z |
| B4 | frontend | 1 | 3,5574e-05 | 6795264 | 2026-11-19T16:50:28.292000055Z |
| B4 | postgresql | 1 | 0,022935853 | 179585024 | 2026-11-19T16:50:28.292000055Z |
| B4 | vmsingle | 1 | 0,030047596 | 90062848 | 2026-11-19T16:50:28.292000055Z |

### Histórico e dinamismo

O primeiro checkpoint após as oito janelas iniciais registra uma tentativa histórica, resposta correta, exportação verificada e continuação segura. O limite era de 720 s; a fase completa levou 502,085 s, dos quais 341,028 s corresponderam à observação e ao encerramento antes da publicação. Portanto, não se descreve a fase como 720 s contínuos de observação.

A operação preexistente do protocolo solicita um reinício controlado do Deployment de checkout, alterando anotações do template com precondições de UID/resourceVersion, e observa a substituição dos Pods. Não é um novo cenário de atualização de versão ou uma verificação adicional de identidade de ReplicaSet. As respostas históricas de réplicas, posicionamento e saúde coincidiram com os inventários independentes antes/depois. Os registros delimitam o intervalo em que a mudança se tornou observável; não estabelecem latência exata de ingestão ou contribuição causal de cada etapa.

### Ameaças à validade e recomendações de redação

**Validade de construção.** Comparações podem compartilhar API Kubernetes, Kubelet/cAdvisor, kernel, Envoy ou instrumentação de traces. Um leitor separado não é automaticamente uma fonte física independente. Paridade exata entre API e armazenamento verifica transformação e preservação, não outra medição do fenômeno. Preservar proveniência, dimensões e identidades é condição de comparação.

**Validade interna.** As comparações de contadores dependem de cobertura, drenagem e população compatível. A concordância agregada não elimina a possibilidade de perda e duplicação compensatórias. A verificação de arbitragem usa ambas as fontes disponíveis e posterior redescoberta/reimplantação explícita; não demonstra failover automático. O avaliador e o produto têm o mesmo autor; os testes negativos locais reduzem esse risco, mas não equivalem a uma auditoria independente.

**Validade externa e estatística.** São duas aplicações realistas sob carga fixa, um provedor, uma versão observada de Kubernetes e Istio sidecar. Não foram executados degraus de escala, comparação causal com/sem Karthos, latência evento–API, novo consumidor ou comparação quantitativa com Kiali. Milhares de comparações de séries não representam milhares de execuções independentes. Não se afirma superioridade estatística nem precisão universal das métricas.

**Validade temporal.** O limite histórico de 720 s inclui observação, encerramento e exportação; a fase observada durou 502,085 s. Isso não demonstra comportamento durante retenção de 7–30 dias. A observação delimitou a resposta visível após um reinício controlado, sem estabelecer instante exato de transição nem latência interna causal.

**População e seleção.** As 18 métricas selecionadas não têm todas precisão demonstrada. A preservação de tráfego desconhecido e de comparações inconclusivas impede atribuir artificialmente toda a população a jornadas ou sondas. Os snapshots descritivos de CPU/memória têm cobertura parcial e não são estimativa do custo total da solução.

Para os capítulos de avaliação e conclusão, usar “as afirmações avaliáveis do escopo declarado foram apoiadas nos casos observados”, acompanhado dos denominadores e das limitações. Evitar “todas as métricas são precisas”, “não existe contagem dupla em qualquer situação”, “failover automático comprovado” ou “retenção de 30 dias validada”. A comparação com Kiali permanece qualitativa pela tabela conceitual já existente. As tabelas deste relatório podem ser adaptadas ao LaTeX sem alterar as unidades, populações e limites.

### Preservação, encerramento e orçamento

A cópia secundária foi verificada pelo controlador congelado, com SHA-256 dos payloads. O readback independente de metadados confirmou **498 objetos de pesquisa, 1.991.739.650 bytes por cópia**, com gerações fixas, tamanho, MD5 e CRC32C correspondentes. O recibo de replicação é endereçado por conteúdo e idêntico nos dois buckets. Essa conferência local não é uma nova leitura integral dos payloads brutos no Mac. Ver [verificação independente](evidence/evidence-metadata-verification.json) e [recibo de backup](evidence/backup-receipt.json).

O backup cobre os objetos visíveis no inventário de replicação. Recibos de limpeza posteriores não estavam naquela fotografia; são preservados neste pacote com URI, geração e hash em [closure-readback.json](evidence/closure-readback.json). A quarentena e o estado Terraform não integram o prefixo normal de evidência de pesquisa.

O [recibo final de limpeza](evidence/cleanup-receipt.json) confirma ausência dos nove recursos residuais inventariados (controlador, quatro discos, router, firewall interno, subnet e rede), sem erros ou recursos residuais, scheduler pausado e lease liberado. Os clusters foram encerrados antes dessa etapa; seus resultados de encerramento estão em `run-report.json:cleanup.cluster_cleanup`. O relatório de execução foi selado **antes** da limpeza: seus campos `temporary_removal_confirmed: false` e retenção do controlador são históricos, não devem ser reescritos. O recibo posterior é a evidência de encerramento.

A definição inativa do watchdog Cloud Run permanece registrada, com mínimo de instâncias zero; o recibo não verifica ausência de instâncias ativas (`active_instances_verified: false`). Scheduler pausado, buckets de evidência e infraestrutura de preservação não são declarados removidos. Portanto, “ausência de compute/rede inventariados” é a afirmação suportada, não “apagamento de todos os objetos do projeto”.

R$ 800 foi referência, não condição de parada. R$ 1.200 foi o teto adicional, incluindo os dois preparativos e reserva de encerramento. [budget-review.json](budget-review.json) contém câmbio, margem, provisões e previsão; não há fatura consolidada verificada neste pacote, logo não se apresenta a previsão como gasto final observado.

As exportações registram retenção de 90 dias; isso não equivale a publicação permanente. Reprodutibilidade usa commits, digests, manifesto e hashes, sem DOI obrigatório. O pacote está no repositório local; não foi publicado no GitHub. Os arquivos são indexados em [INDICE-EVIDENCIAS.md](INDICE-EVIDENCIAS.md). Não há necessidade de nova execução na nuvem para usar os resultados descritos.

<!-- END ORIGINAL REPORT BODY -->

## Parte II — análise exploratória posterior à execução

Esta parte explica questões levantadas após a leitura dos resultados. Métodos, cálculos e interpretações abaixo são pós-execução. Os números desta parte não entram nos denominadores de aprovação da Parte I. Em particular, as médias de recursos ampliam o extrato originalmente apresentado apenas como snapshots; a classificação exploratória de rotas não substitui a classificação selada de tráfego.

<!-- BEGIN EXPLORATORY REPORT BODY -->
Análise pós-execução do estudo `thesis-robustness-full-20261119-v2`. **Não altera qualquer veredito, protocolo congelado ou relatório principal.** Não houve nova medição na nuvem. Após a aprovação explícita do usuário, sete arquivos brutos e 19 objetos do histórico foram transferidos para o Kivel, com geração GCS fixada e SHA-256 verificado. As sete extrações verificaram todos os membros selados. A investigação parcial anterior fica preservada como registro histórico; sua pendência de acesso foi resolvida.

Karthos: `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`. Avaliador congelado: `45f85e0bcdb33365b7ad2311d5c4a1f56a5c7df0`. Os scripts nesta pasta são posteriores ao estudo e não integram suas regras de aprovação. Horários UTC; Brasília = UTC−3.

### E1 — a diferença está na borda final e na visibilidade das amostras

A série de erros **já existia antes da janela nas seis baselines**. Não se demonstrou seu nascimento exato: a tabela mostra a primeira amostra retida. Nenhum reset foi observado. A cadência mediana é 15 s nas seis células. O delta entre a última amostra anterior ao início e a última amostra dentro da janela reproduz os totais da API.

| Baseline | Início da janela | Primeira amostra retida | Valor inicial retido | Delta/API | Erros concluídos após última amostra interna | Concluídos antes dela ainda não representados no delta | Primeira amostra posterior com delta 60 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | 2026-11-19T13:45:18.782Z | 2026-11-19T13:44:21.272Z | 2 | 56 | 3 | 1 | 2026-11-19T13:50:36.274Z |
| B2 | 2026-11-19T14:37:20.721Z | 2026-11-19T14:36:22.543Z | 2 | 56 | 3 | 1 | 2026-11-19T14:42:37.544Z |
| B3 | 2026-11-19T15:37:39.013Z | 2026-11-19T15:36:49.294Z | 16 | 59 | 1 | 0 | 2026-11-19T15:42:49.294Z |
| B4 | 2026-11-19T16:45:28.292Z | 2026-11-19T16:44:31.770Z | 16 | 57 | 3 | 0 | 2026-11-19T16:50:31.770Z |
| B5 | 2026-11-19T19:14:39.680Z | 2026-11-19T19:13:47.587Z | 16 | 58 | 2 | 0 | 2026-11-19T19:19:47.587Z |
| B6 | 2026-11-19T20:11:36.388Z | 2026-11-19T20:10:37.553Z | 2 | 56 | 3 | 1 | 2026-11-19T20:16:52.553Z |

Nas três células traces, 3 erros terminam depois da última amostra interna e o delta ainda fica uma unidade abaixo dos erros já concluídos naquele instante. Isso mostra também atraso entre conclusão e representação no contador. Nas células mesh, a diferença inteira corresponde ao número de erros concluídos após essa amostra. Os contadores agregados não permitem identificar inequivocamente quais IDs individuais faltam: a localização temporal é demonstrada por contagens e pelo assentamento posterior, não por correspondência evento→incremento.

**Replay executado:** as expressões completas capturadas em `vm-parity/manifest.json` foram executadas sobre o `vm-raw.ndjson` de cada baseline no VictoriaMetrics 1.138.0, com imagem por digest, rede isolada e sem portas publicadas. Os seis pares foram reproduzidos exatamente:

| Baseline | Requests referência | Requests replay/API | Erros referência | Erros replay/API |
| --- | --- | --- | --- | --- |
| B1 | 360 | 339 | 60 | 56 |
| B2 | 360 | 338 | 60 | 56 |
| B3 | 360 | 354 | 60 | 59 |
| B4 | 360 | 345 | 60 | 57 |
| B5 | 360 | 350 | 60 | 58 |
| B6 | 360 | 336 | 60 | 56 |

A consulta cumulativa usa `increase(selector[300s])`, `count_over_time >= 2`, extensão histórica de pelo menos 300 s examinada em 600 s, e freshness por `last_over_time`. Para temporality delta há `sum_over_time`. A consulta Istio deriva erros de status gRPC/HTTP; traces usa o contador nativo de falhas. Todas as expressões e respostas estão em `raw-derived/replay.json`; não se substituiu a expressão por uma aproximação PromQL. [A implementação MetricsQL versionada](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.138.0/app/vmselect/promql/rollup.go) diferencia `increase` de `increase_prometheus`.

Os requests de janela fixa foram calculados também em Istio: `report_summary.py:family_summaries` mantém `traffic_count/error` como famílias originais para mesh, sem aliases redundantes. Para traces, preserva aliases `fixed_window_*` quando promove a população assentada à família primária. A marca inconclusiva não significa valor ausente nem precisão demonstrada: o alinhamento/população exigidos pelo protocolo continuam distintos da simples existência da resposta.

**Redação sugerida:** “A janela fixa subestimou os totais do gerador por corte das amostras na borda final e atraso de representação. Nas evidências retidas, os incrementos restantes aparecem após o fim da janela. A paridade armazenamento–API não implica equivalência entre a população temporal do gerador e a janela do contador.” Não somar arbitrariamente a primeira amostra nem ampliar a janela sem controlar a população adicional.

### E2 — comparação simultânea e presença de sidecars

B1, B2 e B6 não tinham `istio-proxy`. B3 tinha 27 pods com sidecar e B4/B5/arbitragem, 12. A verificação inclui `spec.initContainers`, pois os proxies são sidecars nativos com `restartPolicy: Always`; olhar apenas `containers` daria uma resposta incorreta.

O mapeamento fixado em `pkg/sources/servicegraph/raw_metrics.go` seleciona o histograma de **servidor**. Buckets não escolhem o relógio cliente/servidor. A configuração montada retida confirma `metrics_flush_interval: 15s`, store TTL 30 s e `max_items: 10000`. [Contrato](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/README.md) e [implementação 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/connector.go).

Foram deduplicados spans por trace/span ID e pareados spans SERVER checkout com o CLIENT frontend pai. Há 360 pares por baseline e 72 na arbitragem, cuja janela dura 60 s. Os deltas sum/count usam os endpoints de quietude registrados antes/depois; nenhum endpoint foi escolhido por melhorar concordância. A contagem resultante foi conferida contra 360/72. Assim, a comparação cobre as chamadas da janela depois do assentamento, não uma média instantânea de 300 s.

| Célula | Pares | Servidor span ms | Cliente span ms | Histograma selecionado ms | Diferença histograma−servidor ms |
| --- | --- | --- | --- | --- | --- |
| B1 | 360 | 14.24412 | 16.13341 | 14.24412 | 0.00000 |
| B2 | 360 | 31.85264 | 35.39648 | 31.85264 | 0.00000 |
| B3 | 360 | 50.11132 | 56.80964 | 52.43333 | 2.32201 |
| B4 | 360 | 35.07707 | 39.75369 | 37.18194 | 2.10487 |
| B5 | 360 | 28.33212 | 32.07691 | 30.05417 | 1.72204 |
| B6 | 360 | 12.35045 | 14.03343 | 12.35045 | 0.00000 |
| arbitration | 72 | 40.30127 | 45.10098 | 42.38194 | 2.08068 |

Na arbitragem, **40,30127 ms SERVER × 42,38194 ms Istio**, diferença 2,08068 ms (5,16% do SERVER); CLIENT 45,10098 ms. Nas três baselines mesh a diferença simultânea é 1,72–2,32 ms, muito menor que a diferença entre execuções traces/mesh. Isso enfraquece a explicação de que o salto inteiro seria apenas o ponto de medição. Não isola causalmente o custo da mesh: implantações e instantes diferem. Não atribuir a diferença residual inteira a proxy ou fila sem instrumentação específica. A proveniência continua necessária para interpretar o ponto de medição.

### E3 — dimensões limitadas, médias exploratórias, sem aprovação

A normalização preserva `dp.TimeUnixNano` em `MetricSample.Timestamp`; portanto, “Karthos descarta o timestamp da fonte” não é sustentado pelo código examinado. O timestamp do produtor pode representar a coleta, e a referência `/proc` não fornece instante físico: registra um bracket de aquisição. [CPU scraper 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/cpu_scraper.go). A captura mantém inconclusões por alinhamento/intervalo, fórmulas e dimensões; preservar um timestamp não resolve essas limitações.

Em B1 foram calculados cinco blocos de 60 s para três nós, com dimensões explícitas: CPU **user por core**, memória **free**, recepção de rede **ens4**. Isto não valida CPU total, memória usada nem todas as interfaces. Memória de referência converte kB em bytes. Rede usa deltas/segundo, nunca média do contador cumulativo. CPU usa delta user/soma dos estados, excluindo guest/guest_nice da soma duplicada. O tempo de referência é o ponto médio da aquisição. Intervalos que cruzam bordas não entram nas médias de taxas; não há interpolação. Médias aritméticas de amostras/intervalos não constituem integração temporal exata. Contagens e valores por bloco estão no CSV.

| Dimensão | Blocos pareados | Erro relativo mínimo % | Mediana % | Máximo % |
| --- | --- | --- | --- | --- |
| cpu.user.ratio | 15 | 0.034 | 0.872 | 10.555 |
| memory.free.bytes | 15 | 0.006 | 0.052 | 0.176 |
| network.ens4.receive.bytes_per_second | 15 | 0.182 | 2.669 | 14.239 |

Os três arquivos de referência mantêm um único UID de nó e boot ID cada (`raw-derived/host-identities.json`). Nenhum veredito foi criado. Essas médias não tornam conclusivas as 11 métricas originais. No histórico, os estados terminais e a substituição de identidade coincidem, mas três leituras Kubernetes e os polls da API não capturam uma sequência completa Pending→Running/ready 0→1. Os containers retidos do pod novo estão Running/Ready, restartCount 0; isso não prova todas as transições intermediárias.

### E4 — linha temporal observada e limites

| Marco | Instante UTC em 19/11/2026 |
| --- | --- |
| T0 pedido de restart enviado | 17:15:26,513947 |
| T1 primeira leitura retida com o novo pod | aquisição 17:15:33,054599–17:15:33,284019 |
| T2 Ready=True no lastTransitionTime Kubernetes | 17:15:32 |
| T3 primeira resposta API correspondente | aquisição 17:20:39,238389–17:20:39,418985 |
| T4 snapshot correspondente ID 26 | created_at 17:20:34,434307 |
| Último snapshot comprovadamente contendo o pod antigo neste conjunto | ID 22, created_at 17:10:33,875010 |

T1 é a primeira observação retida, não a criação exata. As condições incluem PodScheduled 17:15:26, Initialized 17:15:31 e Ready 17:15:32. A precisão dessas condições é de segundos. Antes do sucesso houve um poll pós-restart ainda com snapshot 22 e 58 polls insuficientes referindo snapshot 24 (17:15:33,875827). Não se possui conteúdo suficiente desses polls para afirmar que snapshot 24 continha o pod antigo.

O limite observado restart→primeira resposta correspondente é **312,905 s**. Entre os brackets da primeira referência e da resposta API: **305,954–306,364 s**. Esse segundo intervalo mede as observações, não é um limite garantido desde a criação real (anterior à primeira leitura). O pod antigo e seu `runs_on` aparecem no snapshot antes e estão ausentes no snapshot depois. `last_seen` antigo é 17:10:17,114417; não é um campo de término de validade e não deve ser apresentado como tal. O instante exato de encerramento não foi estabelecido.

O código fixado define refresh estrutural padrão de 5 min. O intervalo e a sucessão de snapshots são compatíveis com espera pelo refresh, mas estes registros não provam configuração efetiva, causalidade dominante ou qual atualização por kubelet ocorreu primeiro. A redação deve apresentar um limite observado e essa compatibilidade, não “latência exata de ingestão de 306 s”.

### E5 — consumo descritivo a partir de séries retidas

`recursos.csv` contém média, máximo, unidade, entidade, pod, nó, contagem e primeira/última amostra; inclui os diagnósticos com flag separada. Dez amostras por série dentro de cada janela, separadas por cerca de 30 s, cobrem aproximadamente 270 s entre a primeira e a última. São médias das amostras e máximos observados, não consumo integrado exato ou máximo contínuo de 300 s. Não se inferiu zero para série ausente.

B2/B3/B6 possuem os três agentes e gateway. B1 só tem um agente e não tem gateway na extração; B4 não tem agentes/gateway; B5 tem dois agentes. A disponibilidade do inventário não corrige a ausência de séries. A tabela abaixo exclui `diag-*` e watchdog da avaliação, preservados no CSV. Agentes permanecem separados por pod/nó. Memória apresentada em MiB, CPU em cores.

| Baseline | Componente | Pod | Nó | CPU média / máximo (cores) | Memória média / máximo (MiB) |
| --- | --- | --- | --- | --- | --- |
| B1 | agent | karthos-agent-lfcgh | None | 0.00398 / 0.00856 | 67.67 / 72.85 |
| B1 | backend | karthos-backend-7688c6f4f4-rqphc | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.05986 / 0.09668 | 15.69 / 17.01 |
| B1 | frontend | karthos-frontend-5d6ffff59b-c2f4h | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.00007 / 0.00010 | 4.82 / 4.82 |
| B1 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.02509 / 0.05706 | 93.04 / 106.08 |
| B1 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.02095 / 0.02547 | 67.30 / 78.16 |
| B2 | agent | karthos-agent-5q9qw | None | 0.00977 / 0.01467 | 73.44 / 78.86 |
| B2 | agent | karthos-agent-cc79m | None | 0.01806 / 0.02058 | 74.17 / 80.22 |
| B2 | agent | karthos-agent-w5wjp | None | 0.00459 / 0.01000 | 65.21 / 72.91 |
| B2 | backend | karthos-backend-77954d44c4-6ncgl | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.08222 / 0.13978 | 18.02 / 19.46 |
| B2 | frontend | karthos-frontend-5d6ffff59b-b5gfb | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.00006 / 0.00009 | 6.47 / 6.47 |
| B2 | gateway | karthos-gateway-6fb9f7b7b6-58897 | None | 0.02601 / 0.03128 | 106.01 / 135.08 |
| B2 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.02834 / 0.04289 | 166.68 / 174.17 |
| B2 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.02857 / 0.03892 | 75.39 / 82.55 |
| B3 | agent | karthos-agent-cbj9s | None | 0.01963 / 0.02576 | 131.22 / 144.16 |
| B3 | agent | karthos-agent-pk6lc | None | 0.02461 / 0.02972 | 126.99 / 140.08 |
| B3 | agent | karthos-agent-w66ln | None | 0.00809 / 0.01640 | 83.26 / 88.93 |
| B3 | backend | karthos-backend-7787b47d7b-vdjck | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.13241 / 0.19292 | 17.82 / 19.01 |
| B3 | frontend | karthos-frontend-5d6ffff59b-r8dg8 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.00008 / 0.00015 | 4.75 / 4.75 |
| B3 | gateway | karthos-gateway-7454ccf6cd-wmm2n | None | 0.03408 / 0.03745 | 122.50 / 155.41 |
| B3 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.04940 / 0.08761 | 185.27 / 189.58 |
| B3 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.04954 / 0.06858 | 101.71 / 114.90 |
| B4 | backend | karthos-backend-c55c67b96-h9dtg | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.07599 / 0.10157 | 17.48 / 19.53 |
| B4 | frontend | karthos-frontend-5d6ffff59b-dd44h | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.00005 / 0.00008 | 6.48 / 6.48 |
| B4 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.02454 / 0.02953 | 169.17 / 172.50 |
| B4 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0.03425 / 0.04000 | 87.75 / 99.65 |
| B5 | agent | karthos-agent-7h5hz | None | 0.01515 / 0.01857 | 120.59 / 134.82 |
| B5 | agent | karthos-agent-zsvmm | None | 0.00511 / 0.00770 | 75.56 / 82.89 |
| B5 | backend | karthos-backend-6bd76cfd5b-zpcjr | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.06535 / 0.08302 | 15.60 / 17.13 |
| B5 | frontend | karthos-frontend-5d6ffff59b-kl7bt | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.00004 / 0.00006 | 4.78 / 4.78 |
| B5 | gateway | karthos-gateway-586d58fc74-2ghh6 | None | 0.01650 / 0.01887 | 109.02 / 132.43 |
| B5 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.02151 / 0.02715 | 114.44 / 115.99 |
| B5 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.03502 / 0.04023 | 83.19 / 94.05 |
| B6 | agent | karthos-agent-62pc5 | None | 0.00358 / 0.00438 | 66.49 / 69.32 |
| B6 | agent | karthos-agent-b2vm7 | None | 0.00322 / 0.00530 | 66.73 / 70.37 |
| B6 | agent | karthos-agent-pt2ng | None | 0.00853 / 0.01102 | 71.29 / 74.49 |
| B6 | backend | karthos-backend-55f877dccf-5xpc2 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.05335 / 0.09280 | 14.20 / 16.42 |
| B6 | frontend | karthos-frontend-5d6ffff59b-888th | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.00006 / 0.00009 | 5.66 / 5.66 |
| B6 | gateway | karthos-gateway-596c88f9cc-9l2rj | None | 0.01332 / 0.01603 | 94.81 / 115.80 |
| B6 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.01900 / 0.02892 | 148.49 / 149.38 |
| B6 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0.01752 / 0.02122 | 68.40 / 75.16 |

Não há afirmação causal de sobrecarga nem comparação “com/sem Karthos”. O catálogo e o pipeline são usados para descrever o próprio sistema, com cobertura limitada declarada.

### E6 — chamadas internas e cinco requisições adicionais

Em cada baseline há **360 registros checkout PlaceOrder sem identidade de jornada**, sendo 300 gRPC status 0 e 60 status 13, todos HTTP 200. Boutique usa `/hipstershop.CheckoutService/PlaceOrder`, `grpc-go/1.82.0`; Astronomy usa `/oteldemo.CheckoutService/PlaceOrder`, `grpc-node-js/1.14.4`. Nos traces, IP de origem resolve para o pod frontend. Na mesh o observador vê `127.0.0.6`; esse endereço de proxy sozinho não autentica a origem frontend. A rota, população e spans pareados sustentam a interpretação de chamadas internas, sem transformar o IP de proxy em identificação do chamador.

B2 e B3 têm **mais cinco** requests frontend `/`, `Go-http-client/1.1`. Em B2 a origem resolve para `otel-collector-agent-dzgvr`; em B3 é novamente o proxy local. Não há evidência suficiente aqui para denominá-los probes Kubernetes ou provar a finalidade do cliente. Permanecem tráfego adicional observado, desconhecido quanto à finalidade. Portanto, 1925 = 1560 de jornada + 360 checkout + 5 adicionais em Astronomy; Boutique: 1920 = 1560 + 360. A arbitragem tem 384 = 312 + 72. Nenhuma contagem foi subtraída e a classificação selada continua intacta.

### Como reproduzir e localizar a evidência

- `acquisition.json`: 26 objetos com URI, geração, tamanho, hash e destino; sete arquivos brutos permanecem no Kivel em `/home/kivel/projects/karthos-evaluation-authorization-20261119/exploratory/inputs/`.
- `raw-derived/B1.json`…`B6.json`, `arbitration.json`: série completa retida de erros, timestamps de eventos, expressões, spans pareados, inventários, agrupamentos de tráfego, recursos e recibo de extração com hashes de cada arquivo selecionado.
- `raw-derived/replay.json`: imagem por digest, hash de entrada, consulta exata e resposta do replay; os seis containers foram removidos e o inventário posterior não encontrou nomes `karthos-exploratory-replay-*`.
- `raw-derived/durations.json`: endpoints de quietude, valores sum/count por partição e cálculo da média.
- `raw-derived/history.json`: pedido de restart, três referências, 61 registros API (inclui before), snapshots/arestas relevantes.
- `raw-derived/host-60s.json` e `host-60s.csv`: 45 comparações descritivas de médias, sem veredito.
- `raw-derived/final-verification.json`: 18 membros do histórico verificados contra seu selo original e ausência dos containers de replay.
- `raw-derived/collector-configs.json`: configurações montadas retidas, incluindo intervalos e connector.
- `recursos.csv`: resultados completos de CPU/memória, incluindo diagnósticos identificados.
- `scripts/fetch-evidence.py`, `stage-archive.py`, `analyze-raw.py`, `replay-vm.py`, `host-history.py`: aquisição, verificação e cálculos; caminhos remotos são explícitos. Ordem: aquisição → extração → analyze-raw → replay-vm e host-history → cópia dos derivados → build-report → validate.
- `scripts/build-report.py`: gera este relatório e CSVs a partir dos derivados locais. `scripts/validate.py` confere consistência e os hashes do relatório original. `SHA256SUMS` sela esta pasta separadamente.

O relatório principal e seu índice original continuam sendo a fonte dos vereditos do estudo. Este suplemento pode ser citado como análise exploratória pós-execução; não deve ser descrito como hipótese ou critério predefinido.

<!-- END EXPLORATORY REPORT BODY -->

## Parte III — texto para a discussão da avaliação e a conclusão

As subseções a seguir oferecem redação diretamente adaptável à tese. A discussão distingue os resultados obtidos sob o protocolo congelado das análises exploratórias realizadas posteriormente. Nenhuma dessas interpretações altera os vereditos das Partes I e II.

### Discussão da avaliação — alcance das evidências

A avaliação fornece evidências favoráveis à fidelidade do modelo e à arquitetura do Karthos no escopo dos cenários examinados. A campanha principal compreendeu 14 janelas com evidência válida, concluídas em 14 tentativas, sem repetição por falha operacional. A matriz selada registra 24 afirmações aprovadas e uma não aplicável, incluindo verificações locais e experimentais. Esses números descrevem a execução e o atendimento aos critérios declarados; não representam 24 experimentos independentes nem demonstram precisão para todas as métricas disponíveis.

Nas seis linhas de base, as entidades e relações estruturais declaradas pelo protocolo concordaram com inventários adquiridos separadamente da API Kubernetes. As comparações numéricas selecionadas também apoiaram a preservação e a normalização de tráfego, recursos de containers e volumes, respeitando as populações, unidades e tolerâncias especificadas. A paridade entre armazenamento e API demonstra consistência das transformações e consultas nessa fronteira. Contudo, essa paridade não constitui uma medição física independente nem garante que a população temporal de uma consulta corresponda à população delimitada pelo gerador de carga.

Os cenários de seleção e arbitragem apoiaram o comportamento previsto para as fontes disponíveis e para sua contribuição às métricas normalizadas. Esse resultado deve ser limitado às configurações e populações verificadas: não demonstra failover automático em tempo de execução, ausência universal de contagem dupla ou impossibilidade de perdas e duplicações compensatórias. A proveniência preservada permite distinguir as fontes e interpretar as comparações sem tratar sinais heterogêneos como medições necessariamente equivalentes.

As perguntas exercitadas pelo consumidor existente foram respondidas por interfaces públicas, apoiando a adequação do contrato da API aos casos de uso examinados. As consultas históricas também representaram corretamente os estados verificados de réplicas, posicionamento e saúde antes e depois da substituição controlada de um pod. Essas observações apoiam o consumo do modelo por ferramentas externas, mas não validam todos os consumidores possíveis nem a sequência completa de estados intermediários do ciclo de vida.

A reprodução do Boutique com traces e com Istio em duas implantações de identidades distintas, mantendo configurações científicas compatíveis, fornece evidência de reprodução delimitada do procedimento. O resultado não equivale a uma estimativa estatística de variabilidade nem permite generalizar o comportamento para outros provedores, versões ou regimes de carga.

### Discussão da avaliação — análise exploratória pós-execução

Após a campanha, realizou-se uma investigação exploratória sobre seis questões levantadas pela leitura dos resultados. Essa investigação reutilizou os registros preservados e não introduziu novas medições na nuvem, novos critérios de aprovação ou alterações no produto. Seus resultados complementam a interpretação da avaliação, mas não constituem testes confirmatórios predefinidos nem uma replicação experimental independente.

O replay das consultas reproduziu exatamente as contagens da API nas seis linhas de base. Para os 60 erros de referência em cada janela, foram obtidos 56, 56, 59, 57, 58 e 56 erros, respectivamente; para as 360 requisições de checkout, as consultas de janela fixa retornaram 339, 338, 354, 345, 350 e 336. As séries de erros já existiam antes do início das janelas e os incrementos restantes apareceram em amostras posteriores ao encerramento. Essa evidência sustenta uma explicação baseada no corte da borda final e no atraso de representação dos eventos nos contadores, em vez da hipótese de nascimento da série dentro da janela. Como os contadores são agregados, não se estabeleceu uma correspondência individual entre todos os eventos e os incrementos. A explicação não autoriza ampliar arbitrariamente a janela nem substituir os vereditos originais por novas aprovações.

A investigação de duração mostrou que as linhas de base exclusivamente baseadas em traces não continham sidecars Istio, ao contrário das linhas de base mesh. Na mesma população assentada de 72 chamadas da janela de arbitragem, a duração média foi de 40,30127 ms nos spans de servidor e de 42,38194 ms no histograma Istio. A diferença simultânea de aproximadamente 2,08 ms é menor que as diferenças entre execuções com configurações distintas. Isso esclarece a interpretação das fontes, mas não isola causalmente o custo da mesh nem permite atribuir toda a diferença residual ao proxy ou à fila. As durações devem, portanto, ser apresentadas com sua fonte, ponto de medição e população.

No histórico, a primeira resposta da API correspondente ao pod novo foi observada até 312,905 s após o envio do pedido de reinício. Esse valor é um limite observado nessa execução, não uma garantia geral de atualização nem a latência interna exata de ingestão. O pod antigo e sua relação de posicionamento estavam ausentes no snapshot final, mas o instante exato de encerramento da relação não foi estabelecido. A proximidade com o intervalo padrão de atualização estrutural é compatível com a hipótese de espera pelo processamento periódico, sem demonstrar sua contribuição causal exclusiva ou dominante.

As 45 comparações exploratórias de médias em blocos de 60 s, realizadas em uma linha de base, abrangeram somente dimensões delimitadas de CPU, memória e rede. O código examinado preserva o timestamp OTLP fornecido pelo produtor; assim, não se sustenta uma alegação genérica de descarte do timestamp da fonte pelo Karthos. A preservação desse timestamp, entretanto, não resolve diferenças de intervalo de medição, aquisição e alinhamento entre leitores. As comparações exploratórias não tornam conclusivas as métricas de host ou de ciclo de vida originalmente inconclusivas.

As estatísticas descritivas dos componentes utilizam dez amostras por série dentro de cada janela, com aproximadamente 270 s entre a primeira e a última. Foram mantidas as lacunas de cobertura por componente e a separação dos diagnósticos do avaliador. As médias e os máximos observados não representam consumo integrado exato, pico contínuo ou sobrecarga causal do Karthos. Na análise de tráfego, foram identificados 360 registros de chamadas internas de checkout sem identidade de jornada em cada linha de base. As duas execuções Astronomy contêm ainda cinco requisições frontend adicionais cuja finalidade não foi demonstrada. Esses registros não foram subtraídos da população nem reclassificados como sondas no resultado selado.

### Discussão da avaliação — ameaças à validade e limitações

A validade de construção é limitada pelas dependências compartilhadas entre o produto e as referências. Leituras separadas podem depender da mesma API Kubernetes, do mesmo Kubelet/cAdvisor, kernel, produtor Envoy ou instrumentação de traces. A concordância observada deve ser interpretada conforme a fronteira efetivamente verificada: inventário, produtor, armazenamento ou API. A existência de uma referência separada, por si só, não estabelece independência física da medição.

A cobertura numérica também é delimitada. Das 43 métricas do catálogo, 18 foram selecionadas para tentativa de verificação numérica. Seleção e comparação tentada não equivalem a precisão demonstrada. As oito métricas de host e as três de ciclo de vida permanecem inconclusivas, e os diagnósticos de janela fixa preservam seus resultados específicos. As tolerâncias são critérios operacionais com pisos absolutos, não intervalos de confiança ou garantias metrológicas universais.

Quanto à validade interna, a comparação de contadores depende de cobertura, estabilidade, drenagem e compatibilidade das populações. A concordância agregada não exclui perdas e duplicações compensatórias. Além disso, produto e avaliador foram desenvolvidos pelo mesmo autor; os testes negativos locais reduzem o risco de aceitação indevida, mas não substituem uma auditoria independente. As análises exploratórias foram definidas depois da observação dos resultados e devem ser identificadas como tal, inclusive quando fornecem explicações coerentes para as discrepâncias encontradas.

A generalização é restrita a duas aplicações, carga fixa de duas jornadas por segundo, um provedor, uma versão observada de Kubernetes e Istio em modo sidecar. Não foram avaliados degraus de escala, custo causal com e sem Karthos, superioridade quantitativa em relação ao Kiali ou novos consumidores. As numerosas comparações de amostras correlacionadas não representam repetições experimentais independentes. Por isso, não se afirma superioridade estatística nem desempenho universal em outros ambientes.

A validade temporal é igualmente limitada. O orçamento da fase histórica era de 720 s, mas a fase completa durou aproximadamente 502 s, incluindo etapas operacionais, com cerca de 341 s associados à observação e ao encerramento antes da publicação. Esses intervalos não demonstram comportamento durante retenção de 7–30 dias. Tampouco permitem reconstruir todas as transições intermediárias ou estabelecer limites gerais de latência. As estatísticas de recursos têm cobertura amostral parcial; ausência de série não significa consumo zero.

### Conclusão — contribuição demonstrada e trabalhos futuros

Este trabalho apresentou e avaliou o Karthos como uma camada normalizada de dados de observabilidade para Kubernetes. No escopo das aplicações, implantações e configurações examinadas, os resultados apoiam a fidelidade das entidades e relações estruturais declaradas, a preservação e normalização de sinais selecionados, a interpretação por proveniência e o consumo do modelo por meio de APIs públicas. A reprodução do procedimento em duas implantações distintas acrescenta evidência de viabilidade da arquitetura nas condições avaliadas.

A contribuição demonstrada consiste em disponibilizar uma base de dados e interfaces cujo comportamento pode ser relacionado a fontes, identidades, unidades e populações explícitas. Essa conclusão deve acompanhar os limites da avaliação: não foi estabelecida precisão para todo o catálogo, e as verificações exatas de host e ciclo de vida permanecem inconclusivas. As análises posteriores esclareceram discrepâncias de janela, diferenças entre fontes e limites de observação histórica, sem modificar os critérios ou os resultados selados da campanha.

Como trabalhos futuros, destacam-se a verificação de alinhamento temporal e de fórmulas por dimensão nas referências ainda inconclusivas, a caracterização controlada do atraso de atualização estrutural e a observação prolongada do histórico e da retenção. A avaliação em outros provedores, versões e cargas poderá examinar a generalização dos resultados. Estudos específicos poderão investigar escalabilidade, custo causal e comportamento em cenários adicionais de arbitragem, sem presumir que essas propriedades já foram demonstradas. Ferramentas de análise, automação, simulação e predição poderão explorar o contrato de dados como consumidores futuros, cuja adequação requer avaliação própria.

Conclui-se que as evidências sustentam a viabilidade e a fidelidade do modelo e da arquitetura propostos nos cenários avaliados. O alcance dessa conclusão reside nas propriedades e populações verificadas, com limitações explícitas e resultados inconclusivos preservados.

### Nota editorial e de preservação

O texto das subseções de discussão e conclusão pode ser adaptado à numeração da tese, mantendo os denominadores, as unidades e a distinção entre avaliação sob protocolo congelado e análise exploratória. A comparação com Kiali permanece qualitativa. Os recibos de backup, encerramento e orçamento da Parte I são os registros originais da campanha, sem nova alegação sobre o estado atual do provedor. A remoção dos containers locais de replay pertence à análise exploratória. Nenhum arquivo LaTeX foi alterado.

## Rastreabilidade da revisão

[INDICE-REVISAO.md](INDICE-REVISAO.md) conecta a revisão aos resultados estruturados, dados exploratórios, scripts e recibos originais. [REVISAO-EXPLORATORIA.json](REVISAO-EXPLORATORIA.json) registra hashes dos documentos de origem, os arquivos verificados e a política de preservação. `SHA256SUMS-REVISAO` sela os arquivos novos separadamente, sem substituir os dois selos preexistentes.
