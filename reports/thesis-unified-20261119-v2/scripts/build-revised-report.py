"""Compose an editorial revision without modifying either sealed source package."""
import hashlib,json,pathlib,re
P=pathlib.Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(root,seal):
 rows=[]
 for line in (root/seal).read_text().splitlines():
  expected,name=line.split(maxsplit=1);name=name.lstrip('*')
  assert digest(root/name)==expected,name
  rows.append(name)
 return rows
original=verify(P,'SHA256SUMS');exploratory=verify(P/'exploratory','SHA256SUMS')
a=(P/'RELATORIO.md').read_text().split('\n',1)[1].lstrip()
b=(P/'exploratory/INVESTIGACAO.md').read_text().split('\n',1)[1].lstrip()
def demote(s):return re.sub(r'^(#{2,5}) ',r'\1# ',s,flags=re.M)
intro='''# Avaliação unificada do Karthos — relatório final revisado para a tese

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
'''
bridge='''
<!-- END ORIGINAL REPORT BODY -->

## Parte II — análise exploratória posterior à execução

Esta parte explica questões levantadas após a leitura dos resultados. Métodos, cálculos e interpretações abaixo são pós-execução. Os números desta parte não entram nos denominadores de aprovação da Parte I. Em particular, as médias de recursos ampliam o extrato originalmente apresentado apenas como snapshots; a classificação exploratória de rotas não substitui a classificação selada de tráfego.

<!-- BEGIN EXPLORATORY REPORT BODY -->
'''
closing='''
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
'''
report=intro+demote(a)+bridge+demote(b)+closing
(P/'RELATORIO-REVISADO.md').write_text(report)
# Round-trip validation prevents accidental rewriting of original evidence prose/tables.
for start,end,source in [('<!-- BEGIN ORIGINAL REPORT BODY -->\n','\n<!-- END ORIGINAL REPORT BODY -->',a),('<!-- BEGIN EXPLORATORY REPORT BODY -->\n','\n<!-- END EXPLORATORY REPORT BODY -->',b)]:
 part=report.split(start,1)[1].split(end,1)[0]
 assert re.sub(r'^(#{3,6}) ',lambda m:m[1][1:]+' ',part,flags=re.M)==source
(P/'INDICE-REVISAO.md').write_text('''# Índice da revisão para a tese

A revisão editorial mantém separados os resultados congelados e a análise exploratória. Nenhum arquivo dos dois selos de origem é substituído.

| Arquivo | Uso |
|---|---|
| [RELATORIO-REVISADO.md](RELATORIO-REVISADO.md) | Documento consolidado para redação: resultados originais, E1–E6, discussão, limitações e conclusão prontas para adaptação à tese |
| [RELATORIO.md](RELATORIO.md) | Relatório original selado, preservado |
| [RESULTADOS.md](RESULTADOS.md), [resultados.json](resultados.json), [familias.csv](familias.csv) | Valores, denominadores e vereditos originais |
| [INDICE-EVIDENCIAS.md](INDICE-EVIDENCIAS.md), [evidence-index.json](evidence-index.json) | Localização, hashes e referências da campanha |
| [Investigação E1–E6](exploratory/INVESTIGACAO.md) | Documento exploratório de origem, preservado |
| [Aquisição](exploratory/acquisition.json) | 26 objetos: URI, geração e hash |
| [Replay](exploratory/raw-derived/replay.json) | Consultas exatas e respostas das seis baselines |
| [Durações](exploratory/raw-derived/durations.json) | Populações, endpoints, sum/count e spans |
| [Histórico](exploratory/raw-derived/history.json) | Pedido, polls, referências e relações |
| [Host](exploratory/host-60s.csv) | 45 comparações descritivas sem veredito |
| [Recursos](exploratory/recursos.csv) | Médias/máximos por entidade, cobertura e diagnósticos |
| [B1](exploratory/raw-derived/B1.json), [B2](exploratory/raw-derived/B2.json), [B3](exploratory/raw-derived/B3.json), [B4](exploratory/raw-derived/B4.json), [B5](exploratory/raw-derived/B5.json), [B6](exploratory/raw-derived/B6.json) | Séries de erros, spans, inventários, grupos de tráfego e recibos de extração |
| [Backup](evidence/backup-receipt.json), [limpeza](evidence/cleanup-receipt.json), [readback](evidence/closure-readback.json) | Encerramento original da campanha, sem nova alegação sobre estado atual |
| [Verificação exploratória](exploratory/raw-derived/final-verification.json) | Selo do histórico e remoção dos containers de replay |
| [Manifesto desta revisão](REVISAO-EXPLORATORIA.json) | Hashes de origem e verificações de preservação |
| [Gerador](scripts/build-revised-report.py) | Reproduz esta revisão e valida as duas fontes seladas |

Para verificar, executar `shasum -a256 -c SHA256SUMS`, `shasum -a256 -c SHA256SUMS-REVISAO` nesta pasta e `shasum -a256 -c SHA256SUMS` dentro de `exploratory/`. Os selos têm escopos separados.
''')
meta={'kind':'editorial-revision-with-post-hoc-exploratory-analysis','campaign':'thesis-robustness-full-20261119-v2','sealed_verdicts_modified':False,'new_experiments':False,'source_documents':{str(f.relative_to(P)):digest(f) for f in [P/'RELATORIO.md',P/'SHA256SUMS',P/'exploratory/INVESTIGACAO.md',P/'exploratory/SHA256SUMS']},'original_sealed_files_verified':original,'exploratory_sealed_files_verified':exploratory,'validation':{'original_body_roundtrip':'passed','exploratory_body_roundtrip':'passed','original_seal':'passed','exploratory_seal':'passed'},'report':'RELATORIO-REVISADO.md'}
(P/'REVISAO-EXPLORATORIA.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
new=['RELATORIO-REVISADO.md','INDICE-REVISAO.md','REVISAO-EXPLORATORIA.json','scripts/build-revised-report.py']
(P/'SHA256SUMS-REVISAO').write_text(''.join(digest(P/n)+'  '+n+'\n' for n in new))
verify(P,'SHA256SUMS-REVISAO')
print(f'Verified {len(original)} original files, {len(exploratory)} exploratory files, {len(new)} revision files; both source bodies round-trip unchanged.')
