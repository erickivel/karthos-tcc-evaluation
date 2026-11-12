// Evaluation-only fixed schedule. Recipe bodies and outcome definitions remain
// identical to workload.json; all times below share k6's monotonic run clock.
import http from 'k6/http';
import exec from 'k6/execution';
import { check, sleep } from 'k6';
import crypto from 'k6/crypto';
import { Counter } from 'k6/metrics';

const config = JSON.parse(open('./workload.json'));
const runId = __ENV.RUN_ID;
const demo = __ENV.DEMO;
const base = __ENV.TARGET.replace(/\/$/, '');
const contract = config.demos[demo];
const offsets = [20,29,31,44,46,59,61,74,76,89,91,100];
const prefix = 'eval-' + crypto.sha256(runId, 'hex').slice(0,12);
// Fixed once-only VUs have no arrival queue. This explicit custom metric proves
// scheduling evaluation ran; validators must label it closed-model evidence.
const schedulingDrops = new Counter('probe_scheduling_drops');
export const options = {
  scenarios: { probe: { executor: 'per-vu-iterations', vus:24, iterations:1,
    maxDuration:'121s', gracefulStop:'0s' } },
  setupTimeout:'21s', teardownTimeout:'5s', maxRedirects:0,
  tags:{evaluation:'karthos',demo,run:runId,workload:'timing-probe'},
  thresholds:{checks:['rate==1'],probe_scheduling_drops:['count==0']},
};

function clock() { return exec.instance.currentTestRunDuration / 1000; }
function emit(kind,record) { console.log(kind+' '+JSON.stringify(record)); }
function windowRecord(startedAt,endedAt=null) {
  return {run_id:runId,generator:'k6',started_at:startedAt,ended_at:endedAt,
    intended_duration_seconds:120,target_iterations_per_second:24/120,
    expected_iterations:24,expected_checkouts:24,expected_probe_checkouts:24,expected_requests:72,
    offered_model:'absolute-probe-schedule',probe_protocol_version:1,probe_schedule_offsets_seconds:offsets,
    probe_mode:'timing-probe',inner_start_offset_seconds:30,inner_end_offset_seconds:90,
    planned_offsets_seconds:offsets};
}
function expand(value,user) {
  if (typeof value==='string') return value.split('{user}').join(user);
  if (Array.isArray(value)) return value.map(v=>expand(v,user));
  if (value!==null && typeof value==='object') {
    const result={}; for (const key of Object.keys(value)) result[key]=expand(value[key],user);
    return result;
  }
  return value;
}
function plan() {
  const result=[];
  for (const offset of offsets) for (const recipe of ['checkout','expired']) {
    const n=result.length, user=`${prefix}-${n}`, journeyId=`${prefix}:probe:${n}`;
    const steps=expand(contract.journeys[recipe],user);
    steps.forEach((step,index)=>{
      Object.assign(step,{headers:expand(contract.headers,user),recipe,
        phase:index===steps.length-1?'checkout':'prerequisite',planned_offset_seconds:offset,
        journey_id:journeyId,iteration:n,step_index:index,request_id:`${journeyId}:${index}`});
      if (step.body!==null) {
        const form=contract.encoding==='form';
        step.headers['Content-Type']=form?'application/x-www-form-urlencoded':'application/json';
        step.body=form?Object.entries(step.body).map(([k,v])=>`${encodeURIComponent(k)}=${encodeURIComponent(v).replace(/%20/g,'+')}`).join('&'):JSON.stringify(step.body);
      }
    });
    result.push({recipe,planned_offset_seconds:offset,steps});
  }
  return result;
}
function outcomeOK(step,response) {
  if (response.status!==step.expected_status) return false;
  const text=response.body || '';
  if (step.outcome==='status') return true;
  if (step.outcome==='boutique_order') return text.includes('Your order is complete!');
  if (step.outcome==='expired') return text.toLowerCase().includes('expired') && text.toLowerCase().includes('failed to complete the order');
  let data; try { data=JSON.parse(text); } catch (_) { return false; }
  if (!data || typeof data!=='object' || Array.isArray(data)) return false;
  if (step.outcome==='payment_failed') return data.code==='PAYMENT_FAILED';
  if (step.outcome==='cart_item') return (data.items || []).some(i=>i.productId===config.product_id && i.quantity===1);
  if (step.outcome==='astronomy_order') return Boolean(data.orderId) && (data.items || []).some(i=>i.item && i.item.productId===config.product_id && i.item.quantity===1);
  return false;
}
function requestStep(step,origin) {
  const now=clock();
  const remaining=Math.min(10,origin+120-now,step.phase==='prerequisite'?origin+20-now:10);
  if (remaining<=0) { schedulingDrops.add(1); throw new Error('probe request deadline reached'); }
  http.cookieJar().clear(base);
  const headers=Object.assign({},step.headers,{'X-Evaluation-Run':runId,'X-Evaluation-Request-ID':step.request_id});
  const traceId=__ENV.EVAL_CORRELATE_TRACES==='1'?crypto.sha256(step.request_id,'hex').slice(0,32):null;
  const traceparent=traceId?`00-${traceId}-${crypto.sha256(step.request_id+':client','hex').slice(0,16)}-01`:null;
  if (traceparent) headers.traceparent=traceparent;
  const startedAt=new Date().toISOString(), started=clock();
  const record={request_id:step.request_id,run_id:runId,generator:'k6',route:step.route,
    journey_id:step.journey_id,iteration:step.iteration,step_index:step.step_index,
    phase:step.phase,recipe:step.recipe,planned_offset_seconds:step.planned_offset_seconds,
    actual_offset_seconds:started-origin,trace_id:traceId,requested_trace_id:traceId,actual_trace_id:traceId,
    requested_traceparent:traceparent,actual_traceparent:traceparent,started_at:startedAt};
  emit('EVAL_ATTEMPT',record);
  let response=null,exception=null,ok=false;
  try {
    response=http.request(step.method,base+step.path,step.body,{headers,
      tags:{route:step.route,journey_id:step.journey_id,request_id:step.request_id},
      timeout:`${remaining}s`,redirects:0,responseCallback:http.expectedStatuses(step.expected_status)});
    exception=response.error || null;
    ok=outcomeOK(step,response);
    check(response,{'expected status and outcome':()=>ok},{route:step.route});
  } catch (error) {
    exception=String(error);
    check(null,{'expected status and outcome':()=>false},{route:step.route});
  } finally {
    emit('EVAL_REQUEST',Object.assign(record,{completed_at:new Date().toISOString(),duration_ms:(clock()-started)*1000,
      status:response?response.status:0,exception,expected_status:step.expected_status,outcome_ok:ok,
      attempted:true,completed:Boolean(response && response.status>0)}));
  }
  return ok;
}
export function setup() {
  const origin=clock(), originEpochMilliseconds=Date.now(), startedAt=new Date(originEpochMilliseconds).toISOString();
  emit('EVAL_WINDOW',Object.assign(windowRecord(startedAt),{origin_epoch_milliseconds:originEpochMilliseconds,origin_monotonic_seconds:origin}));
  schedulingDrops.add(0);
  const items=plan();
  let ready=true, reason=null;
  try {
    for (const item of items) for (const step of item.steps.slice(0,-1)) {
      if (!requestStep(step,origin)) throw new Error('prerequisite outcome failed');
    }
    if (clock()>=origin+20) throw new Error('prerequisites missed first checkout offset');
  } catch (error) {ready=false;reason=String(error);schedulingDrops.add(24);}
  return {origin,originEpochMilliseconds,startedAt,items,ready,reason};
}
export default function(data) {
  const item=data.items[exec.vu.idInTest-1];
  if (data.ready) {
    sleep(Math.max(0,data.origin+item.planned_offset_seconds-clock()));
    requestStep(item.steps[item.steps.length-1],data.origin);
  }
  sleep(Math.max(0,data.origin+120-clock()));
}
export function teardown(data) {
  emit('EVAL_WINDOW',Object.assign(windowRecord(data.startedAt,new Date().toISOString()),{origin_epoch_milliseconds:data.originEpochMilliseconds,origin_monotonic_seconds:data.origin}));
  emit('EVAL_PROBE_COMPLETE',{run_id:runId,generator:'k6',complete:data.ready,reason:data.reason,
    completed_at:new Date().toISOString(),actual_duration_seconds:(Date.now()-data.originEpochMilliseconds)/1000});
}
export function handleSummary(data) {
  if (__ENV.EVAL_ARTIFACT_MODE==='1') {
    const directory=__ENV.EVAL_ARTIFACT_DIR || '/tmp/eval-artifacts';
    return {[directory+'/k6-summary.json']:JSON.stringify(data)+'\n'};
  }
  return {stdout:'__EVAL_K6_SUMMARY_BEGIN__\n'+JSON.stringify(data)+'\n__EVAL_K6_SUMMARY_END__\n'};
}
