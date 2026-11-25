# Encerramento da tentativa de avaliação unificada

A execução `thesis-robustness-full-20261119` foi preparada para repetir as 14 janelas do estudo original com o avaliador robustecido, mas falhou na preparação da primeira janela. Nenhuma janela de medição foi executada. Portanto, esta tentativa não substitui a avaliação original e não demonstra aprovação nem reprovação das hipóteses científicas.

## Protocolo preservado

O produto permaneceu fixado em `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`; o avaliador de execução foi congelado em `9cfad320647910774573f5f3701ef1c9c2fe83a1`. A matriz previa 14 janelas, duas implantações distintas, duas jornadas por segundo e a observação temporal existente de 720 segundos. A revisão posterior `8c7efe6` altera somente a identificação de campanhas no relatório offline e não foi incorporada à imagem em execução.

A suíte local do avaliador congelado passou com 1.481 testes e quatro testes ignorados. A suíte posterior do relatório passou com 1.486 testes e quatro ignorados. Esses resultados verificam comportamento local; não demonstram permissões operacionais ou fidelidade do produto no GKE.

## Resultado operacional

| Item | Resultado observado |
| --- | --- |
| Verificação real de upload e leitura da quarentena | Aprovada |
| Primeira tentativa da janela Boutique/traces smoke | Falhou antes da medição: permissão de patch de ClusterRoleBinding negada ao controlador |
| Segunda tentativa da mesma janela | Falhou antes da medição durante `helm uninstall karthos` |
| Janelas medidas | 0 de 14 |
| Outras janelas | 13 não tentadas |
| Observação temporal e comparação entre implantações | Não executadas |
| Decisão | Encerramento após esgotar a única repetição operacional permitida |

A primeira falha informa o principal numérico da conta de serviço do controlador e a permissão ausente `container.clusterRoleBindings.update`. A consulta ao papel `roles/container.developer` confirmou apenas leitura e listagem para objetos RBAC. O papel `roles/container.clusterAdmin` administra o ciclo de vida dos clusters e não substitui as permissões Kubernetes de instalação. A segunda falha está registrada pelo erro de desinstalação preservado, e a sua causa detalhada não foi presumida a partir da primeira.

Há também uma diferença de identidade verificável na configuração da VM: o controlador recebe somente o escopo OAuth `cloud-platform`, sem `userinfo-email`. A documentação do GKE explica que, nesse caso, a API Kubernetes recebe o identificador numérico da conta em vez de seu e-mail. Isso é compatível com o principal numérico observado no erro, mas não prova que adicionar o escopo, isoladamente, resolveria todas as permissões de instalação. A correção deve verificar tanto a identidade autenticada quanto as autorizações efetivas; um binding por e-mail não deve ser presumido válido para um principal numérico.

Referências para a correção operacional: [IAM e RBAC no GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/access-control), [autorização RBAC](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/role-based-access-control) e [permissões da API](https://docs.cloud.google.com/kubernetes-engine/docs/reference/api-permissions).

## Implicações para a avaliação

As 19 afirmações aplicáveis à nova avaliação em nuvem permanecem com evidência insuficiente. A matriz também registra cinco verificações locais previamente existentes como aprovadas e uma afirmação opcional como não aplicável; esses cinco resultados não são medições desta execução no GKE. Não se devem preencher essas lacunas com resultados favoráveis do estudo anterior nem apresentar esta tentativa como uma avaliação completa. Os resultados originais e a reanálise continuam preservados separadamente.

A proposta de uma avaliação final unificada continua possível, mas depende de corrigir e verificar as permissões reais do controlador para instalação, atualização e remoção, inclusive após recriar o cluster. Uma nova execução exige uma nova decisão sobre lançamento e orçamento, pois esta tentativa esgotou o limite operacional congelado. Corrigir permissões de implantação não exige alterar as APIs do produto ou os critérios numéricos.

O próximo protocolo deve verificar as operações RBAC necessárias antes de iniciar a preparação das aplicações. Uma verificação de leitura da política IAM ou uma suíte local não substitui a autorização efetiva do principal no cluster. A correção deve documentar o escopo e a duração das permissões, evitando um aumento permanente e indiscriminado de privilégios no projeto.

## Correções operacionais posteriores

A revisão `6e19b8b` acrescenta `userinfo-email` aos escopos da VM do controlador e `compute.networks.updatePolicy` ao papel customizado de limpeza. A segunda permissão foi identificada durante o encerramento: o watchdog recebeu HTTP 403 ao remover o firewall. O operador autenticado removeu exclusivamente esse firewall após conferir UID e propriedade, sem ampliar permissões no projeto.

Essas alterações de configuração passaram em `terraform fmt -check` e na suíte de 1.486 testes, com quatro ignorados. Não foram aplicadas à infraestrutura nem incluídas retroativamente no avaliador congelado. Elas não resolvem, isoladamente, a falta de autorização RBAC para instalação do produto. Uma nova execução continua condicionada à verificação dessa autorização e à reconciliação do orçamento restante.

## Evidências e custo

O manifesto, os digests das imagens, a revisão científica e o orçamento estão em `rule-freeze.json`, `scientific-source-review.json` e `budget-review.json`. O estado operacional está em `execution-status.json`.

A leitura independente confirmou as duas cópias finais: 28 arquivos selados mais o arquivo de checksums em cada destino. A verificação de todos os objetos de pesquisa confirmou 49 objetos e 924.548 bytes por cópia, com igualdade de nomes, tamanhos e SHA-256. O índice verificável está em `evidence-index.json`, e a síntese estruturada das janelas e afirmações está em `results.json`. A ausência dos recursos temporários foi confirmada diretamente no provedor em 19/11/2026 às 08:31:46 UTC (05:31:46 em São Paulo): clusters, VMs, discos, roteadores, redes, sub-redes, firewalls, watchdog e agendador ausentes. O lease foi liberado, e os dois grants temporários da quarentena foram revogados. Os recibos estão em `closure.json`. A infraestrutura protegida de evidências e seu estado Terraform foram preservados; uma reutilização deve reconciliar o estado com os objetos já removidos. A conclusão da limpeza não deve ser inferida apenas do encerramento do processo. O custo efetivamente faturado ainda não foi reconciliado; o orçamento é uma previsão e não uma fatura. R$ 800 permanece referência de planejamento, enquanto R$ 1.200 continua sendo o teto adicional autorizado, incluindo preparação, tentativas, exportação e limpeza.
