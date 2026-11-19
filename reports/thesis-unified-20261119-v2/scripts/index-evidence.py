"""Build a local index of immutable source references; no cloud mutations."""
from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parents[1];E=R/'evidence'
def read(p):return json.loads(p.read_text())
r=read(E/'run-report.json');c=read(E/'claim-matrix.json');m=read(E/'evidence-metadata-verification.json');b=read(R/'resultados.json')
rows=[]
for i,w in enumerate(r['windows'],1):
 e=w['export'];a=e['analysis_export'];rows.append({'window':i,'logical_id':w['logical_id'],'attempt_id':w['attempt_id'],'state':w['state'],'evidence_valid':w['evidence_valid'],'raw_projection':{'uri':e['uri'],'parts':e['objects'],'original_window_seal_sha256':e['original_window_seal_sha256']},'analysis':{'uri':a['uri'],'parts':a['parts'],'members':a['members']}})
local=[]
for p in sorted(R.rglob('*')):
 if p.is_file() and p.name not in ['INDICE-EVIDENCIAS.md','evidence-index.json','SHA256SUMS','package-validation.json']:
  local.append({'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
index={'run_id':m['run_id'],'scope':'Index derived from sealed run report, verified analysis summaries and provider metadata; raw payloads remain in evidence storage. No replacement of original results.','primary_bucket':'karthos-karthos-eval-evidence','secondary_bucket':'karthos-karthos-eval-evidence-backup','windows':rows,'history':r['history']['export'],'research_objects':m['objects'],'closure':read(E/'closure-readback.json'),'local_files':local}
(R/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
text='''# Índice verificável de evidências

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
'''
for x in b['baselines']:
 a=x['archive'];label=next(p for p in (E/'baselines').glob('*.json') if hashlib.sha256(p.read_bytes()).hexdigest()==x['summary_sha256'])
 text+='| '+x['baseline']+' | ['+x['label']+']('+str(label.relative_to(R))+') | `'+a['uri']+a['parts'][0]['name']+'` | `'+a['parts'][0]['sha256']+'` |\n'
text+='''
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
'''
(R/'INDICE-EVIDENCIAS.md').write_text(text)
