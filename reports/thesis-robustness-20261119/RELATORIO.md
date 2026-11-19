# Relatório de apoio à tese: robustecimento da avaliação

Revisão de 19/11/2026. Produto preservado em `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`. Runtime do complemento congelado em `6e9304e`; revisão offline congelada antes das medições em `7d9190e`; reanálise `accuracy-evidence-v3.5.1`. Nenhum LaTeX foi editado.

## Resultado e escopo

O estudo original contabilizou 14 janelas lógicas e 18 tentativas: 13 janelas com evidência requerida válida e uma baseline Boutique/Istio do segundo cluster inválida após seu retry. Isso é contagem de execução/evidência, não uma taxa de precisão. Esta reanálise usa seis baselines e duas janelas de arbitragem, verificadas por hashes dos arquivos e dos membros consumidos. Os resultados e tentativas originais permanecem intactos.

Nas cinco baselines válidas, as verificações delimitadas de estrutura, contagem, erros, duração assentada, CPU/memória de containers e volumes são aprovadas. A sexta baseline permanece inconclusiva para essas afirmações devido à invalidade da aquisição, mesmo quando cálculos diagnósticos concordam. Host e alinhamento exato de ciclo de vida permanecem inconclusivos. Não há conclusão universal de precisão das 43 métricas.

A execução complementar `boutique-complement-20261119-v2` falhou na preparação do controlador: o teste real de escrita na quarentena recebeu HTTP 403. As quatro janelas ficaram não executadas (zero tentativas de janela). As permissões condicionais por run não haviam sido aplicadas antes do acionamento: falha operacional de preparação, não discordância numérica nem reprovação do Karthos. A comparação entre implantações permanece inconclusiva. O registro desta execução não substitui o segundo cluster original. `complement-execution.json` preserva o resultado separado.

## Resultados por baseline

A tabela mostra a validade geral da aquisição e os resultados **condicionados à validade**, não apenas a saída aritmética. `results.json` preserva também famílias originais, diagnósticos, valores, erros, limites, afirmações estruturais e perguntas da API.

| Baseline | Evidência requerida | Estrutura delimitada | Contagem/erros/duração assentada | Container/volume | Host/ciclo de vida exato |
|---|---|---|---|---|---|
| Cluster 1 — Boutique/traces | Válida | Aprovada | Aprovada | Aprovada | Inconclusiva |
| Cluster 1 — Astronomy/traces | Válida | Aprovada | Aprovada | Aprovada | Inconclusiva |
| Cluster 1 — Astronomy/Istio | Válida | Aprovada | Aprovada | Aprovada | Inconclusiva |
| Cluster 1 — Boutique/Istio | Válida | Aprovada | Aprovada | Aprovada | Inconclusiva |
| Cluster 2 original — Boutique/Istio | Inválida | Inconclusiva | Inconclusiva | Inconclusiva | Inconclusiva |
| Cluster 2 original — Boutique/traces | Válida | Aprovada | Aprovada | Aprovada | Inconclusiva |

Para cada uma das famílias primárias da tabela: seis baselines previstas, cinco avaliáveis/aprovadas, zero reprovadas e uma inconclusiva. Para host e alinhamento exato de ciclo de vida: seis previstas, zero avaliáveis e seis inconclusivas. Esses denominadores não são amostras independentes nem porcentagens de perda de telemetria. As 18 tentativas históricas estão em `results.json`; os motivos completos permanecem nas evidências originais indexadas.

## Recuperação da guarda temporal

Na Boutique/traces do primeiro cluster, a guarda que exigia um timestamp comum rejeitava partições separadas por 1 ms. A correção usa fronteiras por partição e, quando necessário, verifica as próprias observações quietas do histograma. Exige estabilidade, unidades, identidade, epochs cumulativos, ausência de resets/conflitos/lacunas, mesma fronteira fonte/armazenamento e prova independente da população. Não aumenta a tolerância temporal nem procura endpoints que produzam concordância.

A reprodução com o código original verificado voltou a rejeitar a comparação com `quiet partitions have different measurement endpoints`; o recibo está em `original-guard-replay-frozen.json`.

A população recuperada contém 360 chamadas e 60 erros em ambos os lados. A duração média dos spans de servidor pareados é 16,3856493102 ms; a fonte nativa resulta em 16,3856534111 ms. Erro absoluto: 0,0000041009 ms; relativo: aproximadamente 0,0000250%. O limite aplicado é 5 ms (`max(5 ms, 20%)`). Fonte e armazenamento diferem apenas pelo arredondamento de ponto flutuante. Isso valida a população assentada delimitada; os resultados inconclusivos da janela fixa original não são apagados.

## Estrutura, proveniência e contagem dupla

Os inventários independentes da API Kubernetes foram confrontados com serviços, pods, containers, `contains` e `runs_on`. Os oito conjuntos possuem correspondência nesse escopo. A baseline inválida mantém esse resultado como diagnóstico, sem promoção da afirmação agregada. Não se verificaram todos os tipos de entidade; aquisições de inventário e grafo são sequenciais, e o produto também depende da API Kubernetes.

No cenário `both-sources`, a proveniência/configuração seleciona Istio e os totais independentes de checkout concordam. A afirmação agregada de ausência de soma das duas fontes é aprovada para esse cenário. Selecionar uma fonte isoladamente não seria prova suficiente. Perda compensada por duplicação continua possível em uma análise apenas agregada.

## Tráfego de fundo observado

| Baseline | Requisições dos observadores | Identidade de jornada | Sondas identificadas | Não classificadas |
|---|---:|---:|---:|---:|
| Cluster 1 — Boutique/traces | 1920 | 1560 | 0 | 360 |
| Cluster 1 — Astronomy/traces | 1925 | 1560 | 0 | 365 |
| Cluster 1 — Astronomy/Istio | 1925 | 1560 | 0 | 365 |
| Cluster 1 — Boutique/Istio | 1920 | 1560 | 0 | 360 |
| Cluster 2 original — Boutique/Istio | 1920 | 1560 | 0 | 360 |
| Cluster 2 original — Boutique/traces | 1920 | 1560 | 0 | 360 |

As contagens acima são requisições registradas pelos observadores durante `[início,fim)`, não número de jornadas nem chamadas totais do cluster. Zero sondas identificadas significa ausência de atribuição pelos registros disponíveis; não prova ausência de sondas. Os registros históricos não permitem atribuir automaticamente todos os acessos desconhecidos. Nenhuma contagem é inferida pela configuração ou subtraída dos totais. O avaliador está preparado para preservar user-agent no log e permitir atribuição por rota declarada mais `kube-probe/`, mantendo desconhecidos; a complementação interrompida não produziu esses registros.

## CPU e memória descritivas

Exemplo com as baselines válidas de Boutique do primeiro cluster: última avaliação retida na API para containers identificados em `karthos-system`, agrupados pelo nome do container. CPU em millicores; memória em MiB. Não são médias da janela, picos, medidas de overhead causal ou estimativas de capacidade. Os auxiliares de avaliação estão explicitamente separados. Os seis snapshots completos e hashes das entradas estão em `component-footprint.json`.

| Container/componente | Quantidade mesh/traces | CPU mesh (mCPU) | Memória mesh (MiB) | CPU traces (mCPU) | Memória traces (MiB) |
|---|---:|---:|---:|---:|---:|
| backend | 1/1 | 85.528 | 14.29 | 67.159 | 15.64 |
| frontend | 1/1 | 0.085 | 4.75 | 0.056 | 4.80 |
| postgresql | 1/1 | 22.293 | 164.41 | 25.017 | 83.25 |
| vmsingle | 1/1 | 48.667 | 91.16 | 26.304 | 84.70 |
| watchdog | 1/1 | 0.005 | 16.45 | 0.004 | 14.61 |

## Seleção das métricas e tolerâncias

As 18 métricas foram escolhidas pelas referências disponíveis e pelas semânticas comparáveis: três de tráfego (contagem, erros, duração), duas de containers (CPU/memória), duas de volume (disponível/capacidade), oito de host (CPU, memória, disco leitura/escrita, rede recebida/transmitida, filesystem uso/utilização) e três de ciclo de vida (prontidão, reinícios, fase do pod). As outras 25 recebem verificações de contrato, proveniência e visibilidade. Seleção e comparação tentada não significam precisão demonstrada: host e alinhamento de ciclo de vida continuam limitados por timestamps de kernel/cache ausentes.

Os critérios originais permanecem: requisições `max(2,15%)`; duração `max(5 ms,20%)`; CPU `max(10 mCPU,20%)`; memória `max(1 MiB,20%)`, conforme o contrato aplicável. São limites operacionais previamente usados para fontes assíncronas, condicionados às guardas de população/identidade, e não intervalos estatísticos de confiança. Erro absoluto sempre deve acompanhar a tolerância; relativo é indefinido quando a referência é zero. Nenhum limite foi alargado para aprovar esta reanálise.

## Referências e consumo pela API

O [protocolo de robustecimento](../../docs/thesis-robustness.md) contém a matriz hipótese → afirmação → referência → comparação → evidência → limitação. Kubernetes fornece o inventário; kubelet/cAdvisor e entradas delimitadas do receptor sustentam comparações de recursos; métricas nativas Istio ou OTLP do connector permitem verificar preservação; observadores/spans pareados sustentam a população de checkout. A independência da leitura não elimina dependências comuns do produtor ou da instrumentação.

As cinco perguntas existentes permanecem o teste de consumo pelo contrato público. Nas baselines válidas, há respostas corretas para réplicas/posicionamento atuais, chamadores no intervalo, derivação de erros e histórico de recursos de containers. Retenção de réplicas/saúde é uma afirmação do experimento temporal dedicado, não demonstrada pelas baselines isoladamente. As respostas e condições por pergunta estão em `results.json`; reconstrução offline de consulta nunca deve ser citada como consulta executada.

## Orientações para capítulos 7 e 8

No capítulo de avaliação, separar validade da execução, fidelidade de transformação, concordância com referência independente, fidelidade na janela fixa e completude assentada. Usar denominadores por baseline/família e manter tentativas malsucedidas. Apresentar a complementação em tabela separada, com hashes científicos compatíveis, identidades distintas e a mesma revisão do avaliador aplicada aos dois conjuntos.

Nas ameaças e limitações: um provedor, Kubernetes observado `1.34.12-gke.1011000`, Istio sidecar, amostra delimitada e referências com dependências compartilhadas. A carga de duas jornadas/s em aplicações realistas valida as afirmações delimitadas; não sustenta escala. Não há hipótese causal de custo/sobrecarga. Latência evento–API não foi avaliada. Observação temporal de 720 s não demonstra comportamento durante retenção de 7–30 dias. A comparação com Kiali permanece qualitativa. Não adicionar novo consumidor nem rollout de Deployment.

## Reprodução e integridade

`evidence-index.json` registra arquivos originais, URIs, tamanhos, hashes e saídas de reanálise. Os arquivos brutos permanecem nos destinos originais; o índice não transforma destinos privados em públicos. A análise lê arquivos selados e exige diretório novo. Usar o commit fixo do avaliador, o produto acima, digests das imagens e os manifestos; DOI não é requisito.

Para regenerar os dados de apoio após recuperar as entradas com os hashes do índice:

```sh
python -B -m evaluation.gke.thesis_support \
  --analysis-root <diretorio-da-reanalise-selada> \
  --claims-subdir claims-final \
  --original-summary <final-summary-verificado> \
  --output <novo-diretorio>
```

Os testes locais cobrem baselines aprovadas/reprovadas/ausentes/inválidas, guarda temporal e suas rejeições, relações incorretas, contagem duplicada, fonte indevida, referências ausentes, sondas/desconhecidos e o perfil de quatro janelas com retry/encerramento. Suíte congelada: 1.458 testes, quatro skips; revisão adicional verificou negativamente epochs, resets e divergência fonte/armazenamento. Testes locais são evidência do avaliador, não substituem a execução complementar.

## Preparação complementar congelada

Manifesto: `1a298604983f541dad876dfb3ad85e382ad556d72c58d11bf5dbbcbf6fdaa5a4`. Imagem do controlador: `sha256:cbd867f9828895888f305006513e2fdd900fdd4fc46da66d561a3bc9ca36ace6`. Os hashes científicos coincidem com os do estudo original; a mudança de controlador e as diferenças operacionais são registradas separadamente.

Cotação de venda do dólar: R$ 5,8102 (BCB, fechamento de 18/11/2026), com margem de 10%, totalizando R$ 6,39122/USD. Previsão bruta: US$ 85,63, aproximadamente R$ 547; reserva adicional de encerramento: R$ 150. Prazo de infraestrutura: 12 h, com a última hora reservada para preservação/limpeza. R$ 800 não interrompe a execução; o teto adicional total é R$ 1.200. O orçamento inclui hipóteses conservadoras de discos, rede/NAT, logs, registro, armazenamento por 90 dias e transferências; não desconta créditos. Esses valores são previsão, não faturamento final observado.

Fontes de preparação: [BCB/PTAX](https://ptax.bcb.gov.br/ptax_internet/consultarUltimaCotacaoDolar.do), [preços Compute Engine](https://cloud.google.com/products/compute/pricing/general-purpose), [preços GKE](https://cloud.google.com/kubernetes-engine/pricing). `rule-freeze.json` distingue a correção retrospectiva do estudo original do congelamento anterior às medições complementares.

A reanálise histórica foi preservada em duas cópias privadas com leitura de retorno e SHA-256 conferidos. `publication-original.json` registra os objetos e gerações; digest do pacote: `2d1b60d8649dd7aa24c4dc5d9278188d7909cb67c152dac01eaf6f2852f41690`. O pacote registra a revisão anterior ao acionamento do controlador; o relatório da complementação interrompida foi preservado separadamente nas duas cópias. `publication-complement.json` registra leitura de retorno, gerações e hashes; ambos os selos validaram os dez membros declarados.

## Desfecho operacional da complementação

Um novo cluster foi efetivamente criado, com identidade distinta das duas implantações originais. A compatibilidade dos insumos científicos foi verificada antes do lançamento; `deployment-input-review.json` detalha a conferência dos arquivos. Sem janelas coletadas, isso não demonstra reprodução dos resultados nem equivalência do software observado nos nós. Não houve repetição automática da experiência.

A ausência do cluster, VM, discos, redes/sub-redes, roteadores, firewalls, watchdog e agendamento foi conferida no provedor em 19/11/2026 às 06:37:23 UTC. O lease também foi liberado. `cleanup-complement.json` registra a prova e a corrida entre o encerramento automático e o local, resolvida sem recriar infraestrutura. Buckets de evidência, registro e estado compartilhado foram preservados. O próximo uso do Terraform deve reconciliar por refresh os controles removidos diretamente. A ausência de resultados permanece explícita nos quatro registros `unattempted`. A falha e o diagnóstico selados estão no índice da publicação complementar. Qualquer nova implantação exige decisão explícita sobre o segundo cluster adicional e deve contabilizar o consumo desta tentativa no mesmo teto de R$ 1.200. O faturamento final ainda não foi observado.
