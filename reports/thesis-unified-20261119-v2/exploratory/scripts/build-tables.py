"""Build the exploratory CSV tables from derived data; no sealed-file writes."""
import json,pathlib,csv,statistics,datetime,hashlib
P=pathlib.Path(__file__).resolve().parents[1];D=P/'raw-derived'
def read(p):return json.loads(p.read_text())
def utc(t):return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def csvfile(name,rows):
 with (P/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
baselines=[read(D/f'B{i}.json') for i in range(1,7)];replay=read(D/'replay.json');dur=read(D/'durations.json');host=read(D/'host-60s.json');history=read(D/'history.json')
resources=[dict(baseline=d['label'],**x) for d in baselines for x in d['E5']];csvfile('recursos.csv',resources);csvfile('host-60s.csv',host['rows'])
