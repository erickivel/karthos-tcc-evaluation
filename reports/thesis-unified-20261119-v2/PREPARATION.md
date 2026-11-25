# Preparação da avaliação

Esta campanha manteve as 14 janelas e a observação do histórico de até 720 s previstas no protocolo. Os dois preparativos anteriores falharam antes de produzir medições, e os seus registros continuam guardados. A campanha só poderia ser utilizada como a avaliação principal depois que as evidências fossem obtidas, seladas e verificadas, e resultados negativos ou inconclusivos continuam sendo resultados válidos.

O Karthos foi mantido no *commit* `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`, com a carga de duas jornadas por segundo, e as tolerâncias e os critérios de aprovação não foram alterados. A correção em relação aos preparativos anteriores foi apenas operacional: o controlador passou a ser identificado pelo e-mail da sua conta, cada *cluster* descartável recebeu uma autorização explícita, as permissões passaram a ser verificadas antes da preparação das aplicações e uma negação explícita de autorização passou a interromper a execução, sem nova tentativa. A função de limpeza foi corrigida e verificada antes da criação da infraestrutura, como registrado em `foundation-fix.json`.

Cada *cluster* tem um identificador distinto e exige uma autorização ligada ao seu inventário, ao recibo de criação e à concessão ativa da execução. A credencial do operador fica apenas na memória local e não é transferida ao controlador, que verifica a sua identidade, as permissões efetivas e operações RBAC descartáveis, com a limpeza confirmada.

## Orçamento e prazo

O valor de R$ 800 era apenas uma referência, e o teto adicional de R$ 1.200 incluía os dois preparativos anteriores, esta campanha, a exportação e a limpeza. A provisão para os dois preparativos foi estimada em R$ 450, sem que isso seja uma fatura. A previsão da campanha era de USD 90,7218272, o que, com o câmbio de R$ 5,8102 por dólar, uma margem de 10% e uma reserva adicional de R$ 150, resulta em R$ 1.179,83, arredondados para cima. O detalhamento está em `budget-review.json`, cujo total calculado é a referência numérica, e os créditos da nuvem não foram descontados.

A previsão de tempo era de 67.320 s, incluindo duas esperas de autorização de 600 s e uma reserva de 3.600 s para a limpeza, dentro de um prazo máximo de 72.000 s. O *watchdog* independente precisava ser verificado antes da criação da infraestrutura temporária, e a reserva final não poderia ser utilizada para tentar concluir medições.

## Critérios de início e de encerramento

Antes do início, a revisão do avaliador e os seus testes foram fixados em um *commit* e em imagens identificadas por *digest*, e os *hashes* do Karthos, das entradas da avaliação e das ferramentas foram preservados. Também foram verificadas as concessões de quarentena desta execução, a ausência de outra concessão ativa, a função de limpeza e a configuração da conta e do projeto. A autorização do primeiro *cluster* acontece antes da transferência do controle, e a do segundo, na espera explícita de preparação.

Cada janela permite no máximo uma nova tentativa por falha operacional e nenhuma por discordância numérica ou por autorização negada, e nenhuma lacuna desta campanha pode ser preenchida com resultados anteriores. O encerramento só pode ser declarado depois de verificadas a exportação, a cópia secundária e a remoção dos recursos temporários.

Este documento registra o estado antes da execução, quando nenhuma janela desta campanha tinha sido executada. Os resultados estão no [relatório da avaliação](RELATORIO.md).
