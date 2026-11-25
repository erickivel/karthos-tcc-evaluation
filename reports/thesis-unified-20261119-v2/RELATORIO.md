# Relatório da avaliação do Karthos

Campanha `thesis-robustness-full-20261119-v2`, executada em 19/11/2026 no Google Kubernetes Engine (GKE).

Este relatório apresenta como o Karthos foi avaliado, quais resultados foram obtidos e quais são os limites desses resultados. Ele está organizado em duas partes. A primeira traz os resultados obtidos com os critérios fixados antes da execução, que são os que definem se cada afirmação foi apoiada ou não. A segunda traz uma análise feita depois da execução, a partir das mesmas evidências, para explicar alguns resultados que ficaram diferentes do que se esperaria à primeira vista. Essa segunda parte não altera nenhum veredito.

## Resumo dos resultados

As 14 janelas previstas terminaram com evidência válida, em 14 tentativas, sem nenhuma repetição por falha operacional. Das 25 afirmações avaliadas, 24 foram apoiadas e uma foi considerada não aplicável. Foram apoiadas a fidelidade da estrutura do modelo em relação aos inventários lidos da API do Kubernetes, as respostas dadas pela API do Karthos às perguntas do consumidor, a concordância numérica das métricas comparadas, a arbitragem entre as fontes e a reprodução da Online Boutique em dois *clusters* criados do zero.

Isso não significa que as 43 métricas do catálogo tiveram a sua precisão demonstrada, nem que todas as comparações de baixo nível foram aprovadas. As comparações exatas das métricas do sistema operacional dos *nodes* e do ciclo de vida dos contêineres e pods ficaram inconclusivas, e o mesmo aconteceu com parte das comparações em janela fixa. As contagens de erros devolvidas pela API diferiram da referência em uma a quatro requisições, dentro do limite fixado. Por isso, a população assentada, a janela fixa e a preservação entre o banco de amostras e a API são apresentadas como resultados separados.

## Parte I: resultados com os critérios fixados antes da execução

### Escopo da campanha e revisões utilizadas

Esta é a campanha principal da avaliação, com 14 janelas. Um estudo anterior, de 17/11, a sua reanálise e dois preparativos interrompidos fazem parte do histórico de desenvolvimento e continuam guardados no repositório, mas não entram em nenhum resultado desta campanha, e nenhuma lacuna desta campanha foi preenchida com resultados anteriores.

- Karthos: *commit* `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`.
- Avaliação executada: *commit* `45f85e0bcdb33365b7ad2311d5c4a1f56a5c7df0`, protocolo `thesis-completion-v2`, verificador `thesis-completion-v2.1`.
- Manifesto de lançamento (SHA-256): `7b858e15c1206c755713675a0f9022e3aa521bf9d8811fb1bd6e331a2bee57c0`.
- Carga: duas jornadas de usuário por segundo (e não duas requisições HTTP por segundo), com linhas de base de 300 s, cada uma precedida por uma janela curta de verificação de 60 s.
- Ambiente: GKE com Kubernetes `v1.34.12-gke.1011000` e Istio em modo *sidecar*.

As imagens (por *digest*) e as entradas da avaliação foram fixadas antes das medições e estão em [rule-freeze.json](rule-freeze.json). Uma janela só era aceita com uma conclusão rastreável, ainda que negativa ou inconclusiva. A regra permitia no máximo uma nova tentativa por janela em caso de falha operacional e nenhuma em caso de discordância numérica, e nenhuma nova tentativa foi necessária.

### Preparação e identidade dos *clusters*

Os dois preparativos anteriores pararam antes de produzir medições válidas, um por falta de acesso à área de quarentena e outro por uma autorização RBAC insuficiente, e não são contados como reprovação do Karthos. Nesta campanha, a identidade, o acesso à quarentena e a autorização de implantação foram verificados antes do início. Os identificadores atribuídos pelo provedor confirmam que os dois *clusters* são distintos:

| *Cluster* | Identificador do provedor |
|---|---|
| C1 | `0dcaa265e1a04241b9c73d44655f6edd4513fcfbe49349918d6ea79a0e225287` |
| C2 | `6ced27f4157c476580d9d09dc2d70893e49ea0ba2c50429491b4dffab08ce967` |

As verificações `fresh-cluster-identities`, `clean-rebuild-boutique-trace` e `clean-rebuild-boutique-mesh` foram aprovadas, comparando os *hashes* do Karthos, das imagens, da carga, do protocolo, da coleta, das regras e da infraestrutura nos dois *clusters*. Com isso, a reprodução fica demonstrada nesses dois *clusters*, mas não há uma estimativa estatística de repetibilidade em outros provedores ou versões.

### Método

A ligação entre cada hipótese, as afirmações, as referências, as comparações, as evidências e as limitações está no [protocolo de robustez](../../docs/thesis-robustness.md). A análise separa os inventários lidos diretamente da API do Kubernetes, a preservação dos valores entre a fonte, o banco de amostras e a API, as referências da população de chamadas ao *checkout* e os componentes que o Karthos e as referências compartilham. Dois grafos produzidos pelo próprio Karthos não substituem um inventário independente, e a escolha de uma única fonte, sozinha, não prova a ausência de contagem dupla.

Cada referência é lida em um ponto diferente do sistema, como mostra a tabela abaixo. Ter um leitor separado, contudo, não elimina a dependência de uma mesma fonte física ou da mesma instrumentação.

| Referência | Onde é lida | O que é compartilhado com o Karthos |
|---|---|---|
| Inventário estrutural e histórico | API do Kubernetes, com identidades, UIDs e inventários guardados | O Karthos também lê a API do Kubernetes, e as leituras são feitas em sequência |
| CPU, memória e volumes dos contêineres | `/stats/summary` do *kubelet*, acessado por `/api/v1/nodes/<node>/proxy/stats/summary`, com a entrada exata do *receiver* registrada à parte | O *kubelet*, o cAdvisor e o seu *cache* são os mesmos, por isso são exigidos identidade, instante de medição e população compatíveis |
| Sistema operacional dos *nodes* | Leitor de referência em cada *node*, que lê o sistema de arquivos do *host* montado em `/hostfs` (`/proc/stat`, `/proc/meminfo`, `/proc/diskstats`, `/proc/net/dev`, *boot ID* e `statvfs` dos pontos de montagem declarados) | O *kernel* e o *host* são os mesmos, e o intervalo de leitura não informa o instante da medição original |
| Métricas do Istio | *Endpoint* `/stats/prometheus` do *sidecar*, na porta 15090, acessado pelo *proxy* da API do Kubernetes | O Envoy que produz as métricas é o mesmo, então a comparação mostra a preservação e a concordância na população declarada |
| Métricas dos *traces* e população pareada | Saída OTLP do *connector* guardada e *spans* de cliente e de servidor do *checkout* capturados | O *connector* e a referência podem compartilhar a instrumentação, por isso o pareamento e a população são verificados à parte |
| Jornadas, respostas e sondas | Registro do gerador de carga, *logs* dos observadores e registros com a identidade da execução | Cobre apenas as requisições observadas, e *logs* ausentes não permitem estimar sondas pela configuração |
| Preservação entre o banco de amostras e a API | Séries guardadas no banco e respostas das rotas públicas da API | Não é outra medição do sistema, mas sim uma verificação do contrato e das transformações |

As comparações por partição mantêm as verificações de estabilidade, cobertura, identidade, épocas, reinícios de contadores, conflitos e lacunas. A correção da diferença de 1 ms, feita antes desta campanha, não aumenta a tolerância de tempo, e as comparações em janela fixa continuam separadas das comparações de população assentada.

Das 43 métricas do catálogo, 18 possuem uma referência ou uma semântica comparável no protocolo: três de chamadas, duas de contêineres, duas de volumes, oito do sistema operacional e três de ciclo de vida. Ter sido selecionada, ou ter tido a comparação tentada, não equivale a ter a precisão demonstrada, e as comparações inconclusivas continuam no denominador correspondente.

Os limites de aprovação são o maior entre 2 chamadas e 15% para as contagens, entre 5 ms e 20% para a duração, entre 10 milinúcleos e 20% para a CPU e entre 1 MiB e 20% para a memória. Esses limites foram fixados antes desta campanha, sem ajuste a partir dos resultados, e são critérios operacionais, e não intervalos de confiança ou garantias metrológicas. Por isso, os resultados trazem também a unidade, a referência, o valor observado, o erro absoluto, o erro relativo (quando a referência não é zero) e o limite aplicado. Na população assentada dos *traces*, as contagens que delimitam a população precisam ser exatamente iguais, e o limite de 15% não permite aceitar uma população diferente. A duração só é comparada depois dessas verificações, e a tolerância de 1 ms só é aplicada quando a população e as fronteiras de cada série são estáveis e cobertas.

As sondas só são contadas quando aparecem identificadas nos registros observados. O tráfego das jornadas, as sondas identificadas e o tráfego desconhecido são mantidos separados, e o tráfego desconhecido não é subtraído. Assim, nenhuma sonda identificada não quer dizer que não houve sondas.

### Relação com as hipóteses do trabalho

| Hipótese ou propriedade | Afirmação e comparação | Evidência | Limite |
|---|---|---|---|
| Fidelidade do modelo | `gke-structural-fidelity`: inventário independente do Kubernetes comparado com as entidades e relações declaradas, 6 de 6 | Matriz de afirmações e as seis análises | Estrutura declarada e observada, com o Kubernetes compartilhado |
| Preservação e normalização | Catálogo, fontes, valores, unidades e paridade entre o banco de amostras e a API, com concordância nas 6 linhas de base | Tabelas numéricas, referências, proveniência e CSVs | Não prova a exatidão do sistema operacional e do ciclo de vida nem das 25 métricas não selecionadas |
| Independência de fonte e arbitragem | Online Boutique e OpenTelemetry Demo com fontes diferentes, escolha de fonte com as duas disponíveis e nova descoberta explícita | `source-independence-*`, `source-arbitration`, `no-double-counting` | Não testa a troca automática de fonte durante a execução, e perdas e duplicações que se compensem não são excluídas |
| Reprodução entre *clusters* | Online Boutique com Istio e com *traces* em C1 e C2, com identificadores distintos e entradas compatíveis | `clean-rebuild-*` e *hashes* | Um segundo *cluster*, sem estimativa estatística de variabilidade |
| Consumo pela API e histórico | Perguntas do consumidor, referências independentes, antes e depois do reinício controlado | Respostas nas seis linhas de base e verificação do histórico | Cinco perguntas sobre um serviço |

### Resultados por linha de base

As seis linhas de base têm 300 s de carga, cada uma precedida por uma janela de 60 s. As outras duas janelas avaliam a arbitragem, e o histórico é verificado em uma fase separada. Oito janelas pertencem ao *cluster* C1, incluindo as duas de arbitragem, e quatro ao C2.

| Linha de base | Aplicação, fonte e *cluster* | Identificador |
|---|---|---|
| B1 | Online Boutique, *traces*, C1 | official-completion-baseline-boutique-trace |
| B2 | OpenTelemetry Demo, *traces*, C1 | official-completion-baseline-astronomy-trace |
| B3 | OpenTelemetry Demo, Istio, C1 | official-completion-baseline-astronomy-mesh |
| B4 | Online Boutique, Istio, C1 | official-completion-baseline-boutique-mesh |
| B5 | Online Boutique, Istio, C2 | official-r2-baseline-boutique-mesh |
| B6 | Online Boutique, *traces*, C2 | official-r2-baseline-boutique-trace |

Nos identificadores, `astronomy` se refere ao OpenTelemetry Demo, cuja loja se chama Astronomy Shop.

#### Afirmações

A matriz final tem 24 afirmações apoiadas e uma não aplicável, a `host-lifecycle-exact-alignment`. Esses números incluem as verificações do *cluster* local e não correspondem a 24 experimentos independentes. As colunas de contagem se referem às linhas de base, e as afirmações locais, de histórico e de comparação entre *clusters* têm outro denominador.

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

#### Famílias de comparações

A tabela mostra, para cada família, as comparações aprovadas, reprovadas e inconclusivas (A/R/I). Uma comparação incompleta é contada como inconclusiva, e o travessão indica que a família não existe para aquela fonte. Os milhares de pontos das famílias de volumes, do sistema operacional e do ciclo de vida são amostras correlacionadas, e não repetições independentes, por isso as famílias não são somadas em uma taxa global. O campo `numerical_agreement`, mantido por compatibilidade, continua `insufficient`, já que a afirmação `representative-numerical-agreement` cobre apenas chamadas, contêineres e volumes. Todas as famílias estão em [familias.csv](familias.csv).

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

#### Valores, erros e limites

O erro absoluto é a diferença, em módulo, entre o valor observado e a referência, e o erro relativo é esse erro dividido pela referência, apresentado como fração. Os valores estão arredondados só para apresentação, e o CSV [resultados-numericos.csv](resultados-numericos.csv) mantém a precisão completa. As contagens se referem às chamadas ao *checkout*, e não a todas as requisições das jornadas. Como parte das jornadas paga com um cartão vencido, os 60 erros da referência são esperados, e não falhas do avaliador nem defeitos do Karthos. Nenhuma janela registrou resultados inesperados da aplicação. Nas linhas de base com *traces*, o limite da população é zero, ou seja, as contagens precisam ser iguais. As durações de cada fonte são comparadas com a sua própria referência, sem tratar a diferença entre execuções como efeito do Istio.

| Linha de base | Métrica e etapa | Unidade | Referência | Observado | Erro absoluto | Erro relativo | Limite | Resultado |
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
| B3 | duration / fonte → exportação após assentamento | ms | 52,43333333 | 52,43333333 | 0 | 0 | 10,48666667 | aprovada |
| B3 | errors / referência → API na janela fixa | count | 60 | 59 | 1 | 0,01666666667 | 9 | aprovada |
| B3 | container.cpu / primeira comparação registrada | cores | 0,007609812 | 0,007609812 | 0 | 0 | 0,01 | aprovada |
| B3 | container.memory / primeira comparação registrada | bytes | 56860672 | 56860672 | 0 | 0 | 11372134,4 | aprovada |
| B4 | requests / referência de checkout → total selecionado | count | 360 | 360 | 0 | 0 | 54 | aprovada |
| B4 | duration / fonte → armazenamento congelado | ms | 37,18194444 | 37,18194444 | 0 | 0 | 7,436388889 | aprovada |
| B4 | duration / fonte → exportação após assentamento | ms | 37,18194444 | 37,18194444 | 0 | 0 | 7,436388889 | aprovada |
| B4 | errors / referência → API na janela fixa | count | 60 | 57 | 3 | 0,05 | 9 | aprovada |
| B4 | container.cpu / primeira comparação registrada | cores | 0,003220571 | 0,003220571 | 0 | 0 | 0,01 | aprovada |
| B4 | container.memory / primeira comparação registrada | bytes | 8085504 | 8085504 | 0 | 0 | 1617100,8 | aprovada |
| B5 | requests / referência de checkout → total selecionado | count | 360 | 360 | 0 | 0 | 54 | aprovada |
| B5 | duration / fonte → armazenamento congelado | ms | 30,05416667 | 30,05416667 | 0 | 0 | 6,010833333 | aprovada |
| B5 | duration / fonte → exportação após assentamento | ms | 30,05416667 | 30,05416667 | 0 | 0 | 6,010833333 | aprovada |
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

Para a CPU e a memória dos contêineres, a tabela acima traz a primeira comparação registrada de cada métrica, sem escolha pelo resultado. Em todas as comparações avaliáveis dessas métricas, o erro absoluto máximo foi zero:

| Linha de base | Métrica | Comparações previstas | Avaliáveis | Erro absoluto máximo | Unidade |
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

#### Catálogo e métricas selecionadas

Em todas as linhas de base, o catálogo devolvido pela API trouxe as 43 métricas com o nome, a unidade, a fonte e a agregação esperados. Das 43, as 18 da tabela abaixo foram selecionadas para a comparação numérica, por terem uma referência disponível, e não por amostragem estatística, de modo que as 25 restantes não recebem nenhum crédito de precisão. Uma comparação de chamadas em janela fixa pode ficar inconclusiva mesmo quando a população assentada é aprovada na tabela anterior, e a preservação exata entre o banco de amostras e a API não prova a precisão física da medição.

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

#### População observada

| Linha de base | Requisições observadas | Jornadas identificadas | Sondas identificadas | Não classificadas |
|---|---|---|---|---|
| B1 | 1920 | 1560 | 0 | 360 |
| B2 | 1925 | 1560 | 0 | 365 |
| B3 | 1925 | 1560 | 0 | 365 |
| B4 | 1920 | 1560 | 0 | 360 |
| B5 | 1920 | 1560 | 0 | 360 |
| B6 | 1920 | 1560 | 0 | 360 |

Nenhuma sonda foi deduzida a partir da configuração e nenhum tráfego desconhecido foi subtraído. A soma cobre apenas os observadores instalados, e não o *cluster* inteiro. A origem das requisições não classificadas é explicada na Parte II (E6).

### Histórico

O primeiro ponto de controle, depois das oito janelas iniciais, registra a verificação do histórico com resposta correta, exportação verificada e continuação segura. O limite dessa fase era de 720 s, e ela levou 502,085 s no total, dos quais 341,028 s corresponderam à observação e ao encerramento antes da publicação. Ou seja, a fase não foi uma observação contínua de 720 s.

Nessa fase, a avaliação pede ao Kubernetes um reinício controlado do *Deployment* do *checkout*, alterando as anotações do seu *template* com pré-condições de UID e de `resourceVersion`, e observa a substituição dos pods. Não se trata de uma atualização de versão nem de uma verificação adicional da identidade do *ReplicaSet*. As respostas da API sobre as réplicas, o posicionamento e a saúde coincidiram com os inventários independentes antes e depois da substituição. Os registros delimitam o intervalo em que a mudança passou a ser visível, mas não estabelecem a latência exata de ingestão nem quanto cada etapa contribuiu para ela (ver E4).

## Parte II: análise posterior à execução

Depois da leitura dos resultados, seis pontos foram investigados (E1 a E6) a partir das evidências guardadas da campanha. Essa análise não envolveu nenhuma nova medição na nuvem nem mudança no Karthos, e os seus números não entram em nenhum veredito da Parte I. Os dados citados nesta parte ficam na pasta [exploratory/](exploratory/). Os horários estão em UTC (Brasília é UTC−3).

Para essa análise, sete arquivos brutos e 19 objetos do histórico foram copiados do armazenamento da campanha, com a geração de cada objeto fixada e o SHA-256 verificado, e as sete extrações conferiram todos os arquivos selados (`exploratory/raw-derived/final-verification.json`).

### E1: contagem em janela fixa

Quando a API é consultada em uma janela fixa de 300 s, as contagens ficam abaixo da referência. A série de erros já existia antes do início da janela nas seis linhas de base (a tabela mostra a primeira amostra guardada, e não o nascimento da série), nenhum reinício de contador foi observado e a cadência mediana das amostras foi de 15 s nas seis células. A diferença entre a última amostra anterior ao início e a última amostra dentro da janela reproduz os totais devolvidos pela API.

| Linha de base | Início da janela | Primeira amostra guardada | Valor inicial | Delta (API) | Erros concluídos após a última amostra da janela | Erros concluídos antes dela, ainda fora do delta | Primeira amostra posterior com delta 60 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | 2026-11-19T13:45:18.782Z | 2026-11-19T13:44:21.272Z | 2 | 56 | 3 | 1 | 2026-11-19T13:50:36.274Z |
| B2 | 2026-11-19T14:37:20.721Z | 2026-11-19T14:36:22.543Z | 2 | 56 | 3 | 1 | 2026-11-19T14:42:37.544Z |
| B3 | 2026-11-19T15:37:39.013Z | 2026-11-19T15:36:49.294Z | 16 | 59 | 1 | 0 | 2026-11-19T15:42:49.294Z |
| B4 | 2026-11-19T16:45:28.292Z | 2026-11-19T16:44:31.770Z | 16 | 57 | 3 | 0 | 2026-11-19T16:50:31.770Z |
| B5 | 2026-11-19T19:14:39.680Z | 2026-11-19T19:13:47.587Z | 16 | 58 | 2 | 0 | 2026-11-19T19:19:47.587Z |
| B6 | 2026-11-19T20:11:36.388Z | 2026-11-19T20:10:37.553Z | 2 | 56 | 3 | 1 | 2026-11-19T20:16:52.553Z |

Nas três células com o Istio, toda a diferença corresponde aos erros concluídos depois da última amostra da janela. Nas três com *traces*, além desses três erros, o delta ainda fica uma unidade abaixo dos erros já concluídos naquele instante, o que mostra também um atraso entre a conclusão de uma chamada e a sua representação no contador. Como os contadores são agregados, não é possível dizer exatamente quais chamadas faltaram, e a localização no tempo vem das contagens e das amostras posteriores, e não de uma correspondência entre cada chamada e cada incremento.

Para confirmar que a diferença não vinha da tradução da consulta, as consultas da API, capturadas em `vm-parity/manifest.json`, foram executadas novamente sobre a cópia das amostras de cada linha de base (`vm-raw.ndjson`), no VictoriaMetrics 1.138.0, com a imagem fixada por *digest*, em rede isolada e sem portas publicadas. Os seis resultados foram reproduzidos exatamente:

| Linha de base | Requisições (referência) | Requisições (consulta refeita e API) | Erros (referência) | Erros (consulta refeita e API) |
| --- | --- | --- | --- | --- |
| B1 | 360 | 339 | 60 | 56 |
| B2 | 360 | 338 | 60 | 56 |
| B3 | 360 | 354 | 60 | 59 |
| B4 | 360 | 345 | 60 | 57 |
| B5 | 360 | 350 | 60 | 58 |
| B6 | 360 | 336 | 60 | 56 |

A consulta para contadores cumulativos utiliza `increase(selector[300s])`, exige `count_over_time >= 2` e um histórico de pelo menos 300 s, examinado em 600 s, e confere a atualização por `last_over_time`. Para contadores com temporalidade delta, utiliza `sum_over_time`. No Istio, os erros são derivados do *status* gRPC ou HTTP, e nos *traces* é utilizado o contador de falhas do próprio *connector*. Todas as expressões e respostas estão em `exploratory/raw-derived/replay.json`, e a [implementação do MetricsQL na versão utilizada](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.138.0/app/vmselect/promql/rollup.go) mostra a diferença entre `increase` e `increase_prometheus`.

As contagens em janela fixa também foram calculadas para o Istio, em que continuam nas famílias `traffic_count` e `traffic_error`. Nos *traces*, essas contagens aparecem nas famílias `fixed_window_*`, já que a população assentada passa a ser a família principal. Em ambos os casos, inconclusivo não quer dizer valor ausente nem precisão demonstrada.

Portanto, a janela fixa ficou abaixo do gerador de carga porque as amostras do fim da janela ficam de fora e porque há um atraso até as chamadas aparecerem nos contadores, e os incrementos que faltaram aparecem nas amostras posteriores ao fim da janela. A paridade entre o banco de amostras e a API, por sua vez, não implica que a janela do contador tenha a mesma população do gerador. Essa explicação não justifica estender a janela arbitrariamente nem trocar os vereditos originais.

### E2: duração das chamadas e presença dos *sidecars*

As linhas de base B1, B2 e B6 não tinham o `istio-proxy`, enquanto B3 tinha 27 pods com *sidecar* e B4, B5 e a arbitragem, 12. Essa verificação inclui `spec.initContainers`, já que os *proxies* são *sidecars* nativos com `restartPolicy: Always` e não apareceriam olhando apenas `containers`.

No Karthos, o mapeamento fixado em `pkg/sources/servicegraph/raw_metrics.go` seleciona o histograma do lado do servidor. A configuração guardada do *connector* confirma `metrics_flush_interval: 15s`, TTL de 30 s e `max_items: 10000` ([contrato](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/README.md) e [implementação na versão 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/connector.go)).

Os *spans* foram deduplicados pelo identificador do *trace* e do *span*, e cada *span* de servidor do *checkout* foi pareado com o *span* de cliente do *frontend* que o originou. Há 360 pares em cada linha de base e 72 na janela de arbitragem, que dura 60 s. As somas e contagens foram lidas nos pontos de estabilidade registrados antes e depois de cada janela, sem escolher nenhum ponto por melhorar a concordância, e a contagem resultante foi conferida com 360 e 72. Assim, a comparação cobre as chamadas da janela depois que os contadores pararam de mudar, e não uma média instantânea de 300 s.

| Célula | Pares | *Span* de servidor (ms) | *Span* de cliente (ms) | Histograma selecionado (ms) | Histograma − servidor (ms) |
| --- | --- | --- | --- | --- | --- |
| B1 | 360 | 14,24412 | 16,13341 | 14,24412 | 0,00000 |
| B2 | 360 | 31,85264 | 35,39648 | 31,85264 | 0,00000 |
| B3 | 360 | 50,11132 | 56,80964 | 52,43333 | 2,32201 |
| B4 | 360 | 35,07707 | 39,75369 | 37,18194 | 2,10487 |
| B5 | 360 | 28,33212 | 32,07691 | 30,05417 | 1,72204 |
| B6 | 360 | 12,35045 | 14,03343 | 12,35045 | 0,00000 |
| Arbitragem | 72 | 40,30127 | 45,10098 | 42,38194 | 2,08068 |

Na janela de arbitragem, em que as duas fontes mediram as mesmas chamadas, o *span* de servidor deu 40,30127 ms e o Istio, 42,38194 ms, uma diferença de 2,08068 ms (5,16% do valor do servidor), com o *span* de cliente em 45,10098 ms. Nas três linhas de base com o Istio, a diferença entre as duas fontes na mesma célula ficou entre 1,72 e 2,32 ms, muito menor que a diferença entre as células com *traces* e com o Istio. Isso indica que o ponto de medição explica só uma pequena parte dessa diferença. Contudo, como as células foram executadas em momentos e implantações diferentes, a análise não isola o custo da *service mesh*, e a diferença restante não pode ser atribuída ao *proxy* ou a filas sem uma instrumentação específica. Por isso, cada duração continua sendo apresentada junto com a sua fonte.

### E3: métricas do sistema operacional e do ciclo de vida

Na normalização, o Karthos preserva `dp.TimeUnixNano` em `MetricSample.Timestamp`, ou seja, guarda o instante informado pela fonte. Esse instante, contudo, pode ser o da coleta, e a referência lida de `/proc` também não informa o instante físico da medição, apenas o intervalo em que foi lida ([*scraper* de CPU na versão 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/cpu_scraper.go)). Por isso, preservar o instante da fonte não resolve as diferenças de intervalo, de fórmula e de dimensão que deixaram essas comparações inconclusivas.

Para ter uma ideia da proximidade entre as duas leituras, foram calculadas em B1 médias em cinco blocos de 60 s para os três *nodes*, em três dimensões: CPU de usuário por núcleo, memória livre e bytes recebidos na interface `ens4`. Isso não valida a CPU total, a memória utilizada nem as outras interfaces. A memória de referência foi convertida de kB para bytes, a rede foi calculada por diferenças por segundo (e não pela média do contador cumulativo) e a CPU pela razão entre o tempo de usuário e a soma dos estados, sem contar duas vezes `guest` e `guest_nice`. O instante da referência é o ponto médio da leitura, intervalos que cruzam a borda de um bloco não entram nas médias de taxas e não há interpolação. Os valores de cada bloco estão em `exploratory/host-60s.csv`.

| Dimensão | Blocos pareados | Erro relativo mínimo (%) | Mediana (%) | Máximo (%) |
| --- | --- | --- | --- | --- |
| cpu.user.ratio | 15 | 0,034 | 0,872 | 10,555 |
| memory.free.bytes | 15 | 0,006 | 0,052 | 0,176 |
| network.ens4.receive.bytes_per_second | 15 | 0,182 | 2,669 | 14,239 |

Cada um dos três arquivos de referência manteve um único UID de *node* e um único *boot ID* (`exploratory/raw-derived/host-identities.json`). Essas médias indicam que as leituras são próximas, mas não tornam conclusivas as 11 métricas originais, e nenhum veredito foi criado a partir delas. No histórico, os estados finais e a troca de identidade do pod coincidem com a referência, mas as três leituras do Kubernetes e as consultas à API não capturam toda a sequência de estados (de `Pending` a `Running` e de não pronto a pronto). Os contêineres do pod novo estavam `Running` e prontos, com `restartCount` 0, o que não prova todas as transições intermediárias.

### E4: tempo até o pod novo aparecer na API

| Marco | Instante (UTC, 19/11/2026) |
| --- | --- |
| T0: pedido de reinício enviado | 17:15:26,513947 |
| T1: primeira leitura guardada com o pod novo | leitura entre 17:15:33,054599 e 17:15:33,284019 |
| T2: `Ready=True` (`lastTransitionTime` do Kubernetes) | 17:15:32 |
| T3: primeira resposta da API com o pod novo | leitura entre 17:20:39,238389 e 17:20:39,418985 |
| T4: retrato correspondente (ID 26) | criado às 17:20:34,434307 |
| Último retrato que comprovadamente contém o pod antigo | ID 22, criado às 17:10:33,875010 |

T1 é a primeira observação guardada, e não o instante exato de criação do pod. As condições do pod registram `PodScheduled` às 17:15:26, `Initialized` às 17:15:31 e `Ready` às 17:15:32, com precisão de segundos. Antes da resposta correta, houve uma consulta, já depois do reinício, que ainda devolveu o retrato 22, e 58 consultas que devolveram o retrato 24 (criado às 17:15:33,875827) sem o pod novo. Não há conteúdo suficiente dessas consultas para afirmar que o retrato 24 continha o pod antigo.

O tempo entre o pedido de reinício e a primeira resposta da API com o pod novo foi de até 312,905 s. Entre a primeira leitura de referência com o pod novo e essa resposta, foram de 305,954 a 306,364 s, intervalo que mede as observações, e não um limite a partir da criação real do pod, que é anterior à primeira leitura. O pod antigo e a sua relação `runs_on` aparecem no retrato anterior e não aparecem no seguinte. O último `last_seen` do pod antigo é 17:10:17,114417, mas esse campo não indica o fim da validade da relação, e o instante exato em que ela deixou de valer não foi estabelecido.

No código avaliado, a estrutura do *cluster* é lida, por padrão, a cada cinco minutos, e o intervalo observado e a sequência dos retratos são compatíveis com a espera por essa leitura. Esses registros, contudo, não comprovam a configuração efetiva, que essa espera foi a causa dominante nem qual atualização vinda do *kubelet* aconteceu primeiro. Portanto, 312,905 s é um limite observado nesta execução, e não a latência exata de ingestão.

### E5: consumo dos componentes do Karthos

O consumo foi calculado a partir das séries de CPU e de memória guardadas, com dez amostras por série em cada janela, separadas por cerca de 30 s, cobrindo aproximadamente 270 s entre a primeira e a última. Os valores são médias dessas amostras e máximos observados, e não o consumo integrado exato nem o máximo contínuo em 300 s. Uma série ausente não foi tratada como consumo zero. O arquivo [exploratory/recursos.csv](exploratory/recursos.csv) traz, para cada série, a média, o máximo, a unidade, a entidade, o pod, o *node*, o número de amostras e a primeira e a última amostra, inclusive dos componentes de diagnóstico da avaliação, marcados à parte.

B2, B3 e B6 têm as séries dos três agentes e do *gateway*. B1 tem a de um agente e não tem a do *gateway*, B4 não tem a dos agentes nem a do *gateway* e B5 tem a de dois agentes. A tabela abaixo não inclui os componentes de diagnóstico (`diag-*`) nem o *watchdog* da avaliação, e os agentes aparecem separados por pod. A memória está em MiB e a CPU em núcleos.

| Linha de base | Componente | Pod | *Node* | CPU média / máximo (núcleos) | Memória média / máximo (MiB) |
| --- | --- | --- | --- | --- | --- |
| B1 | agent | karthos-agent-lfcgh | — | 0,00398 / 0,00856 | 67,67 / 72,85 |
| B1 | backend | karthos-backend-7688c6f4f4-rqphc | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,05986 / 0,09668 | 15,69 / 17,01 |
| B1 | frontend | karthos-frontend-5d6ffff59b-c2f4h | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,00007 / 0,00010 | 4,82 / 4,82 |
| B1 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,02509 / 0,05706 | 93,04 / 106,08 |
| B1 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,02095 / 0,02547 | 67,30 / 78,16 |
| B2 | agent | karthos-agent-5q9qw | — | 0,00977 / 0,01467 | 73,44 / 78,86 |
| B2 | agent | karthos-agent-cc79m | — | 0,01806 / 0,02058 | 74,17 / 80,22 |
| B2 | agent | karthos-agent-w5wjp | — | 0,00459 / 0,01000 | 65,21 / 72,91 |
| B2 | backend | karthos-backend-77954d44c4-6ncgl | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,08222 / 0,13978 | 18,02 / 19,46 |
| B2 | frontend | karthos-frontend-5d6ffff59b-b5gfb | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,00006 / 0,00009 | 6,47 / 6,47 |
| B2 | gateway | karthos-gateway-6fb9f7b7b6-58897 | — | 0,02601 / 0,03128 | 106,01 / 135,08 |
| B2 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,02834 / 0,04289 | 166,68 / 174,17 |
| B2 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,02857 / 0,03892 | 75,39 / 82,55 |
| B3 | agent | karthos-agent-cbj9s | — | 0,01963 / 0,02576 | 131,22 / 144,16 |
| B3 | agent | karthos-agent-pk6lc | — | 0,02461 / 0,02972 | 126,99 / 140,08 |
| B3 | agent | karthos-agent-w66ln | — | 0,00809 / 0,01640 | 83,26 / 88,93 |
| B3 | backend | karthos-backend-7787b47d7b-vdjck | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,13241 / 0,19292 | 17,82 / 19,01 |
| B3 | frontend | karthos-frontend-5d6ffff59b-r8dg8 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,00008 / 0,00015 | 4,75 / 4,75 |
| B3 | gateway | karthos-gateway-7454ccf6cd-wmm2n | — | 0,03408 / 0,03745 | 122,50 / 155,41 |
| B3 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,04940 / 0,08761 | 185,27 / 189,58 |
| B3 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,04954 / 0,06858 | 101,71 / 114,90 |
| B4 | backend | karthos-backend-c55c67b96-h9dtg | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,07599 / 0,10157 | 17,48 / 19,53 |
| B4 | frontend | karthos-frontend-5d6ffff59b-dd44h | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,00005 / 0,00008 | 6,48 / 6,48 |
| B4 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,02454 / 0,02953 | 169,17 / 172,50 |
| B4 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-9dd7f3b5-s11v | 0,03425 / 0,04000 | 87,75 / 99,65 |
| B5 | agent | karthos-agent-7h5hz | — | 0,01515 / 0,01857 | 120,59 / 134,82 |
| B5 | agent | karthos-agent-zsvmm | — | 0,00511 / 0,00770 | 75,56 / 82,89 |
| B5 | backend | karthos-backend-6bd76cfd5b-zpcjr | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,06535 / 0,08302 | 15,60 / 17,13 |
| B5 | frontend | karthos-frontend-5d6ffff59b-kl7bt | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,00004 / 0,00006 | 4,78 / 4,78 |
| B5 | gateway | karthos-gateway-586d58fc74-2ghh6 | — | 0,01650 / 0,01887 | 109,02 / 132,43 |
| B5 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,02151 / 0,02715 | 114,44 / 115,99 |
| B5 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,03502 / 0,04023 | 83,19 / 94,05 |
| B6 | agent | karthos-agent-62pc5 | — | 0,00358 / 0,00438 | 66,49 / 69,32 |
| B6 | agent | karthos-agent-b2vm7 | — | 0,00322 / 0,00530 | 66,73 / 70,37 |
| B6 | agent | karthos-agent-pt2ng | — | 0,00853 / 0,01102 | 71,29 / 74,49 |
| B6 | backend | karthos-backend-55f877dccf-5xpc2 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,05335 / 0,09280 | 14,20 / 16,42 |
| B6 | frontend | karthos-frontend-5d6ffff59b-888th | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,00006 / 0,00009 | 5,66 / 5,66 |
| B6 | gateway | karthos-gateway-596c88f9cc-9l2rj | — | 0,01332 / 0,01603 | 94,81 / 115,80 |
| B6 | postgresql | karthos-postgresql-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,01900 / 0,02892 | 148,49 / 149,38 |
| B6 | vmsingle | karthos-victoria-metrics-single-server-0 | gke-karthos-eval-1bad98-observability-81ae96d4-6rs6 | 0,01752 / 0,02122 | 68,40 / 75,16 |

Nas séries dos agentes e do *gateway*, o *node* não ficou registrado. O componente `frontend` é o *dashboard* do Karthos. Esses valores descrevem o que os componentes utilizaram nesta execução, e não a sobrecarga que o Karthos causa nas aplicações, já que não houve uma comparação com e sem o Karthos.

### E6: requisições não classificadas

Em cada linha de base há 360 registros de chamadas `PlaceOrder` ao *checkout* sem a identidade de uma jornada, sendo 300 com *status* gRPC 0 e 60 com *status* 13, todos com HTTP 200. Na Online Boutique, a rota é `/hipstershop.CheckoutService/PlaceOrder`, com o cliente `grpc-go/1.82.0`, e no OpenTelemetry Demo é `/oteldemo.CheckoutService/PlaceOrder`, com `grpc-node-js/1.14.4`. Nas células com *traces*, o endereço de origem corresponde ao pod do *frontend*. Nas células com o Istio, o observador vê `127.0.0.6`, endereço do *proxy* que, sozinho, não identifica o *frontend* como origem. São a rota, a população e os *spans* pareados que mostram que essas são as chamadas internas do *frontend* ao *checkout*.

B2 e B3 têm ainda cinco requisições adicionais ao *frontend*, na rota `/`, com o cliente `Go-http-client/1.1`. Em B2, a origem corresponde ao pod `otel-collector-agent-dzgvr`, e em B3, novamente ao *proxy* local. Não há evidência suficiente para dizer que são sondas do Kubernetes nem para provar a finalidade desse cliente, de modo que elas continuam como tráfego observado de finalidade desconhecida. Assim, no OpenTelemetry Demo, as 1925 requisições são 1560 das jornadas, 360 chamadas ao *checkout* e 5 adicionais, e, na Online Boutique, as 1920 são 1560 das jornadas e 360 chamadas ao *checkout*. Na arbitragem, as 384 requisições são 312 das jornadas e 72 chamadas ao *checkout*. Nenhuma contagem foi subtraída, e a classificação original continua a mesma.

## Limitações

**Validade de construção.** As comparações podem compartilhar com o Karthos a API do Kubernetes, o *kubelet* e o cAdvisor, o *kernel*, o Envoy ou a instrumentação dos *traces*, e um leitor separado não é automaticamente uma fonte física independente. A paridade exata entre o banco de amostras e a API verifica a transformação e a preservação dos valores, e não outra medição do mesmo fenômeno. Por isso, a concordância deve ser lida de acordo com a fronteira de fato verificada (inventário, fonte, banco de amostras ou API).

**Validade interna.** As comparações de contadores dependem da cobertura, do assentamento e de uma população compatível, e a concordância de um total não exclui perdas e duplicações que se compensem. A arbitragem foi verificada com as duas fontes disponíveis e com uma nova descoberta e reimplantação explícitas, sem demonstrar uma troca automática de fonte. Além disso, o Karthos e a avaliação foram desenvolvidos pelo mesmo autor, e os testes negativos locais reduzem esse risco, mas não equivalem a uma auditoria independente. As análises da Parte II foram definidas depois de observados os resultados.

**Validade externa e estatística.** A avaliação utilizou duas aplicações sob carga fixa de duas jornadas por segundo, um provedor, uma versão do Kubernetes e o Istio em modo *sidecar*. Não foram avaliados o comportamento em escala, o custo do Karthos em comparação com a sua ausência, a latência entre um evento e a sua chegada à API, outros consumidores nem uma comparação quantitativa com o Kiali ou com outras ferramentas, cuja comparação continua qualitativa. As milhares de comparações de séries são amostras correlacionadas, e não milhares de execuções independentes, de modo que não se afirma superioridade estatística nem precisão universal das métricas.

**Validade temporal.** O limite de 720 s da fase de histórico incluía a observação, o encerramento e a exportação, e a fase observada durou 502,085 s, o que não demonstra o comportamento ao longo dos sete dias de retenção dos retratos nem dos 30 dias das amostras. A observação delimitou o tempo até a resposta visível após um reinício controlado, sem estabelecer o instante exato de cada transição nem a latência interna.

**População e seleção.** Das 43 métricas, 18 foram selecionadas para a comparação numérica, e nem todas tiveram a precisão demonstrada, já que as oito do sistema operacional e as três do ciclo de vida continuam inconclusivas. O tráfego desconhecido e as comparações inconclusivas foram mantidos, sem atribuir toda a população a jornadas ou a sondas. As séries de consumo dos componentes têm cobertura parcial e não estimam o custo total da solução.

## Preservação e encerramento

As evidências da campanha foram exportadas para dois *buckets* do armazenamento da nuvem, com 498 objetos e 1.991.739.650 bytes em cada cópia, e a cópia secundária foi verificada pelo SHA-256, pela geração, pelo tamanho e pelo MD5 e CRC32C de cada objeto. Depois da exportação, os *clusters* e os recursos temporários de computação e de rede foram removidos, como registrado em `evidence/run-report.json` (`cleanup.cluster_cleanup`) e confirmado pelos recibos de limpeza guardados no armazenamento da campanha.

## Reprodução e arquivos

Os resultados podem ser conferidos a partir dos *commits*, dos *digests*, do manifesto e dos *hashes*, sem precisar executar a avaliação novamente na nuvem. As exportações completas ficaram no armazenamento da nuvem, com retenção de 90 dias, e esta pasta guarda os resultados, os resumos das linhas de base e os dados derivados citados neste relatório.

- [resultados.json](resultados.json), [resultados-numericos.csv](resultados-numericos.csv), [familias.csv](familias.csv), [metricas-selecionadas.csv](metricas-selecionadas.csv) e [trafego-observado.csv](trafego-observado.csv): resultados estruturados, valores e erros, famílias de comparações, as 18 métricas por linha de base e a população observada;
- [evidence/claim-matrix.json](evidence/claim-matrix.json), [evidence/run-report.json](evidence/run-report.json) e [evidence/baselines/](evidence/baselines/): a matriz de afirmações, o relatório de execução e os resumos das seis linhas de base, conferidos com o selo original [evidence/GKE-SHA256SUMS](evidence/GKE-SHA256SUMS);
- [rule-freeze.json](rule-freeze.json): revisão executada, manifesto e *digests* das imagens, fixados antes das medições;
- `exploratory/raw-derived/`: dados derivados de cada linha de base (`B1.json` a `B6.json` e `arbitration.json`), a consulta refeita (`replay.json`), as durações (`durations.json`), o histórico (`history.json`, com o pedido de reinício, as três referências, 61 respostas da API e os retratos), as médias do sistema operacional (`host-60s.json`), as identidades dos *nodes* (`host-identities.json`), as configurações do Collector (`collector-configs.json`) e a verificação das extrações (`final-verification.json`);
- [exploratory/recursos.csv](exploratory/recursos.csv) e [exploratory/host-60s.csv](exploratory/host-60s.csv): o consumo dos componentes e as médias do sistema operacional em tabela.

Os arquivos desta pasta são selados por [SHA256SUMS](SHA256SUMS).
