# Robustecimento da avaliação — revisão de 19/11/2026

Este protocolo complementa `thesis-completion-v2`. A finalidade é validar o modelo e a arquitetura do Karthos; escala e custo causal não são hipóteses. Preserva duas jornadas/s, o produto `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`, contratos públicos e testes/consumidor locais existentes.

## Origem e regras

O checkout local antecedia o avaliador efetivamente implantado em `full-bounded-study-20261117`. A implementação desse estudo foi recuperada do servidor de análise, conferida contra os hashes do manifesto congelado e incorporada antes destas correções. Os módulos de finalização delimitada, população assentada e cópia secundária já pertenciam à execução original. Sua incorporação não representa uma nova medição. Resultados originais, tentativas e arquivos brutos não são sobrescritos.

As regras abaixo são **retrospectivas** para o estudo de 17/11. Uma execução complementar deve registrar o commit e os hashes destas regras antes de iniciar medições. Regras, entradas, motivo da revisão e saídas seladas pertencem a diretórios novos. Não se descreve retrospectivamente esta reanálise como pré-registrada.

* Agregação por baseline, afirmação e família: previstas, avaliáveis, aprovadas, reprovadas, inconclusivas e não aplicáveis. Tentativas inválidas permanecem no registro. Evidência ausente não equivale a aprovação nem a contradição avaliável.
* Diferenças de 1 ms: cada partição usa seus timestamps registrados, sem ampliar tolerâncias temporais. Histogramas sem os timestamps exatos do contador usam a última medição anterior a cada uma das duas observações quietas, escolhida por tempo, nunca por concordância. Exigem medições distintas, valores estáveis, frescor de até 60 s já exigido pelo protocolo, fronteiras iguais entre fonte e armazenamento e população independente compatível. Resets, epochs, conflitos, lacunas e unidades continuam verificados. Soma e contagem do histograma precisam compartilhar fronteiras. A janela fixa original é preservada.
* Estrutura: inventários Kubernetes independentes confrontam serviços, pods, containers, `contains` e `runs_on`. Objetos terminais sem inventário temporal são inconclusivos; não eliminam contradições já demonstradas. Não se generaliza esta verificação aos onze tipos de entidade. Inventários e API são aquisições sequenciais, não snapshot atômico.
* Contagem dupla: proveniência e seleção de fonte são necessárias, mas também se confrontam totais independentes na população delimitada e no cenário com ambas as fontes disponíveis. Totais concordantes não excluem perda compensada por duplicação.
* Sondas: contar apenas requisições observadas. Classificar como jornada apenas com identidade da execução e requisição; atribuir sonda por rota declarada e user-agent `kube-probe/`. Manter tráfego desconhecido. Sondas que não passam pelos observadores não foram medidas; configuração de frequência não produz contagem observada.

## Matriz de argumentos

| Hipótese | Afirmação delimitada | Referência e dependências | Comparação/evidência | Limitação |
|---|---|---|---|---|
| Fidelidade estrutural | Entidades e relações declaradas no inventário aparecem corretamente | API Kubernetes; o produto também depende dela, aquisição independente | `structural_fidelity`, inventários e respostas API selados | Não prova todos os tipos; aquisição sequencial e estado terminal |
| Fidelidade numérica | Valores preservam unidade, dimensão e população | cAdvisor/kubelet, métricas nativas Istio ou OTLP do connector, referências de execução | famílias de métricas, valores, erro absoluto/relativo e limites | Leitura independente pode compartilhar produtor; não é referência física independente |
| Arbitragem | Fonte emitida coincide com configuração selecionada e não soma ambas | Plano/configuração, proveniência e total de checkout independente | cenário `both-sources`, `single_contribution`, contagem | Não exclui compensação agregada entre perda e duplicação |
| Identidade e dinamismo | Substituição de pod preserva distinção temporal e relações | UID Kubernetes e histórico capturado | registros originais do experimento de substituição | Sem rollout adicional; não cobre toda mudança de topologia |
| Reprodução entre implantações | Boutique preserva afirmações em cluster distinto | IDs do provedor/UIDs e hashes científicos | primeiro cluster original e complemento, mesmas regras | Uma amostra por implantação; sem inferência estatística de superioridade |
| Consumo pela API | Cinco perguntas são respondidas pelo contrato público | Ledger do consumidor e referências por pergunta | resultados por pergunta e baseline | Query corrigida apenas offline não é apresentada como executada |

## Referências, tolerâncias e população

As tolerâncias existentes permanecem: erro relativo de 15% para contagens quando previsto pelo contrato e 20% para duração, combinados com os pisos absolutos existentes em `frozen-thresholds`/`scoring.DEFAULTS`. O limite aplicado é o máximo entre piso absoluto e parcela relativa. São critérios operacionais do estudo para fontes com coleta assíncrona e populações delimitadas; não são intervalos de confiança, garantias universais ou prova de ausência de perda. Erro relativo fica indefinido quando a referência é zero. Registrar sempre unidade, referência, observado, erro absoluto e limite efetivamente aplicado.

As 18 métricas selecionadas entre as 43 do catálogo têm referências e semântica comparáveis dentro do protocolo. Disponibilidade no catálogo, comparação tentada e precisão demonstrada são resultados distintos. Não transformar 18/43 em cobertura de precisão nem contar comparações inconclusivas como aprovações. A lista versionada de seleção e suas justificativas permanece em `protocols/thesis-completion-v2/inputs` e nos relatórios por métrica.

Referências de host/container usam kubelet/cAdvisor ou leituras delimitadas do receptor; quando a mesma entrada é usada para conferir transformação, isso é fidelidade da transformação, não uma segunda medição física independente. Traces de checkout pareados usam observadores e spans retidos; o connector e a referência podem compartilhar instrumentação. Comparações fonte→armazenamento e referência→fonte são explicitadas separadamente. O inventário de referências nos relatórios registra a origem, fronteiras e disponibilidade de cada leitura.

## Complemento Boutique

Perfil `boutique-complement-v1`: um cluster novo; Boutique/Istio smoke 60 s, baseline 300 s, depois Boutique/traces smoke 60 s e baseline 300 s. Não executa histórico, arbitragem, Astronomy, escala, rollout, novo consumidor ou versão reduzida de reprodução. Readiness permanece sem geração de carga adicional.

Uma falha operacional admite no máximo um retry da janela. Discordância numérica não dispara retry. Esgotadas as tentativas ou falhando a análise de arquivos já preservados, encerrar, exportar e limpar; janelas restantes permanecem não executadas. O complemento não substitui o segundo cluster histórico e não aprova retroativamente a execução antiga.

R$ 800 é referência de planejamento; ultrapassá-la não interrompe o trabalho. R$ 1.200 é o teto bruto adicional. `complement_budget` exige cotação com data/fonte, margem cambial, reserva de encerramento de pelo menos R$ 120 e pelo menos 30 minutos para limpeza. Os limites em USD são arredondados para baixo. A previsão inclui preparação, retries, exportação, retenção, cópia secundária e encerramento; créditos não reduzem custo bruto. O prazo e watchdog devem respeitar essa previsão. Alertas de faturamento não são mecanismo de imposição de teto. Medições cedem lugar à preservação/limpeza antes da reserva final.

Antes de criar recursos temporários, revisar e aplicar os dois bindings condicionais de quarentena gerados por `quarantine.iam_proposal` para **o run exato**: `roles/storage.objectCreator` e `roles/storage.objectViewer`, apenas para a conta do controlador e o prefixo `gke-quarantine-v1/runs/<run-id>/`. Conferir a política lida do provedor e registrar o recibo. `preflight_requirements` apenas produz uma proposta; não aplica IAM nem comprova acesso. O probe real no controlador continua obrigatório e uma falha não pode ser ignorada. Remover os grants temporários após preservação e limpeza verificadas. A execução `boutique-complement-20261119-v2` demonstrou essa lacuna de preparação: o probe recebeu HTTP 403 antes das quatro janelas, cujos registros permanecem não executados.

Mudanças operacionais desta revisão: corrigir a corrida de `sleep_bounded`; pré-aquecer transporte autenticado de identidade do pod e reutilizá-lo mantendo UID/restart guards; registrar user-agent nos logs dos observadores, sem nova dimensão nas métricas. Registrar essas diferenças no manifesto e verificar separadamente produto, versões, imagens por digest, carga, tolerâncias e configuração científica antes de comparar implantações.

## Leitura dos resultados

Os resultados distinguem a análise original, a reanálise e o complemento, e mantêm visíveis as falhas, as inconclusões e as tentativas. CPU/memória existentes podem formar tabela descritiva, sem inferência causal sobre sobrecarga. Kiali permanece comparação qualitativa pela tabela da revisão de ferramentas. As cinco perguntas justificam consumo pela API; não se cria novo consumidor.

Ameaças/limitações: um provedor, versão Kubernetes efetivamente observada, Istio sidecar, amostra delimitada, dependências compartilhadas das referências, sondas não observadas, atraso evento–API não avaliado e ausência de hipótese de escala/custo. Observação de 720 s não demonstra comportamento durante retenção de 7–30 dias. Repositórios, commits fixos, digests e manifestos verificáveis permitem rastrear reprodução; DOI não é obrigatório.

## Campanha integral revisada

Uma decisão posterior substitui o complemento de quatro janelas por `thesis-robustness-full-v1`: as quatorze janelas originais, duas implantações distintas e a observação temporal existente, com as regras corrigidas congeladas antes da execução. A campanha final deve ser apresentada separadamente dos estudos de desenvolvimento/piloto; não substituir falhas novas por resultados antigos favoráveis.

O novo perfil desconta explicitamente o gasto ou a provisão conservadora da preparação anterior do teto adicional de R$ 1.200, antes da reserva de encerramento. Verifica bindings condicionais exatos de quarentena no provedor antes do lease e dos recursos de execução, após resolver a fundação existente. Essa verificação não concede permissões nem substitui o probe real do controlador. O limite de retry e a interrupção após falha também abrangem a observação temporal.
