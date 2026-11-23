# E1–E6 — investigação exploratória parcial

Estado: análise pós-execução, sem alteração de vereditos selados. Fontes já examinadas: seis resumos locais com hashes verificados, relatório final de execução, código Karthos no commit `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`, função do avaliador que nomeia famílias e contratos upstream versionados. [local-findings.json](local-findings.json) retém os valores, referências e hashes de código; [script](scripts/analyze-local.py) reproduz a extração local. Não houve nova medição, execução de cluster ou replay do VictoriaMetrics.

A inspeção dos JSONL da análise já presente no Kivel confirmou o esquema real: `*.references.jsonl` contém registros normalizados de referência (por exemplo `metric`, `value`, `measurement_timestamp`, `acquisition_started_at`, `acquisition_ended_at`, dimensões e UID); `*.reference_sets.jsonl` contém referências compactas com offset/bytes/hash; `*.comparisons.jsonl` contém comparações e identidades. Não se pode presumir que `references.jsonl` seja o ledger do gerador nem que uma lista deduplicada de referências contenha todos os eventos de tráfego.

## E1 — contagens de janela fixa

| Baseline | Erros de referência | Erros API | Requests API na janela fixa | Requests de referência |
|---|---:|---:|---:|---:|
| B1 | 60 | 56 | 339 | 360 |
| B2 | 60 | 56 | 338 | 360 |
| B3 | 60 | 59 | 354 | 360 |
| B4 | 60 | 57 | 345 | 360 |
| B5 | 60 | 58 | 350 | 360 |
| B6 | 60 | 56 | 336 | 360 |

Os requests foram efetivamente calculados. Os totais acima somam todas as partições dos exemplos retidos de `boundary_summary.boundaries[boundary=storage-to-api]`, com `omitted_examples=0`; não são estimativas. Cada partição tem paridade API/armazenamento. Não se confundem esses totais com deltas de séries delimitados por amostras anteriores/posteriores, que usam outras fronteiras.

Em `evaluation/gke/report_summary.py:family_summaries`, traces passam a usar a população assentada como família primária e preservam a antiga como `fixed_window_traffic_count/error`. Em Istio, contagem/erros já permanecem nas famílias `traffic_count/error` de janela fixa; não são emitidos aliases redundantes. A família de duração fixa permanece separada para ambas as fontes. A ausência do alias na tabela não significa ausência de cálculo.

O código `metric_store.go:exactWindowIncrease` usa `increase` para séries cumulativas, com guardas de pelo menos duas amostras e extensão histórica de pelo menos uma janela. Para delta usa `sum_over_time`. Seletores incluem fonte, identidades da aresta e temporality; há também a guarda de freshness. Para Istio, erros são uma derivação do contador de requests por status gRPC/HTTP (`pkg/sources/istio/query_view.go`); para traces, o contador é o de falhas nativo do connector. O replay da consulta completa capturada ainda não foi realizado.

A implementação versionada de [MetricsQL 1.138.0](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.138.0/app/vmselect/promql/rollup.go) trata `increase` de forma diferente de `increase_prometheus` e pode considerar uma amostra anterior à janela. Portanto, não se deve aplicar automaticamente a hipótese de que o primeiro valor é sempre descartado.

Há priming documentado antes das janelas. Em B1 ele ocorreu entre 13:43:29 e 13:43:59 UTC; a janela começa às 13:45:18,782 UTC. O bracket retido do contador de erros começa em 13:45:06,271 UTC, antes da janela. Isso já contraria a hipótese específica de que essa série só nasceu no primeiro erro da janela B1. O bracket não prova o instante de nascimento da série. Atribuir os 4 erros faltantes aos primeiros ou últimos eventos ainda depende da série completa e do ledger. Não se recomenda ampliar a janela, somar o primeiro valor como zero ou mudar o produto sem essa verificação: essas ações podem incluir eventos de outra população.

## E2 — duração traces versus Istio

`pkg/sources/servicegraph/raw_metrics.go` mapeia `traces_service_graph_request_server` para `servicegraph.request.duration`. O histograma de cliente não é o escolhido para essa métrica. O [contrato do connector 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/README.md) e sua [implementação](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/connector.go) separam histogramas de cliente e servidor. Os buckets controlam a distribuição, não selecionam se o relógio é do cliente ou do servidor. Karthos configura flush de 15 s, TTL de pareamento de 30 s e normaliza a duração de servidor; confirmar a configuração montada por célula exige a evidência de implantação.

A comparação na mesma janela de arbitragem, a presença de sidecars e as médias de spans cliente/servidor ainda não foram calculadas. Mesmo se spans e Istio concordarem na arbitragem, isso não isola causalmente o custo da mesh entre execuções diferentes. Mesmo se ambas as células tiverem sidecars, isso não torna automaticamente uma comparação de latências entre execuções causalmente controlada. Uma diferença entre relógios também não identifica, sozinha, quanto corresponde a fila/proxy.

## E3 — timestamps e médias de 60 s

A hipótese “Karthos não preserva o timestamp da fonte” é ampla demais: `pkg/backend/ingest/normalizer.go` converte `dp.TimeUnixNano` em `MetricSample.Timestamp`; não substitui indiscriminadamente pelo instante de ingestão. A precisão persistida e os timestamps de avaliação da API são outra questão. O [CPU scraper 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/cpu_scraper.go) usa um timestamp de scrape e calcula utilização entre leituras; esse timestamp não é uma prova do instante físico de cada leitura do kernel/cache.

As razões retidas são específicas: intervalo de CPU/estado incompleto ou não alinhado; ausência de intervalo/instante subjacente para contadores de rede/disco; timestamp de gauge ambíguo; fórmulas/dimensões de memória não verificadas; alinhamento do filesystem indisponível. O leitor de referência `/proc` registra brackets de aquisição e deixa `measurement_timestamp=None`. Essas razões não demonstram que o backend descartou um timestamp físico fornecido pelo produtor.

Médias por 60 s e a sequência de transições ainda dependem das séries e referências completas. Não se calcula a média de contadores cumulativos como se fosse uma taxa de rede; é necessário derivar incrementos por interface, preservar identidade/boot e escolher uma população dimensional comum. Uma futura média exploratória deve declarar o uso de timestamps de aquisição, cobertura e ausência de veredito, sem converter a inconclusão selada em aprovação.

## E4 — instantes já demonstrados

Todos os horários abaixo são UTC em 19/11/2026; Brasília = UTC−3.

| Marco | Evidência retida |
|---|---|
| T0: envio do pedido de restart | 17:15:26,513947 |
| Aquisição independente usada para a referência | 17:15:33,054599–17:15:33,284019 |
| T3: primeira aquisição API correspondente | 17:20:39,238389–17:20:39,418985 |
| T4: `created_at` do snapshot correspondente, ID 26 | 17:20:34,434307 |
| T1 e T2 separados: primeira aparição e primeiro Ready | Não estabelecidos separadamente por esse resumo |
| T5 e encerramento de `runs_on` antigo | Não estabelecidos por esse resumo |

O intervalo referência→API registrado é **305,954–306,364 s**. Do envio do pedido de restart até a conclusão da primeira aquisição correspondente decorreram **312,905 s**. Há 60 polls, dos quais 58 não produziram correspondência disponível. Estes são intervalos de observação do controlador, não latência interna de ingestão. O instante da criação do snapshot não equivale ao instante em que o cliente o recebeu.

O código fixado realmente usa refresh estrutural padrão de **5 min** em `cmd/backend/main.go`. O intervalo é compatível com a hipótese de refresh periódico, mas não confirma a causa dominante: falta verificar configuração efetiva, logs do refresh, aquisições intermediárias e eventual caminho de atualização por kubelet. Não atribuir causalmente todos os 306 s ao timer apenas pela proximidade.

## E5 — consumo médio dos componentes

Os snapshots disponíveis continuam válidos como snapshots, não como médias ou máximos de 300 s. Não foram convertidos em médias. Para o cálculo solicitado ainda são necessárias séries completas por identidade de container/nó; agentes devem permanecer separados por nó e `diag-*` deve ficar separado. O próprio armazenamento do Karthos é uma fonte de descrição da implantação instrumentada, não uma medição causal de overhead.

## E6 — tráfego sem classificação

Confirmado: B1/B4/B5/B6 possuem 360 registros não classificados; B2/B3 possuem 365. A igualdade com o total de checkout é indício, não atribuição. Rota, origem e user-agent ainda precisam ser agrupados nos logs. Não se reclassificaram esses registros como checkout ou sondas e não se subtraiu nenhum deles.

## Pendência de acesso

A revisão automática de aprovação rejeitou a transferência dos arquivos brutos do GCS para o Kivel porque a autorização para esse destino não ficou estabelecida. Foi solicitada aprovação explícita. Até a resposta, a investigação permanece limitada às evidências já disponíveis, sem transferência substitutiva ou contorno da rejeição. Os cálculos dependentes não foram executados e não há conclusão causal fechada para E1/E2/E4.
