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
