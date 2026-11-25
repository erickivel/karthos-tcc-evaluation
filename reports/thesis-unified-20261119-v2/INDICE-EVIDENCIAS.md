# Índice das evidências

Este índice reúne os arquivos que sustentam o [relatório da avaliação](RELATORIO.md) e mostra onde cada evidência pode ser conferida.

## Resultados

- [RELATORIO.md](RELATORIO.md): método, as seis linhas de base, os resultados, a análise posterior à execução, as limitações e o encerramento.
- [resultados.json](resultados.json), [resultados-numericos.csv](resultados-numericos.csv), [familias.csv](familias.csv), [metricas-selecionadas.csv](metricas-selecionadas.csv) e [trafego-observado.csv](trafego-observado.csv): resultados estruturados, valores e erros, famílias de comparações, as 18 métricas por linha de base e a população observada.
- [evidence-index.json](evidence-index.json): as 14 janelas, as tentativas, as URIs, os arquivos internos, os *hashes*, as gerações e os 498 objetos guardados.
- [evidence/claim-matrix.json](evidence/claim-matrix.json) e [evidence/run-report.json](evidence/run-report.json): a matriz de afirmações e o relatório de execução originais, sem nenhuma alteração, conferidos com o selo [evidence/GKE-SHA256SUMS](evidence/GKE-SHA256SUMS).
- [evidence/summary-validation.json](evidence/summary-validation.json): validação dos resumos das seis linhas de base, com os estados e as contagens de cada grupo numérico, as primeiras comparações de cada métrica (sem escolha pelo resultado) e a paridade com a API. Os dados completos ficam nos arquivos de análise listados abaixo.

## Linhas de base e arquivos de análise

Os arquivos de análise de cada linha de base ficam no armazenamento da campanha, em *buckets* do Google Cloud que exigem acesso autorizado. A cópia secundária tem os mesmos nomes de objeto, no *bucket* `karthos-karthos-eval-evidence-backup`, e as gerações de cada cópia estão em [evidence/evidence-metadata-verification.json](evidence/evidence-metadata-verification.json). O *hash* da tabela é o do arquivo de análise, e não o do arquivo bruto da captura.

| Linha de base | Resumo | Arquivo de análise (URI) | SHA-256 |
|---|---|---|---|
| B1 | [Online Boutique, *traces*, C1](evidence/baselines/boutique-traces-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-boutique-trace-a1/analysis/window.tar.gz` | `dbe76126d80dc46a85c6374a0e4f203666137d8da068609a556bb977a3bda1db` |
| B2 | [OpenTelemetry Demo, *traces*, C1](evidence/baselines/astronomy-traces-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-astronomy-trace-a1/analysis/window.tar.gz` | `9e807244e396a0eda12bad0eda19e2ee4d4b7937dbe8608fb7826359d62a948e` |
| B3 | [OpenTelemetry Demo, Istio, C1](evidence/baselines/astronomy-istio-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-astronomy-mesh-a1/analysis/window.tar.gz` | `a59f9f0fd373f5609b9e574fd0bfaf5f385c20574ceb6daa87c4ea8f61c3992a` |
| B4 | [Online Boutique, Istio, C1](evidence/baselines/boutique-istio-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-boutique-mesh-a1/analysis/window.tar.gz` | `0d9ec587bc3040f9747b96d335f3193f4d6078ecc6e6a7c94cc69a4f6f44b394` |
| B5 | [Online Boutique, Istio, C2](evidence/baselines/boutique-istio-r2-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-2/baseline/official-r2-baseline-boutique-mesh-a1/analysis/window.tar.gz` | `fc88b084f5ef38f0b12cd12a61483afe8e26bc7366cf764b672dbbe50d4585e7` |
| B6 | [Online Boutique, *traces*, C2](evidence/baselines/boutique-traces-r2-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-2/baseline/official-r2-baseline-boutique-trace-a1/analysis/window.tar.gz` | `694d64934eb418558900e5249cebc096bc90e5305882d72370dc168454c2c6d5` |

Nos nomes dos arquivos, `astronomy` se refere ao OpenTelemetry Demo.

## Encerramento

| Registro | O que comprova |
|---|---|
| [Backup](evidence/backup-receipt.json) | O controlador da execução verificou os arquivos copiados, e o recibo é identificado pelo seu SHA-256 |
| [Verificação dos metadados](evidence/evidence-metadata-verification.json) | 498 objetos e 1.991.739.650 bytes em cada cópia, com geração, tamanho, MD5 e CRC32C iguais aos do recibo, sem uma nova leitura integral dos arquivos |
| [Pedido de encerramento](evidence/completion-request.json) | Inventário com UIDs e as gerações dos objetos do resumo verificado |
| [Limpeza final](evidence/cleanup-receipt.json) | Remoção dos recursos de computação e de rede inventariados, sem recursos restantes nem erros, com o agendador pausado e a reserva liberada, e com a definição do Cloud Run mantida |
| [Origem dos recibos](evidence/closure-readback.json) | URI, geração, tamanho e SHA-256 dos três recibos lidos depois do encerramento |
| [Leituras pontuais de consumo](evidence/footprint-observation.json) | CPU e memória lidas durante B1 a B4, com os *hashes* dos arquivos e do selo original, e com cobertura parcial |
| [Fixação das regras](rule-freeze.json) | Revisão executada, *hashes* das entradas da avaliação, manifesto, *digests* das imagens e validação local feita antes das medições |

O selo original também lista arquivos que não fazem parte deste pacote. Por isso, conferi-lo inteiro nesta pasta acusaria esses arquivos como ausentes, sem que isso indique corrupção. Os arquivos originais presentes aqui são conferidos pelo *script* abaixo, e o índice aponta onde ficam os demais. A comparação com tolerância de 1 ms e as demais verificações são as da revisão fixada antes da campanha, sem nenhum ajuste posterior. Os relatórios dos preparativos anteriores continuam em pastas separadas.

## Conferência local

A partir da raiz do repositório de avaliação:

```sh
python3 reports/thesis-unified-20261119-v2/scripts/build-results.py
python3 reports/thesis-unified-20261119-v2/scripts/index-evidence.py
python3 reports/thesis-unified-20261119-v2/scripts/verify-package.py
```

O primeiro comando gera os resultados estruturados e os CSVs a partir dos resumos verificados, sem executar novas medições nem reavaliar as tolerâncias. O segundo atualiza o índice dos arquivos, e o terceiro confere o pacote. Os *scripts* `karthos-current-report-*` documentam a extração e a compactação feitas depois da execução, com o avaliador fixado, e as projeções completas e os arquivos de análise ficam no armazenamento indexado. Para conferir todos os arquivos do pacote, dentro desta pasta:

```sh
shasum -a 256 -c SHA256SUMS
```

O arquivo `SHA256SUMS` cobre o pacote, incluindo o relatório, os CSVs, os *scripts* e os recibos, e o [package-validation.json](package-validation.json) registra as verificações locais. As exportações no armazenamento da nuvem tinham uma retenção declarada de 90 dias, e este pacote, publicado no repositório de avaliação, é o registro permanente dos resultados. A fatura final da nuvem não foi verificada.
