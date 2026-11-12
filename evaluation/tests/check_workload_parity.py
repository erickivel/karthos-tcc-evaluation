"""Run from the harness root: python3 evaluation/tests/check_workload_parity.py.

Uses an isolated Node Docker container to execute the real run.js adapter with
k6 API shims; does not contact an application or cluster. This checks request,
identity and ledger parity, not k6's runtime scheduling or HTTP implementation.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evaluation.workloads.common import journey

run_ids = ['parity', 'pilot-v2-20261019-b:r1-boutique-trace:k6-checkout-heavy',
           'pilot-v2-20261019-b:r2-boutique-trace:locust-checkout-heavy', 'á:' * 100]
fixtures = [dict(demo=demo, workload=workload, n=n, run_id=run_id,
                 expected=journey(demo, workload, n, run_id))
            for run_id in run_ids for demo in ('boutique', 'astronomy')
            for workload in ('legacy', 'checkout-heavy') for n in range(50)]
script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const nodeCrypto = require('crypto');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
const config = fs.readFileSync('/work/evaluation/workloads/workload.json', 'utf8');
const source = fs.readFileSync('/work/evaluation/workloads/run.js', 'utf8')
  .replace(/^import .*\n/gm, '').replace('export default function ()', 'function run ()')
  .replace(/export (const|function) /g, '$1 ');
let calls = 0;
for (const f of fixtures) {
  const captured = [], logs = [];
  const ctx = {
    __ENV: {DEMO:f.demo, WORKLOAD:f.workload, RUN_ID:f.run_id, TARGET:'http://frontend'},
    open: () => config, exec: {scenario: {iterationInTest:f.n}},
    console: {log:x=>logs.push(x)}, check:()=>{},
    crypto: {sha256:(value, encoding)=>nodeCrypto.createHash('sha256').update(value,'utf8').digest(encoding)},
    http: {
      cookieJar:()=>({clear:()=>{}}), expectedStatuses:x=>x,
      request:(method,url,body,params)=> {
        const step=f.expected[captured.length];
        captured.push({method,path:url.replace('http://frontend',''),body,headers:params.headers,route:params.tags.route});
        let text='';
        if(step.outcome==='boutique_order')text='Your order is complete!';
        if(step.outcome==='expired')text='failed to complete the order: expired';
        if(step.outcome==='payment_failed')text='{"code":"PAYMENT_FAILED"}';
        if(step.outcome==='astronomy_order')text='{"orderId":"o","items":[{"item":{"productId":"OLJCESPC7Z","quantity":1}}]}';
        if(step.outcome==='cart_item')text='{"items":[{"productId":"OLJCESPC7Z","quantity":1}]}';
        return {status:step.expected_status,body:text};
      }
    }
  };
  vm.runInNewContext(source+'\nconst window=setup();run();teardown(window);',ctx);
  const rows=logs.filter(x=>x.startsWith('EVAL_REQUEST ')).map(x=>JSON.parse(x.slice(13)));
  const attempts=logs.filter(x=>x.startsWith('EVAL_ATTEMPT ')).map(x=>JSON.parse(x.slice(13)));
  const windows=logs.filter(x=>x.startsWith('EVAL_WINDOW ')).map(x=>JSON.parse(x.slice(12)));
  assert.equal(captured.length,f.expected.length); assert.equal(rows.length,captured.length);
  assert.equal(attempts.length,captured.length); assert.equal(windows.length,2);
  assert.equal(windows[0].run_id,f.run_id); assert.equal(windows[0].ended_at,null);
  assert.ok(windows[1].ended_at);
  captured.forEach((actual,i)=>{
    const expected=f.expected[i];
    assert.equal(actual.method,expected.method); assert.equal(actual.path,expected.path); assert.equal(actual.route,expected.route);
    if(expected.body && typeof expected.body==='object')assert.deepStrictEqual(JSON.parse(actual.body),expected.body);
    else assert.equal(actual.body,expected.body);
    for(const [key,value] of Object.entries(expected.headers))assert.equal(actual.headers[key],value);
    assert.equal(actual.headers['X-Evaluation-Run'],f.run_id);
    assert.equal(actual.headers['X-Evaluation-Request-ID'],`${f.run_id}:${f.n}:${i}`);
    assert.equal(rows[i].run_id,f.run_id); assert.equal(rows[i].request_id,`${f.run_id}:${f.n}:${i}`);
    assert.equal(attempts[i].request_id,rows[i].request_id);
    assert.equal(rows[i].outcome_ok,true); assert.equal(rows[i].status,expected.expected_status);assert.equal(rows[i].completed,true);
    calls++;
  });
}
console.log(`PASS: ${fixtures.length} journeys, ${calls} requests matched paths, payloads, hashed identities, headers, outcomes, attempts, ledger and windows`);
'''
subprocess.run(['docker', 'run', '--rm', '-i', '-v', f'{ROOT}:/work:ro',
                'node:22-alpine', 'node', '-e', script], input=json.dumps(fixtures),
               text=True, check=True)
