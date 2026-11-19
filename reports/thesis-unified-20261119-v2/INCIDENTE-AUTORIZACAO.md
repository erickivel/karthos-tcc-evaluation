# Correção operacional antes da avaliação

A tentativa anterior de 14 janelas parou antes das medições. O primeiro erro preservado negou `container.clusterRoleBindings.update` ao principal numérico `104260585867013899667`; a segunda tentativa falhou na desinstalação do Helm. Não se presume que os dois erros tenham causa detalhada idêntica. O relatório e as duas cópias das evidências anteriores permanecem preservados em `../thesis-unified-20261119/`.

A correção combina identidade e autorização efetiva. A VM recebe o escopo `userinfo.email`; cada cluster descartável recebe um binding explícito para o e-mail esperado do controlador. O operador confere UID, propriedade, lease ativo, inventário por geração e recibo imutável de criação. O token fica em memória local e não é gravado ou transferido como credencial ao controlador. Não foi concedido um papel administrativo geral no projeto.

Antes de preparar aplicações, o controlador verifica a identidade autenticada, consulta permissões e executa operações de criação, patch, atualização e remoção em objetos RBAC descartáveis. Negações explícitas de autorização interrompem a execução sem repetir a mesma operação como nova tentativa científica. Os diagnósticos permanecem preservados, inclusive quando a falha ocorre no consumidor ou na coleta histórica.

## Evidência ao iniciar a primeira implantação

| Verificação | Evidência observada |
| --- | --- |
| Escopo de identidade na VM | `cloud-platform` e `userinfo.email`; conta esperada |
| Bootstrap do primeiro cluster | Binding lido após criação; UID do cluster e da VM verificados |
| Acesso temporário do operador | Túnel encerrado e firewall temporário removido |
| Principal visto pela API Kubernetes | E-mail esperado do controlador |
| Verificações de permissão | 17 aprovadas |
| Prova operacional RBAC | Quatro objetos criados, modificados e removidos; limpeza confirmada |
| Quarentena | Upload e leitura reais verificados |
| Produto e configuração científica | Commit, imagens científicas, tolerâncias e carga preservados |

Os recibos são `controller-identity-observation.json`, `operator-bootstrap-repeat-1.json` e `authorization-repeat-1.json`. A observação externa retém também o recibo da quarentena. A revisão do avaliador, os hashes e o instante anterior ao lançamento estão em `rule-freeze.json`; a suíte local passou com 1.537 testes, quatro ignorados.

A função customizada do watchdog recebeu `compute.networks.updatePolicy`, com aplicação e leitura posterior registradas em `foundation-fix.json`. O watchdog passou no dry-run autenticado e foi armado antes da infraestrutura temporária. Isso não substitui a verificação final de remoção dos recursos. A segunda implantação precisa passar pela mesma autorização com seu próprio UID.

Esses resultados demonstram a correção do caminho de autorização observado na primeira implantação. Não são resultados de fidelidade do modelo, não aprovam hipóteses científicas e não demonstram que as 14 janelas terminaram. A campanha final deve ser relatada a partir das evidências seladas, preservando qualquer resultado negativo ou inconclusivo.
