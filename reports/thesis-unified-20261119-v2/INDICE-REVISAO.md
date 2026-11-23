# Índice da revisão para a tese

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
