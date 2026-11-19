# Índice verificável de evidências

## Entrada para a redação

- [Relatório final em português](RELATORIO.md): método, seis baselines, resultados, limitações e encerramento.
- [Tabelas geradas](RESULTADOS.md), [resultados estruturados](resultados.json), [valores e erros](resultados-numericos.csv), [famílias](familias.csv), [18 métricas por baseline](metricas-selecionadas.csv) e [população observada](trafego-observado.csv).
- [Índice completo JSON](evidence-index.json): 14 janelas, tentativas, URIs, arquivos internos, hashes, gerações e 498 objetos de pesquisa.
- [Matriz final original](evidence/claim-matrix.json) e [relatório de execução original](evidence/run-report.json), preservados sem reescrita e conferidos contra [selo original](evidence/GKE-SHA256SUMS).
- [Recibo de validação das seis projeções](evidence/summary-validation.json): todos os estados/contagens dos grupos numéricos, primeiros exemplos sem seleção favorável e paridade API. Os arquivos completos permanecem nos arquivos de análise indexados.

## Linhas de base e análises seladas

Os URIs `gs://` requerem acesso autorizado ao bucket. No bucket secundário usa-se o mesmo nome de objeto, trocando apenas o bucket para `karthos-karthos-eval-evidence-backup`. `evidence-metadata-verification.json` registra as gerações distintas em cada cópia. O hash abaixo é do arquivo de análise, não do arquivo bruto de captura.

| Baseline | Resumo local | Arquivo de análise (URI) | SHA-256 |
|---|---|---|---|
| B1 | [Boutique / traces / C1](evidence/baselines/boutique-traces-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-boutique-trace-a1/analysis/window.tar.gz` | `dbe76126d80dc46a85c6374a0e4f203666137d8da068609a556bb977a3bda1db` |
| B2 | [Astronomy / traces / C1](evidence/baselines/astronomy-traces-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-astronomy-trace-a1/analysis/window.tar.gz` | `9e807244e396a0eda12bad0eda19e2ee4d4b7937dbe8608fb7826359d62a948e` |
| B3 | [Astronomy / Istio / C1](evidence/baselines/astronomy-istio-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-astronomy-mesh-a1/analysis/window.tar.gz` | `a59f9f0fd373f5609b9e574fd0bfaf5f385c20574ceb6daa87c4ea8f61c3992a` |
| B4 | [Boutique / Istio / C1](evidence/baselines/boutique-istio-r1-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-1/baseline/official-completion-baseline-boutique-mesh-a1/analysis/window.tar.gz` | `0d9ec587bc3040f9747b96d335f3193f4d6078ecc6e6a7c94cc69a4f6f44b394` |
| B5 | [Boutique / Istio / C2](evidence/baselines/boutique-istio-r2-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-2/baseline/official-r2-baseline-boutique-mesh-a1/analysis/window.tar.gz` | `fc88b084f5ef38f0b12cd12a61483afe8e26bc7366cf764b672dbbe50d4585e7` |
| B6 | [Boutique / traces / C2](evidence/baselines/boutique-traces-r2-summary.json) | `gs://karthos-karthos-eval-evidence/gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/repeat-2/baseline/official-r2-baseline-boutique-trace-a1/analysis/window.tar.gz` | `694d64934eb418558900e5249cebc096bc90e5305882d72370dc168454c2c6d5` |

## Encerramento e escopos da verificação

| Registro | O que comprova |
|---|---|
| [Backup](evidence/backup-receipt.json) | Controlador verificou os payloads copiados; recibo endereçado por SHA-256 |
| [Readback independente](evidence/evidence-metadata-verification.json) | 498 objetos e 1.991.739.650 bytes por cópia; gerações/tamanho/MD5/CRC32C iguais ao recibo; não é novo hash integral de payloads no Mac |
| [Pedido de encerramento](evidence/completion-request.json) | Inventário com UIDs e referências geracionais aos objetos do resumo verificado |
| [Limpeza final](evidence/cleanup-receipt.json) | Ausência de compute/rede inventariados, sem residuais/erros, scheduler pausado e lease liberado; definição Cloud Run retida |
| [Origem dos recibos](evidence/closure-readback.json) | URI, geração, tamanho e SHA-256 dos três recibos lidos posteriormente |
| [Snapshot descritivo](evidence/footprint-observation.json) | CPU/memória já retidos de B1–B4, hashes dos membros e do selo original; cobertura parcial |
| [Congelamento](rule-freeze.json) | Revisão executada, hashes científicos, manifesto, digests de imagens e validação local anterior à medição |

O selo original contém também arquivos não baixados neste pacote. Não executar a checagem integral desse selo no diretório parcial e interpretar arquivos ausentes como corrupção. Os arquivos originais presentes são verificados pelo script abaixo; o índice aponta os demais membros e arquivos remotos. A comparação de 1 ms e as guardas são as da revisão congelada, sem ajuste retrospectivo nesta campanha. Os relatórios de preparativos antigos continuam separados.

## Reprodução da consolidação e conferência local

A partir da raiz do repositório de avaliação:

```sh
python3 reports/thesis-unified-20261119-v2/scripts/build-results.py
python3 reports/thesis-unified-20261119-v2/scripts/verify-package.py
```

O primeiro comando apenas formata resultados já verificados; não roda benchmarks nem reavalia tolerâncias. `RELATORIO.md` incorpora integralmente `RESULTADOS.md`. Os scripts `karthos-current-report-*` documentam a extração/compactação realizada offline com o avaliador congelado; as projeções completas e os arquivos de análise permanecem no Kivel/evidência indexada. Para conferir todos os arquivos do pacote, dentro deste diretório:

```sh
shasum -a 256 -c SHA256SUMS
```

`SHA256SUMS` cobre o pacote final, incluindo relatório, CSVs, scripts e recibos. `package-validation.json` registra as verificações locais. Retenção remota declarada: 90 dias, não preservação permanente. Não houve publicação no GitHub nem DOI. A fatura final não foi verificada.
