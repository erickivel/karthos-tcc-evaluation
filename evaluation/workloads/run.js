import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import crypto from 'k6/crypto';

const config = JSON.parse(open('./workload.json'));
const demo = __ENV.DEMO;
const workload = __ENV.WORKLOAD || 'legacy';
const runId = __ENV.RUN_ID;
const userPrefix = 'eval-' + crypto.sha256(runId, 'hex').slice(0, 12);
const base = __ENV.TARGET.replace(/\/$/, '');
const contract = config.demos[demo];
const schedule = config.workloads[workload].schedule;

export const options = {
  scenarios: { controlled: {
    executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 2), timeUnit: '1s',
    duration: __ENV.DURATION || `${config.workloads[workload].duration_seconds}s`,
    preAllocatedVUs: 10, maxVUs: 50, gracefulStop: '20s',
  } },
  maxRedirects: 0,
  tags: { evaluation: 'karthos', demo, run: runId, workload },
  thresholds: { dropped_iterations: ['count==0'], checks: ['rate==1'] },
};

function windowRecord(startedAt, endedAt) {
  const seconds = Number((__ENV.DURATION || `${config.workloads[workload].duration_seconds}s`).replace(/s$/, ''));
  const rate = Number(__ENV.RATE || 2);
  return { run_id: runId, generator: 'k6', started_at: startedAt,
    ended_at: endedAt, intended_duration_seconds: seconds,
    target_iterations_per_second: rate, expected_iterations: seconds * rate };
}

export function setup() {
  const startedAt = new Date().toISOString();
  console.log('EVAL_WINDOW ' + JSON.stringify(windowRecord(startedAt, null)));
  return { startedAt };
}

export function teardown(data) {
  console.log('EVAL_WINDOW ' + JSON.stringify(windowRecord(data.startedAt, new Date().toISOString())));
}

function expand(value, user) {
  if (typeof value === 'string') return value.split('{user}').join(user);
  if (Array.isArray(value)) return value.map((v) => expand(v, user));
  if (value !== null && typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value)) result[key] = expand(value[key], user);
    return result;
  }
  return value;
}

function outcomeOK(step, response) {
  if (response.status !== step.expected_status) return false;
  const text = response.body || '';
  if (step.outcome === 'status') return true;
  if (step.outcome === 'boutique_order') return text.includes('Your order is complete!');
  if (step.outcome === 'expired') return text.toLowerCase().includes('expired') && text.toLowerCase().includes('failed to complete the order');
  let data;
  try { data = JSON.parse(text); } catch (_) { return false; }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  if (step.outcome === 'payment_failed') return data.code === 'PAYMENT_FAILED';
  if (step.outcome === 'cart_item') return (data.items || []).some((i) => i.productId === config.product_id && i.quantity === 1);
  if (step.outcome === 'astronomy_order') return Boolean(data.orderId) && (data.items || []).some((i) => i.item && i.item.productId === config.product_id && i.item.quantity === 1);
  return false;
}

export default function () {
  const n = exec.scenario.iterationInTest;
  const user = `${userPrefix}-${n}`;
  const journeyId = `${runId}:${n}`;
  // Explicit cookie keeps Boutique cart and checkout identity the same; clear VU
  // cookie state so a previous iteration cannot override that identity.
  http.cookieJar().clear(base);
  const steps = expand(contract.journeys[schedule[n % schedule.length]], user);
  for (let index = 0; index < steps.length; index++) {
    const step = steps[index];
    const requestId = `${journeyId}:${index}`;
    const headers = Object.assign({}, expand(contract.headers, user), {
      'X-Evaluation-Run': runId, 'X-Evaluation-Request-ID': requestId,
    });
    const traceId = __ENV.EVAL_CORRELATE_TRACES === '1' ? crypto.sha256(requestId, 'hex').slice(0, 32) : null;
    if (traceId) headers.traceparent = `00-${traceId}-${crypto.sha256(requestId + ':client', 'hex').slice(0, 16)}-01`;
    let body = step.body;
    if (body !== null) {
      if (contract.encoding === 'form') {
        headers['Content-Type'] = 'application/x-www-form-urlencoded';
        body = Object.entries(body).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v).replace(/%20/g, '+')}`).join('&');
      } else {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(body);
      }
    }
    const started = new Date();
    console.log('EVAL_ATTEMPT ' + JSON.stringify({ request_id: requestId,
      run_id: runId, generator: 'k6', route: step.route, journey_id: journeyId,
      started_at: started.toISOString(), trace_id: traceId }));
    let response = null;
    let exception = null;
    let ok = false;
    try {
      response = http.request(step.method, base + step.path, body, {
        headers, tags: { route: step.route, journey_id: journeyId, request_id: requestId },
        timeout: '15s', redirects: 0,
        responseCallback: http.expectedStatuses(step.expected_status),
      });
      exception = response.error || null;
      ok = outcomeOK(step, response);
      check(response, { 'expected status and outcome': () => ok }, { route: step.route });
    } catch (error) {
      exception = String(error);
      check(null, { 'expected status and outcome': () => false }, { route: step.route });
    } finally {
      const completed = new Date();
      console.log('EVAL_REQUEST ' + JSON.stringify({
        request_id: requestId, run_id: runId, generator: 'k6', route: step.route,
        journey_id: journeyId, iteration: n, step_index: index, trace_id: traceId,
        started_at: started.toISOString(), completed_at: completed.toISOString(),
        duration_ms: completed - started, status: response ? response.status : 0,
        exception, expected_status: step.expected_status, outcome_ok: ok,
        attempted: true, completed: Boolean(response && response.status > 0),
      }));
    }
  }
}

export function handleSummary(data) {
  if (__ENV.EVAL_ARTIFACT_MODE === '1') {
    const directory = __ENV.EVAL_ARTIFACT_DIR || '/tmp/eval-artifacts';
    return { [directory + '/k6-summary.json']: JSON.stringify(data) + '\n' };
  }
  return { stdout: '__EVAL_K6_SUMMARY_BEGIN__\n' + JSON.stringify(data) + '\n__EVAL_K6_SUMMARY_END__\n' };
}
