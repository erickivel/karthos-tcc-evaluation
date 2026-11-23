"""Build Portuguese exploratory report from derived data; no sealed-file writes."""
import json,pathlib,csv,statistics,datetime,hashlib
P=pathlib.Path(__file__).resolve().parents[1];D=P/'raw-derived'
def read(p):return json.loads(p.read_text())
def utc(t):return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def csvfile(name,rows):
 with (P/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
baselines=[read(D/f'B{i}.json') for i in range(1,7)];replay=read(D/'replay.json');dur=read(D/'durations.json');host=read(D/'host-60s.json');history=read(D/'history.json')
resources=[dict(baseline=d['label'],**x) for d in baselines for x in d['E5']];csvfile('recursos.csv',resources);csvfile('host-60s.csv',host['rows'])
errors=[]
for d in baselines:
 s=d['E1']['error_series'][0];errors.append([d['label'],utc(d['E1']['window'][0]),utc(s['first_retained_sample'][0]),s['first_retained_sample'][1],s['simple_pre_to_last_delta'],s['errors_completed_after_last_sample'],s['completed_errors_not_yet_in_counter_at_last_sample'],utc(s['first_sample_reaching_all_errors'][0])])
req=[next(x['checkout_total'] for x in r['queries'] if x['query']['api_file']=='api-request-count.json') for r in replay['results']]
hr=[]
for metric in sorted({r['metric'] for r in host['rows']}):
 vals=[r['relative_error_percent'] for r in host['rows'] if r['metric']==metric and r['relative_error_percent'] is not None];hr.append([metric,len(vals),f'{min(vals):.3f}',f'{statistics.median(vals):.3f}',f'{max(vals):.3f}'])
res=[]
for d in baselines:
 grouped={}
 for x in d['E5']:
  if x['diagnostic']:continue
  grouped.setdefault((x['pod'],x['container'],x['node']),{})[x['metric']]=x
 for (pod,c,node),v in sorted(grouped.items()):
  cpu=v.get('container_cpu_usage');mem=v.get('container_memory_usage');res.append([d['label'],c,pod,node,f"{cpu['mean']:.5f} / {cpu['maximum']:.5f}" if cpu else 'ausente',f"{mem['mean']/2**20:.2f} / {mem['maximum']/2**20:.2f}" if mem else 'ausente'])
text='''# Investigação exploratória E1–E6 — evidências brutas verificadas

Análise pós-execução do estudo `thesis-robustness-full-20261119-v2`. **Não altera qualquer veredito, protocolo congelado ou relatório principal.** Não houve nova medição na nuvem. Após a aprovação explícita do usuário, sete arquivos brutos e 19 objetos do histórico foram transferidos para o Kivel, com geração GCS fixada e SHA-256 verificado. As sete extrações verificaram todos os membros selados. A investigação parcial anterior fica preservada como registro histórico; sua pendência de acesso foi resolvida.

Karthos: `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`. Avaliador congelado: `45f85e0bcdb33365b7ad2311d5c4a1f56a5c7df0`. Os scripts nesta pasta são posteriores ao estudo e não integram suas regras de aprovação. Horários UTC; Brasília = UTC−3.

## E1 — a diferença está na borda final e na visibilidade das amostras

A série de erros **já existia antes da janela nas seis baselines**. Não se demonstrou seu nascimento exato: a tabela mostra a primeira amostra retida. Nenhum reset foi observado. A cadência mediana é 15 s nas seis células. O delta entre a última amostra anterior ao início e a última amostra dentro da janela reproduz os totais da API.

'''+table(['Baseline','Início da janela','Primeira amostra retida','Valor inicial retido','Delta/API','Erros concluídos após última amostra interna','Concluídos antes dela ainda não representados no delta','Primeira amostra posterior com delta 60'],errors)+'''

Nas três células traces, 3 erros terminam depois da última amostra interna e o delta ainda fica uma unidade abaixo dos erros já concluídos naquele instante. Isso mostra também atraso entre conclusão e representação no contador. Nas células mesh, a diferença inteira corresponde ao número de erros concluídos após essa amostra. Os contadores agregados não permitem identificar inequivocamente quais IDs individuais faltam: a localização temporal é demonstrada por contagens e pelo assentamento posterior, não por correspondência evento→incremento.

**Replay executado:** as expressões completas capturadas em `vm-parity/manifest.json` foram executadas sobre o `vm-raw.ndjson` de cada baseline no VictoriaMetrics 1.138.0, com imagem por digest, rede isolada e sem portas publicadas. Os seis pares foram reproduzidos exatamente:

'''+table(['Baseline','Requests referência','Requests replay/API','Erros referência','Erros replay/API'],[[d['label'],360,int(req[i]),60,int(d['E1']['error_series'][0]['simple_pre_to_last_delta'])] for i,d in enumerate(baselines)])+'''

A consulta cumulativa usa `increase(selector[300s])`, `count_over_time >= 2`, extensão histórica de pelo menos 300 s examinada em 600 s, e freshness por `last_over_time`. Para temporality delta há `sum_over_time`. A consulta Istio deriva erros de status gRPC/HTTP; traces usa o contador nativo de falhas. Todas as expressões e respostas estão em `raw-derived/replay.json`; não se substituiu a expressão por uma aproximação PromQL. [A implementação MetricsQL versionada](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.138.0/app/vmselect/promql/rollup.go) diferencia `increase` de `increase_prometheus`.

Os requests de janela fixa foram calculados também em Istio: `report_summary.py:family_summaries` mantém `traffic_count/error` como famílias originais para mesh, sem aliases redundantes. Para traces, preserva aliases `fixed_window_*` quando promove a população assentada à família primária. A marca inconclusiva não significa valor ausente nem precisão demonstrada: o alinhamento/população exigidos pelo protocolo continuam distintos da simples existência da resposta.

**Redação sugerida:** “A janela fixa subestimou os totais do gerador por corte das amostras na borda final e atraso de representação. Nas evidências retidas, os incrementos restantes aparecem após o fim da janela. A paridade armazenamento–API não implica equivalência entre a população temporal do gerador e a janela do contador.” Não somar arbitrariamente a primeira amostra nem ampliar a janela sem controlar a população adicional.

## E2 — comparação simultânea e presença de sidecars

B1, B2 e B6 não tinham `istio-proxy`. B3 tinha 27 pods com sidecar e B4/B5/arbitragem, 12. A verificação inclui `spec.initContainers`, pois os proxies são sidecars nativos com `restartPolicy: Always`; olhar apenas `containers` daria uma resposta incorreta.

O mapeamento fixado em `pkg/sources/servicegraph/raw_metrics.go` seleciona o histograma de **servidor**. Buckets não escolhem o relógio cliente/servidor. A configuração montada retida confirma `metrics_flush_interval: 15s`, store TTL 30 s e `max_items: 10000`. [Contrato](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/README.md) e [implementação 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/connector/servicegraphconnector/connector.go).

Foram deduplicados spans por trace/span ID e pareados spans SERVER checkout com o CLIENT frontend pai. Há 360 pares por baseline e 72 na arbitragem, cuja janela dura 60 s. Os deltas sum/count usam os endpoints de quietude registrados antes/depois; nenhum endpoint foi escolhido por melhorar concordância. A contagem resultante foi conferida contra 360/72. Assim, a comparação cobre as chamadas da janela depois do assentamento, não uma média instantânea de 300 s.

'''+table(['Célula','Pares','Servidor span ms','Cliente span ms','Histograma selecionado ms','Diferença histograma−servidor ms'],[[x['label'],int(x['count']),f"{x['server_span_ms']:.5f}",f"{x['client_span_ms']:.5f}",f"{x['mean_ms']:.5f}",f"{x['mean_ms']-x['server_span_ms']:.5f}"] for x in dur])+'''

Na arbitragem, **40,30127 ms SERVER × 42,38194 ms Istio**, diferença 2,08068 ms (5,16% do SERVER); CLIENT 45,10098 ms. Nas três baselines mesh a diferença simultânea é 1,72–2,32 ms, muito menor que a diferença entre execuções traces/mesh. Isso enfraquece a explicação de que o salto inteiro seria apenas o ponto de medição. Não isola causalmente o custo da mesh: implantações e instantes diferem. Não atribuir a diferença residual inteira a proxy ou fila sem instrumentação específica. A proveniência continua necessária para interpretar o ponto de medição.

## E3 — dimensões limitadas, médias exploratórias, sem aprovação

A normalização preserva `dp.TimeUnixNano` em `MetricSample.Timestamp`; portanto, “Karthos descarta o timestamp da fonte” não é sustentado pelo código examinado. O timestamp do produtor pode representar a coleta, e a referência `/proc` não fornece instante físico: registra um bracket de aquisição. [CPU scraper 0.156.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/cpu_scraper.go). A captura mantém inconclusões por alinhamento/intervalo, fórmulas e dimensões; preservar um timestamp não resolve essas limitações.

Em B1 foram calculados cinco blocos de 60 s para três nós, com dimensões explícitas: CPU **user por core**, memória **free**, recepção de rede **ens4**. Isto não valida CPU total, memória usada nem todas as interfaces. Memória de referência converte kB em bytes. Rede usa deltas/segundo, nunca média do contador cumulativo. CPU usa delta user/soma dos estados, excluindo guest/guest_nice da soma duplicada. O tempo de referência é o ponto médio da aquisição. Intervalos que cruzam bordas não entram nas médias de taxas; não há interpolação. Médias aritméticas de amostras/intervalos não constituem integração temporal exata. Contagens e valores por bloco estão no CSV.

'''+table(['Dimensão','Blocos pareados','Erro relativo mínimo %','Mediana %','Máximo %'],hr)+'''

Os três arquivos de referência mantêm um único UID de nó e boot ID cada (`raw-derived/host-identities.json`). Nenhum veredito foi criado. Essas médias não tornam conclusivas as 11 métricas originais. No histórico, os estados terminais e a substituição de identidade coincidem, mas três leituras Kubernetes e os polls da API não capturam uma sequência completa Pending→Running/ready 0→1. Os containers retidos do pod novo estão Running/Ready, restartCount 0; isso não prova todas as transições intermediárias.

## E4 — linha temporal observada e limites

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

## E5 — consumo descritivo a partir de séries retidas

`recursos.csv` contém média, máximo, unidade, entidade, pod, nó, contagem e primeira/última amostra; inclui os diagnósticos com flag separada. Dez amostras por série dentro de cada janela, separadas por cerca de 30 s, cobrem aproximadamente 270 s entre a primeira e a última. São médias das amostras e máximos observados, não consumo integrado exato ou máximo contínuo de 300 s. Não se inferiu zero para série ausente.

B2/B3/B6 possuem os três agentes e gateway. B1 só tem um agente e não tem gateway na extração; B4 não tem agentes/gateway; B5 tem dois agentes. A disponibilidade do inventário não corrige a ausência de séries. A tabela abaixo exclui `diag-*` e watchdog da avaliação, preservados no CSV. Agentes permanecem separados por pod/nó. Memória apresentada em MiB, CPU em cores.

'''+table(['Baseline','Componente','Pod','Nó','CPU média / máximo (cores)','Memória média / máximo (MiB)'],res)+'''

Não há afirmação causal de sobrecarga nem comparação “com/sem Karthos”. O catálogo e o pipeline são usados para descrever o próprio sistema, com cobertura limitada declarada.

## E6 — chamadas internas e cinco requisições adicionais

Em cada baseline há **360 registros checkout PlaceOrder sem identidade de jornada**, sendo 300 gRPC status 0 e 60 status 13, todos HTTP 200. Boutique usa `/hipstershop.CheckoutService/PlaceOrder`, `grpc-go/1.82.0`; Astronomy usa `/oteldemo.CheckoutService/PlaceOrder`, `grpc-node-js/1.14.4`. Nos traces, IP de origem resolve para o pod frontend. Na mesh o observador vê `127.0.0.6`; esse endereço de proxy sozinho não autentica a origem frontend. A rota, população e spans pareados sustentam a interpretação de chamadas internas, sem transformar o IP de proxy em identificação do chamador.

B2 e B3 têm **mais cinco** requests frontend `/`, `Go-http-client/1.1`. Em B2 a origem resolve para `otel-collector-agent-dzgvr`; em B3 é novamente o proxy local. Não há evidência suficiente aqui para denominá-los probes Kubernetes ou provar a finalidade do cliente. Permanecem tráfego adicional observado, desconhecido quanto à finalidade. Portanto, 1925 = 1560 de jornada + 360 checkout + 5 adicionais em Astronomy; Boutique: 1920 = 1560 + 360. A arbitragem tem 384 = 312 + 72. Nenhuma contagem foi subtraída e a classificação selada continua intacta.

## Como reproduzir e localizar a evidência

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
'''
(P/'INVESTIGACAO.md').write_text(text)
print('report written; resource rows',len(resources),'host rows',len(host['rows']))
