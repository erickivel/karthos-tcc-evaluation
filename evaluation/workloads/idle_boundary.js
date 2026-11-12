import http from 'k6/http';
import exec from 'k6/execution';
import crypto from 'k6/crypto';
import { check, sleep } from 'k6';

const runId = __ENV.RUN_ID;
const base = __ENV.TARGET.replace(/\/$/, '');
const delays = [5.995, 5.998, 5.999, 6.000, 6.001, 6.002, 6.005];

// A closed diagnostic: no offered-rate or dropped-arrival accuracy claim.
// HTTP transport internals can retry connections; packets establish actual reuse.
export const options = {
  scenarios: { idle_boundary: {
    executor: 'per-vu-iterations', vus: 1, iterations: 21,
    maxDuration: '300s', gracefulStop: '5s',
  } },
  maxRedirects: 0,
  noConnectionReuse: false,
  noVUConnectionReuse: false,
  tags: { evaluation: 'karthos', demo: 'astronomy', run: runId, workload: 'idle-boundary' },
  thresholds: { checks: ['rate==1'] },
};

function windowRecord(startedAt, endedAt) {
  return { run_id: runId, generator: 'k6', mode: 'idle-boundary',
    offered_model: 'closed-per-vu-iterations', started_at: startedAt, ended_at: endedAt,
    intended_duration_seconds: null, max_duration_seconds: 300,
    target_iterations_per_second: null, expected_iterations: 21,
    expected_pairs: 21, expected_requests: 42, delays_seconds: delays,
    scheduling_evidence: 'dropped_iterations not applicable to closed model' };
}

export function setup() {
  const startedAt = new Date().toISOString();
  console.log('EVAL_WINDOW ' + JSON.stringify(windowRecord(startedAt, null)));
  return { startedAt };
}

export function teardown(data) {
  console.log('EVAL_WINDOW ' + JSON.stringify(windowRecord(data.startedAt, new Date().toISOString())));
}

function request(pair, phase, delay, control, warmCompletedAt) {
  const requestId = `${runId}:${pair}:${phase}`;
  const traceId = crypto.sha256(requestId, 'hex').slice(0, 32);
  const traceparent = `00-${traceId}-${crypto.sha256(requestId + ':client', 'hex').slice(0, 16)}-01`;
  const connection = phase === 'warm' && !control ? 'keep-alive' : 'close';
  const started = new Date();
  const metadata = { request_id: requestId, run_id: runId, generator: 'k6',
    mode: 'idle-boundary', route: 'product', journey_id: `${runId}:${pair}`,
    pair_index: pair, iteration: pair, phase, step_index: phase === 'warm' ? 0 : 1,
    idle_target_seconds: delay, fresh_connection_control: control,
    connection_header: connection, trace_id: traceId, traceparent,
    started_at: started.toISOString(),
    warm_completed_at: warmCompletedAt,
    actual_idle_seconds: warmCompletedAt === null ? null : (started.getTime() - Date.parse(warmCompletedAt)) / 1000 };
  console.log('EVAL_ATTEMPT ' + JSON.stringify(metadata));
  let response = null;
  let exception = null;
  try {
    response = http.get(base + '/api/products/OLJCESPC7Z', {
      headers: { Host: 'frontend-proxy:8080', Connection: connection,
        'X-Evaluation-Run': runId, 'X-Evaluation-Request-ID': requestId, traceparent },
      tags: { route: 'product', request_id: requestId, pair: String(pair), phase,
        idle_target: String(delay), control: control ? 'fresh' : 'persistent' },
      timeout: '15s', redirects: 0, responseCallback: http.expectedStatuses(200),
    });
    exception = response.error || null;
  } catch (error) {
    exception = String(error);
  }
  const completed = new Date();
  const ok = Boolean(response && response.status === 200);
  check(response, { 'diagnostic GET returns 200': () => ok });
  console.log('EVAL_REQUEST ' + JSON.stringify(Object.assign({}, metadata, {
    completed_at: completed.toISOString(), duration_ms: completed - started,
    http_duration_ms: response ? response.timings.duration : null,
    status: response ? response.status : 0, exception,
    expected_status: 200, outcome_ok: ok, attempted: true,
    completed: Boolean(response && response.status > 0),
    unexpected_response_body: response && response.status !== 200 ? String(response.body || '').slice(0, 1024) : null,
  })));
  return completed.toISOString();
}

export default function () {
  const pair = exec.scenario.iterationInTest;
  const delay = delays[Math.floor(pair / 3)];
  const control = pair % 3 === 2;
  const warmCompletedAt = request(pair, 'warm', delay, control, null);
  sleep(delay);
  request(pair, 'probe', delay, control, warmCompletedAt);
}

export function handleSummary(data) {
  const directory = __ENV.EVAL_ARTIFACT_DIR || '/tmp/eval-artifacts';
  if (__ENV.EVAL_ARTIFACT_MODE === '1') {
    return { [directory + '/k6-summary.json']: JSON.stringify(data) + '\n' };
  }
  return { stdout: '__EVAL_K6_SUMMARY_BEGIN__\n' + JSON.stringify(data) + '\n__EVAL_K6_SUMMARY_END__\n' };
}
