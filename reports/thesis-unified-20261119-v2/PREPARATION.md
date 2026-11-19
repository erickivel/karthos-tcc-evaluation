# Preparação da avaliação unificada — segunda tentativa

Esta campanha mantém as quatorze janelas e a observação histórica de 720 s da proposta unificada aprovada. Os dois preparativos anteriores falharam antes das medições; seus registros permanecem preservados. Esta tentativa só poderá ser usada como avaliação principal após aquisição, selagem e verificação das evidências. Resultados negativos e inconclusivos permanecem resultados válidos para o relatório.

O Karthos permanece no commit `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`, com duas jornadas por segundo. Não há alteração das tolerâncias ou dos critérios científicos. A correção é operacional: identidade por e-mail da conta do controlador, autorização explícita em cada cluster descartável, verificação de permissões antes da preparação das aplicações e interrupção sem retry diante de negação explícita de autorização. A função de limpeza implantada foi corrigida e verificada antes de provisionar; ver `foundation-fix.json`.

Cada implantação tem UID distinto e exige uma autorização vinculada ao inventário imutável, ao recibo de criação e à concessão ativa da execução. O token do operador permanece em memória local; não é transferido ao controlador. A verificação no controlador testa a identidade, as permissões efetivas e operações RBAC descartáveis com limpeza confirmada.

## Orçamento e prazo

R$ 800 é apenas referência. O teto adicional de R$ 1.200 inclui os dois preparativos malsucedidos, esta campanha, exportação e limpeza. A provisão conservadora conjunta para os preparativos é R$ 450; não se trata de uma fatura. A previsão da campanha é USD 90,7218272. Com câmbio de R$ 5,8102/USD, margem de 10% e reserva adicional de R$ 150, o total arredondado para cima é R$ 1.179,83. O detalhamento verificável está em `budget-review.json`; seu total calculado é a referência numérica. Créditos não são deduzidos.

A previsão temporal é 67.320 s, incluindo duas esperas de autorização de 600 s e reserva de limpeza de 3.600 s. O prazo máximo permanece 72.000 s. O watchdog independente deve ser verificado antes da infraestrutura temporária. Não ultrapassar a reserva final para tentar concluir medições.

## Critérios de lançamento e encerramento

A revisão do avaliador e seus testes devem ser congelados em commit e imagem por digest antes do lançamento. Preservar os hashes do produto, cache científico e ferramentas. Verificar as concessões de quarentena específicas desta execução, a ausência de outra concessão ativa, a função de limpeza e a configuração da conta/projeto. A autorização do primeiro cluster ocorre antes da transferência de controle; a do segundo ocorre na espera explícita de preparação.

Manter no máximo um retry operacional por janela, nenhum por discordância numérica ou autorização negada. Não substituir lacunas desta campanha por resultados favoráveis antigos. Verificar exportação, cópia secundária e ausência dos recursos temporários antes de declarar encerramento. O relatório final será em português, sem edição do LaTeX.

Estado: preparação local; nenhuma janela desta tentativa foi executada.
