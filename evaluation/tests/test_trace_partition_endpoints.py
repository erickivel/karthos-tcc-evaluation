import copy
import json
import unittest
from evaluation.tests.test_trace_settled import TraceSettledTests
from evaluation.gke.trace_settled import check


class PartitionEndpointsTests(unittest.TestCase):
    def fixture(self):
        t,v,n,s,b,a=TraceSettledTests().fixture()
        t['events']['traces'].append(dict(t['events']['traces'][0],identity='c/d',trace_id='c'*32,connector_failed=True))
        t['events']['observers'].append(dict(t['events']['observers'][0],identity='second',traceparent='00-'+'c'*32+'-'+'d'*16+'-01'))
        t['whole_capture_source_identity_coverage']={'checkout_trace_ids':['a'*32,'c'*32],'paired_trace_ids':['a'*32,'c'*32]}
        t['generator_contract']['checkout_request_ids'].append('r2')
        for rows in (n,s):
            rows.extend(dict(row,metric=dict(row['metric'],failed='true'),first=8.001,last=23.001) for row in list(rows))
        for doc,at in ((b,8.001),(a,23.001)):
            for record in doc['records']:
                key=next(iter(record['values']));labels=json.loads(key);labels['failed']='true';new=json.dumps(labels,sort_keys=True)
                record['values'][new]=record['values'][key]
                record['sample_times'][new]=at
                record['observed_at']+=.01
        return t,v,n,s,b,a

    def result(self,args):
        t,v,n,s,b,a=args
        return check(t,{'vm':{'series':s}},n,b,a,{},fixed_verification=v)

    def test_one_millisecond_is_not_a_shared_endpoint_requirement(self):
        args=self.fixture();original=copy.deepcopy(args)
        result=self.result(args)
        self.assertEqual(result['state'],'pass',result)
        self.assertEqual(result['requests']['observed'],2)
        self.assertEqual(len(result['partition_endpoints']),2)
        self.assertEqual(args,original)

    def test_per_series_wrong_endpoint_reset_and_unstable_quiet_still_rejected(self):
        for mutation in ('endpoint','reset','unstable','missing','stale'):
            args=self.fixture();t,v,n,s,b,a=args
            if mutation=='endpoint':s[-1]['last']+=.001
            elif mutation=='reset':s[-1]['resets']=1
            elif mutation=='unstable':a['stable']=False
            elif mutation=='missing':s.pop()
            else:a['records'][-1]['observed_at']+=61
            with self.subTest(mutation=mutation):self.assertEqual(self.result(args)['state'],'insufficient')

    def test_raw_parser_uses_each_partition_bounds(self):
        from evaluation.tests.test_trace_settled import TraceSettledParserTests
        from evaluation.gke.trace_settled import from_evidence
        t,v,n,s,b,a=self.fixture()
        _,_,_,_,docs=TraceSettledParserTests().evidence()
        metrics=docs['structured/gateway/raw/metrics.json'][0]['resourceMetrics'][0]['scopeMetrics'][0]['metrics']
        extra=copy.deepcopy(metrics)
        for metric in extra:
            for point in metric.get('sum',metric.get('histogram'))['dataPoints']:
                next(x for x in point['attributes'] if x['key']=='failed')['value']={'boolValue':True}
                point['timeUnixNano']=str(int(point['timeUnixNano'])+1000000)
        failed=copy.deepcopy(extra[0]);failed['name']='traces_service_graph_request_failed_total'
        metrics.extend(extra+[failed])
        vm=docs['vm-raw.ndjson'];extra=copy.deepcopy(vm)
        for row in extra:
            row['metric']['failed']='true';row['timestamps']=[x+1 for x in row['timestamps']]
        vm.extend(extra)
        result=from_evidence(t,v,list(docs),lambda name:{'quiet-before.json':b,'quiet-after.json':a}[name],lambda name:iter(docs[name]),{})
        self.assertEqual(result['state'],'pass',result)
        self.assertEqual(result['requests']['observed'],2)
        vm[-1]['timestamps'][-1]+=1
        result=from_evidence(t,v,list(docs),lambda name:{'quiet-before.json':b,'quiet-after.json':a}[name],lambda name:iter(docs[name]),{})
        self.assertEqual(result['state'],'insufficient')


class SeriesQuietEndpointsTests(unittest.TestCase):
    def test_series_bounds_require_two_fresh_stable_observations(self):
        from evaluation.gke.trace_settled import series_quiet_bounds
        docs=[{'records':[{'observed_at':x},{'observed_at':x+15}]} for x in (20,80)]
        points=[(5.001,2),(20.001,2),(65.001,9),(80.001,9)]
        bounds,proof=series_quiet_bounds(points,*docs)
        self.assertEqual(bounds,(20.001,80.001))
        self.assertEqual(len(proof),2)
        for bad in ([*points,(20.001,3)],[(5.001,1),*points[1:]],[(5.001,2),*points[2:]]):
            with self.subTest(points=bad),self.assertRaises(ValueError):series_quiet_bounds(bad,*docs)
