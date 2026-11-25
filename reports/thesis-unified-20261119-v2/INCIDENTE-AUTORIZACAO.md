# Correção da autorização antes da avaliação

A tentativa anterior de 14 janelas parou antes de produzir medições. O primeiro erro registrado foi a negação da permissão `container.clusterRoleBindings.update` à conta de serviço do controlador, e a segunda tentativa falhou na desinstalação do Helm. Não se assume que os dois erros tenham exatamente a mesma causa. O relatório e as duas cópias das evidências dessa tentativa continuam guardados em `../thesis-unified-20261119/`.

A correção envolveu tanto a identidade quanto a autorização efetiva. A máquina virtual do controlador passou a receber o escopo `userinfo.email`, e cada *cluster* descartável passou a receber uma permissão explícita para o e-mail esperado do controlador. O operador confere o UID, a propriedade, a reserva ativa, o inventário e o recibo de criação de cada *cluster*. A sua credencial fica apenas na memória local, sem ser gravada nem transferida ao controlador, e nenhum papel administrativo geral foi concedido no projeto.

Antes de preparar as aplicações, o controlador verifica a identidade autenticada, consulta as suas permissões e cria, altera, atualiza e remove objetos RBAC descartáveis. Uma negação explícita de autorização interrompe a execução, sem repetir a mesma operação como uma nova tentativa da avaliação. Os diagnósticos continuam guardados, inclusive quando a falha acontece no consumidor ou na coleta do histórico.

## Verificações no início do primeiro *cluster*

| Verificação | Resultado observado |
| --- | --- |
| Escopo de identidade da máquina virtual | `cloud-platform` e `userinfo.email`, com a conta esperada |
| Preparação do primeiro *cluster* | Permissão lida depois da criação, com os UIDs do *cluster* e da máquina virtual verificados |
| Acesso temporário do operador | Túnel encerrado e regra de *firewall* temporária removida |
| Identidade vista pela API do Kubernetes | E-mail esperado do controlador |
| Verificações de permissão | 17 aprovadas |
| Teste das operações RBAC | Quatro objetos criados, alterados e removidos, com a limpeza confirmada |
| Quarentena | Envio e leitura de um arquivo verificados |
| Karthos e configuração da avaliação | *Commit*, imagens, tolerâncias e carga preservados |

Os recibos estão em `controller-identity-observation.json`, `operator-bootstrap-repeat-1.json` e `authorization-repeat-1.json`, e o registro externo guarda também o recibo da quarentena. A revisão do avaliador, os *hashes* e o instante anterior ao início estão em `rule-freeze.json`, e a suíte de testes local passou com 1.537 testes, com quatro ignorados.

A função customizada do *watchdog* recebeu a permissão `compute.networks.updatePolicy`, com a aplicação e a leitura posterior registradas em `foundation-fix.json`. O *watchdog* passou em uma execução de teste autenticada e foi ativado antes da criação da infraestrutura temporária, o que não substitui a verificação final da remoção dos recursos. O segundo *cluster* passou pela mesma autorização, com o seu próprio UID.

Essas verificações mostram que o caminho de autorização foi corrigido no primeiro *cluster*. Elas não são resultados sobre a fidelidade do modelo e não apoiam nenhuma hipótese, e os resultados da campanha estão no [relatório da avaliação](RELATORIO.md).
