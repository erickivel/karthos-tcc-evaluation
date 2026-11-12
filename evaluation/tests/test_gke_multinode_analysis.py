"""Offline fixtures for multi-node evidence readers and required gates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from evaluation import evidence_v4 as evidence
from evaluation import timing_analysis as timing
from evaluation.tests import test_evidence_v4 as fixtures


class MultinodeAnalysisTests(unittest.TestCase):
    def fixture(self,directory):
        fixture=list(fixtures.ResourcePipelineIntegrationTests().fixture(directory));path=fixture[0]
        original=path/'structured/agent';original.rename(path/'structured/old-agent')
        original.mkdir()
        nodes=[];components={}
        for name in ('node','node2','node3'):
            uid='collector-uid' if name=='node' else 'collector-'+name
            root=original/name
            if name=='node':(path/'structured/old-agent').rename(root)
            else:
                (root/'raw').mkdir(parents=True)
                (root/'pod.json').write_text(json.dumps({'metadata':{'uid':uid},'spec':{'nodeName':name}}))
                (root/'raw/metrics.json').write_text(json.dumps({'resourceMetrics':[{'resource':{'attributes':[{'key':'k8s.node.name','value':{'stringValue':name}}]},'scopeMetrics':[{'metrics':[{'name':'k8s.node.cpu.usage','gauge':{'dataPoints':[{'timeUnixNano':'100100000000','asInt':'1'}]}}]}]}]})+'\n')
                (root/'raw/receiver-input.ndjson').write_text(json.dumps({'collector_uid':uid,'node_name':name,'path':'/stats/summary','status':200,'body':{'node':{'nodeName':name},'pods':[]}})+'\n')
            receiver=root/'raw/receiver-input.ndjson'
            row=json.loads(receiver.read_text());row['node_name']=name;receiver.write_text(json.dumps(row)+'\n')
            nodes.append({'metadata':{'name':name,'uid':'node-uid-'+name}})
            components['agent:'+name]={'collector_uid':uid,'node_name':name,'errors':[]}
            summary=copy.deepcopy(fixture[4][0]) if name=='node' else {'captured_at':100.5,'received_at':100.5,'summary':{'node':{'nodeName':name},'pods':[]}}
            summary.update(node_name=name,node_uid='node-uid-'+name)
            (path/('kubelet-'+name+'.ndjson')).write_text(json.dumps(summary)+'\n')
        (path/'structured/nodes-before.json').write_text(json.dumps({'items':nodes}))
        (path/'structured/status.json').write_text(json.dumps({'valid':True,'components':components}))
        return fixture

    def score(self,fixture):
        path,nodes,cpu,memory,_,stored,inventory=fixture
        # Caller supplies the obsolete single-node summaries path (empty).
        return evidence.resource_scores(path,nodes,cpu,memory,[],'app-ns',100,110,{},stored,inventory)

    def test_multinode_resources_use_corresponding_archive_and_independent_stream(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.score(self.fixture(d))
            self.assertEqual(result['state'],'pass',result)
            self.assertEqual(result['agent_evidence']['expected_nodes'],['node','node2','node3'])
            for check in result['checks']:
                self.assertEqual((check['node'],check['collector_uid'],check['pod_uid']),('node','collector-uid','pod-uid'))

    def test_missing_agent_or_kubelet_stream_is_incomplete(self):
        for missing in ('pod.json','kubelet'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as d:
                fixture=self.fixture(d);path=fixture[0]
                file=path/'kubelet-node2.ndjson' if missing=='kubelet' else path/'structured/agent/node2/pod.json'
                file.unlink()
                result=self.score(fixture)
                self.assertEqual(result['state'],'incomplete',result)
                self.assertTrue(result['agent_evidence']['errors'])

    def test_duplicate_collector_uid_and_mismatched_receiver_node_fail_closed(self):
        for mutation in ('uid','node'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                fixture=self.fixture(d);root=fixture[0]/'structured/agent/node2'
                file=root/'pod.json' if mutation=='uid' else root/'raw/receiver-input.ndjson'
                row=json.loads(file.read_text())
                if mutation=='uid':row['metadata']['uid']='collector-uid'
                else:row['node_name']='node'
                file.write_text(json.dumps(row)+'\n')
                result=self.score(fixture)
                self.assertEqual(result['state'],'incomplete',result)

    def test_required_gate_rejects_missing_multinode_identity_evidence(self):
        report={'manifest':{'expected_nodes':3},'independent_valid':True,
                'clock':{'supports_1s_boundary_claims':True},
                'availability_status':{'valid':True},'availability':{'state':'available'},
                'eventual_completeness':{'exact':True},'windows':{'whole':{'api_storage_parity':{'checks':{key:{'state':'pass'} for key in ('exact_request_count_api','exact_error_count_api','exact_duration_api')}}}}}
        self.assertFalse(timing.required_evidence_gate(report)['valid'])
        report['agent_evidence']={'valid':True,'expected_nodes':['n1','n2','n3'],'agents':[{'node':n,'collector_uid':'uid-'+n} for n in ('n1','n2','n3')]}
        self.assertTrue(timing.required_evidence_gate(report)['valid'])

    def test_tail_availability_retains_per_node_and_collector_uid(self):
        metric={'resourceMetrics':[{'scopeMetrics':[{'metrics':[{'name':'x','gauge':{'dataPoints':[{'timeUnixNano':'10','asInt':'1'}]}}]}]}]}
        rows=[{'source':'collector-tail','query_id':'agent:'+n+':metrics.json','node_name':n,'collector_uid':'uid-'+n,'body':json.dumps(metric)+'\n','requested_at_ns':10,'received_at_ns':20} for n in ('n1','n2')]
        result=timing.availability_intervals(rows)
        self.assertEqual({(r['node_name'],r['collector_uid']) for r in result['observations']},{('n1','uid-n1'),('n2','uid-n2')})

    def test_graph_pod_uid_cannot_be_replaced_by_equal_named_raw_series(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(d)
            fixture[1][0]['labels']['k8s.pod.uid']='replacement-pod'
            result=self.score(fixture)
            self.assertEqual(result['state'],'incomplete',result)
            self.assertTrue(any('identity' in c.get('reason','') for c in result['checks']))

    def test_wrong_node_in_independent_stream_cannot_complete_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(d);file=fixture[0]/'kubelet-node2.ndjson'
            row=json.loads(file.read_text());row['summary']['node']['nodeName']='node';file.write_text(json.dumps(row)+'\n')
            result=self.score(fixture)
            self.assertEqual(result['state'],'incomplete',result)
            self.assertIn('independent kubelet source identity invalid: node2',result['agent_evidence']['errors'])

    def test_duplicate_raw_source_identity_is_not_arbitrarily_selected(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(d);path=fixture[0]
            file=path/'structured/agent/node2/raw/metrics.json'
            file.write_bytes((path/'structured/agent/node/raw/metrics.json').read_bytes())
            result=self.score(fixture)
            self.assertEqual(result['state'],'incomplete',result)
            self.assertTrue(result['agent_evidence']['errors'])

    def test_required_agent_gate_validates_raw_metric_node_before_resource_scoring(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=self.fixture(d);path=fixture[0]
            (path/'structured/agent/node2/raw/metrics.json').write_bytes((path/'structured/agent/node/raw/metrics.json').read_bytes())
            result=evidence.agent_evidence(path)
            self.assertFalse(result['valid'],result)
