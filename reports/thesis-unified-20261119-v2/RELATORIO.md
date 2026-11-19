# Avaliação unificada do Karthos — relatório final de apoio à tese

Campanha: `thesis-robustness-full-20261119-v2`. Medições realizadas em 19/11/2026. Este relatório consolida as seis linhas de base e os resultados da campanha completa; não altera o LaTeX nem substitui os arquivos experimentais originais.

## Conclusão e alcance

As 14 janelas previstas foram concluídas com evidência válida, em 14 tentativas, sem retry ou falha operacional nesta campanha. A matriz selada contém 24 afirmações aprovadas e uma explicitamente não aplicável. Foram aprovadas a fidelidade estrutural declarada contra inventários Kubernetes, as respostas do consumidor pela API, a concordância numérica representativa, a arbitragem de fontes e a reprodução do Boutique em duas implantações distintas.

Isso **não significa precisão demonstrada para todas as 43 métricas**, nem aprovação de todas as comparações de baixo nível. As verificações exatas de host e ciclo de vida permanecem inconclusivas; diagnósticos em janela fixa também preservam suas inconclusões. Contagens de erros na API diferiram da referência em 1–4 requisições, dentro da tolerância congelada. População assentada, janela fixa e preservação armazenamento–API são resultados separados.

## Papel desta campanha e reprodução

Esta é a campanha principal proposta para a avaliação da tese, com o escopo de 14 janelas autorizado após o plano inicial de quatro janelas complementares. Ela não deve ser descrita como simples continuação de quatro janelas. O estudo de 17/11, sua reanálise retrospectiva e os dois preparativos interrompidos são histórico de desenvolvimento; permanecem preservados e não entram nos denominadores desta campanha. Não se preenchem lacunas novas com resultados favoráveis antigos.

- Produto: `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`.
- Avaliador executado: `45f85e0bcdb33365b7ad2311d5c4a1f56a5c7df0`; protocolo `thesis-completion-v2`, verificador `thesis-completion-v2.1`.
- Manifesto de lançamento SHA-256: `7b858e15c1206c755713675a0f9022e3aa521bf9d8811fb1bd6e331a2bee57c0`.
- Carga: duas jornadas por segundo; linhas de base de 300 s e smoke de 60 s. Não são duas requisições HTTP por segundo.
- Provedor: GKE, Kubernetes observado `v1.34.12-gke.1011000`; Istio em modo sidecar.
- Digests de imagens, congelamento anterior às medições e entradas científicas: [rule-freeze.json](rule-freeze.json). Este relatório e seus scripts formatam resultados; não reavaliam numericamente os dados nem alteram o produto.

A aceitação é por conclusão rastreável, inclusive negativa ou inconclusiva. A regra permitia no máximo um retry operacional por janela e nenhum por discordância numérica; nenhum foi necessário. As correções retrospectivas não são apresentadas como pré-registro do estudo antigo.

## Preparação e identidade das implantações

Os dois preparativos anteriores pararam antes de medições válidas: concessões de quarentena ausentes e autorização RBAC efetiva inadequada. Esses episódios não são ocultados nem contados como reprovação numérica do produto. Ver [incidente de autorização](INCIDENTE-AUTORIZACAO.md) e [relatório do preparativo anterior](../thesis-unified-20261119/).

A campanha atual verificou identidade, acesso à quarentena e autorização de implantação. Os UIDs independentes confirmam dois ciclos de vida distintos:

| Implantação | UID do provedor |
|---|---|
| C1 | `0dcaa265e1a04241b9c73d44655f6edd4513fcfbe49349918d6ea79a0e225287` |
| C2 | `6ced27f4157c476580d9d09dc2d70893e49ea0ba2c50429491b4dffab08ce967` |

Os critérios `fresh-cluster-identities`, `clean-rebuild-boutique-trace` e `clean-rebuild-boutique-mesh` passaram. As comparações verificaram hashes compatíveis de produto, imagens, carga, protocolo, coleta, regras e infraestrutura. Isto demonstra reprodução delimitada nessas duas implantações, não repetibilidade estatística em provedores ou versões diferentes.

## Método e interpretação

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


## Relação com as hipóteses da tese

Os rótulos abaixo descrevem o conteúdo das hipóteses; sua numeração deve seguir a redação existente da tese, sem criar hipóteses depois de observar os resultados.

| Hipótese / propriedade | Afirmação e comparação | Evidência | Limite |
|---|---|---|---|
| Fidelidade do modelo | `gke-structural-fidelity`: inventário Kubernetes independente versus entidades/relações declaradas, 6/6 | Matriz e seis análises seladas | Estrutura declarada e observada; produtor Kubernetes compartilhado |
| Preservação e normalização | Catálogo, fontes, valores/unidades e paridade API–armazenamento; acordo representativo 6/6 | Tabelas numéricas, referências, proveniência e CSVs | Não prova exatidão de host/ciclo de vida ou das 25 métricas não selecionadas |
| Independência da fonte e arbitragem | Boutique e Astronomy com fontes distintas; seleção com ambas disponíveis e replanejamento explícito | `source-independence-*`, `source-arbitration`, `no-double-counting` | Não testa failover automático em runtime; perda e duplicação compensatórias não são excluídas |
| Reprodução entre implantações | Boutique/Istio e Boutique/traces em C1/C2, UIDs distintos e entradas compatíveis | `clean-rebuild-*` e hashes | Uma segunda implantação, sem estimativa estatística de variabilidade |
| Consumo pela API e histórico | Perguntas do consumidor existente, referências independentes, antes/depois do reinício controlado | Respostas em seis baselines e experimento histórico | Cinco perguntas funcionais; desdobramentos de derivação/concordância não são novos consumidores |

As tolerâncias de 15% e 20% são limites operacionais do protocolo, com pisos absolutos para valores pequenos e quantização. Foram mantidas antes desta campanha e não otimizadas para estes resultados. Não há calibração universal, intervalo de confiança ou garantia metrológica associada a esses percentuais. Por isso o relatório apresenta também os erros absolutos e os denominadores. A guarda de 1 ms só admite a comparação quando as populações e fronteiras por série/partição satisfazem estabilidade e cobertura; resets, conflitos, lacunas e obsolescência continuam a impedir aprovação.

## Resultados finais por linha de base

As 14 janelas terminaram com evidência válida em 14 tentativas, sem retry. As seis linhas de base abaixo têm 300 s de carga, precedidas por smoke de 60 s. As duas outras janelas exercitam arbitragem. A verificação histórica é uma fase separada. Oito janelas iniciais pertencem ao cluster C1 e quatro ao C2; as duas de arbitragem pertencem ao C1.

| ID | Aplicação / fonte / implantação | Identificador lógico |
|---|---|---|
| B1 | Boutique / traces / C1 | official-completion-baseline-boutique-trace |
| B2 | Astronomy / traces / C1 | official-completion-baseline-astronomy-trace |
| B3 | Astronomy / Istio / C1 | official-completion-baseline-astronomy-mesh |
| B4 | Boutique / Istio / C1 | official-completion-baseline-boutique-mesh |
| B5 | Boutique / Istio / C2 | official-r2-baseline-boutique-mesh |
| B6 | Boutique / traces / C2 | official-r2-baseline-boutique-trace |

### Afirmações e critérios de aceitação

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

### Famílias numéricas e resultados inconclusivos

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

### Valores, erros e tolerâncias

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

### Métricas selecionadas: cobertura não equivale a precisão

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

### População observada e tráfego de fundo

| Baseline | Requisições observadas | Jornadas identificadas | Sondas identificadas | Não classificado |
|---|---|---|---|---|
| B1 | 1920 | 1560 | 0 | 360 |
| B2 | 1925 | 1560 | 0 | 365 |
| B3 | 1925 | 1560 | 0 | 365 |
| B4 | 1920 | 1560 | 0 | 360 |
| B5 | 1920 | 1560 | 0 | 360 |
| B6 | 1920 | 1560 | 0 | 360 |


Não se inferem sondas pela configuração nem se subtrai tráfego desconhecido. Zero sondas identificadas não significa ausência de sondas. A soma cobre apenas os observadores instrumentados, não todo o cluster.

### CPU e memória: descrição de snapshots existentes

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

## Histórico e dinamismo

O primeiro checkpoint após as oito janelas iniciais registra uma tentativa histórica, resposta correta, exportação verificada e continuação segura. O limite era de 720 s; a fase completa levou 502,085 s, dos quais 341,028 s corresponderam à observação e ao encerramento antes da publicação. Portanto, não se descreve a fase como 720 s contínuos de observação.

A operação preexistente do protocolo solicita um reinício controlado do Deployment de checkout, alterando anotações do template com precondições de UID/resourceVersion, e observa a substituição dos Pods. Não é um novo cenário de atualização de versão ou uma verificação adicional de identidade de ReplicaSet. As respostas históricas de réplicas, posicionamento e saúde coincidiram com os inventários independentes antes/depois. Os registros delimitam o intervalo em que a mudança se tornou observável; não estabelecem latência exata de ingestão ou contribuição causal de cada etapa.

## Ameaças à validade e recomendações de redação

**Validade de construção.** Comparações podem compartilhar API Kubernetes, Kubelet/cAdvisor, kernel, Envoy ou instrumentação de traces. Um leitor separado não é automaticamente uma fonte física independente. Paridade exata entre API e armazenamento verifica transformação e preservação, não outra medição do fenômeno. Preservar proveniência, dimensões e identidades é condição de comparação.

**Validade interna.** As comparações de contadores dependem de cobertura, drenagem e população compatível. A concordância agregada não elimina a possibilidade de perda e duplicação compensatórias. A verificação de arbitragem usa ambas as fontes disponíveis e posterior redescoberta/reimplantação explícita; não demonstra failover automático. O avaliador e o produto têm o mesmo autor; os testes negativos locais reduzem esse risco, mas não equivalem a uma auditoria independente.

**Validade externa e estatística.** São duas aplicações realistas sob carga fixa, um provedor, uma versão observada de Kubernetes e Istio sidecar. Não foram executados degraus de escala, comparação causal com/sem Karthos, latência evento–API, novo consumidor ou comparação quantitativa com Kiali. Milhares de comparações de séries não representam milhares de execuções independentes. Não se afirma superioridade estatística nem precisão universal das métricas.

**Validade temporal.** O limite histórico de 720 s inclui observação, encerramento e exportação; a fase observada durou 502,085 s. Isso não demonstra comportamento durante retenção de 7–30 dias. A observação delimitou a resposta visível após um reinício controlado, sem estabelecer instante exato de transição nem latência interna causal.

**População e seleção.** As 18 métricas selecionadas não têm todas precisão demonstrada. A preservação de tráfego desconhecido e de comparações inconclusivas impede atribuir artificialmente toda a população a jornadas ou sondas. Os snapshots descritivos de CPU/memória têm cobertura parcial e não são estimativa do custo total da solução.

Para os capítulos de avaliação e conclusão, usar “as afirmações avaliáveis do escopo declarado foram apoiadas nos casos observados”, acompanhado dos denominadores e das limitações. Evitar “todas as métricas são precisas”, “não existe contagem dupla em qualquer situação”, “failover automático comprovado” ou “retenção de 30 dias validada”. A comparação com Kiali permanece qualitativa pela tabela conceitual já existente. As tabelas deste relatório podem ser adaptadas ao LaTeX sem alterar as unidades, populações e limites.

## Preservação, encerramento e orçamento

A cópia secundária foi verificada pelo controlador congelado, com SHA-256 dos payloads. O readback independente de metadados confirmou **498 objetos de pesquisa, 1.991.739.650 bytes por cópia**, com gerações fixas, tamanho, MD5 e CRC32C correspondentes. O recibo de replicação é endereçado por conteúdo e idêntico nos dois buckets. Essa conferência local não é uma nova leitura integral dos payloads brutos no Mac. Ver [verificação independente](evidence/evidence-metadata-verification.json) e [recibo de backup](evidence/backup-receipt.json).

O backup cobre os objetos visíveis no inventário de replicação. Recibos de limpeza posteriores não estavam naquela fotografia; são preservados neste pacote com URI, geração e hash em [closure-readback.json](evidence/closure-readback.json). A quarentena e o estado Terraform não integram o prefixo normal de evidência de pesquisa.

O [recibo final de limpeza](evidence/cleanup-receipt.json) confirma ausência dos nove recursos residuais inventariados (controlador, quatro discos, router, firewall interno, subnet e rede), sem erros ou recursos residuais, scheduler pausado e lease liberado. Os clusters foram encerrados antes dessa etapa; seus resultados de encerramento estão em `run-report.json:cleanup.cluster_cleanup`. O relatório de execução foi selado **antes** da limpeza: seus campos `temporary_removal_confirmed: false` e retenção do controlador são históricos, não devem ser reescritos. O recibo posterior é a evidência de encerramento.

A definição inativa do watchdog Cloud Run permanece registrada, com mínimo de instâncias zero; o recibo não verifica ausência de instâncias ativas (`active_instances_verified: false`). Scheduler pausado, buckets de evidência e infraestrutura de preservação não são declarados removidos. Portanto, “ausência de compute/rede inventariados” é a afirmação suportada, não “apagamento de todos os objetos do projeto”.

R$ 800 foi referência, não condição de parada. R$ 1.200 foi o teto adicional, incluindo os dois preparativos e reserva de encerramento. [budget-review.json](budget-review.json) contém câmbio, margem, provisões e previsão; não há fatura consolidada verificada neste pacote, logo não se apresenta a previsão como gasto final observado.

As exportações registram retenção de 90 dias; isso não equivale a publicação permanente. Reprodutibilidade usa commits, digests, manifesto e hashes, sem DOI obrigatório. O pacote está no repositório local; não foi publicado no GitHub. Os arquivos são indexados em [INDICE-EVIDENCIAS.md](INDICE-EVIDENCIAS.md). Não há necessidade de nova execução na nuvem para usar os resultados descritos.
