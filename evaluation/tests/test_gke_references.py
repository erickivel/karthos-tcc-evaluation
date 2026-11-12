import copy
import unittest
from types import SimpleNamespace
try:
    from evaluation.references import kubelet, host, filesystem
except ImportError:
    kubelet = host = filesystem = None

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(kubelet, 'independent reference readers missing')
    def test_kubelet_preserves_identity_dimensions_source_time_and_units(self):
        body={'node':{'nodeName':'n1'},'pods':[{'podRef':{'uid':'uid','name':'p','namespace':'ns'},'startTime':'start','containers':[{'name':'c','startTime':'restart','cpu':{'time':'measured','usageNanoCores':250},'network':{'time':'net-time','interfaces':[{'name':'eth0','rxBytes':10,'txBytes':20}]}}]}]}
        rows=kubelet.read_summary(body, node='n1', requested_at=1, received_at=2)
        cpu=next(r for r in rows if r['metric']=='cpu.usageNanoCores')
        self.assertEqual((cpu['pod_uid'],cpu['container_start_time'],cpu['measurement_timestamp'],cpu['unit']),('uid','restart','measured','nanocores'))
        self.assertEqual((cpu['acquisition_started_at'],cpu['acquisition_ended_at'],cpu['source']),(1,2,'kubelet-summary'))
        self.assertEqual({r['dimensions'].get('direction') for r in rows if r['metric']=='network.bytes'},{'receive','transmit'})
        with self.assertRaises(ValueError): kubelet.read_summary(body,node='wrong',requested_at=1,received_at=2)
    def test_bounded_adapter_reuses_acquisition_and_rejects_mismatched_node(self):
        calls=[]
        def runner(*args,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(returncode=0,stdout=b'{"node":{"nodeName":"n1"},"pods":[]}',stderr=b'')
        r=kubelet.acquire_summary('n1',runner)
        self.assertEqual(r['summary']['node']['nodeName'],'n1')
        self.assertEqual(r['acquisition_policy'],'bounded-kubelet-json2')
        self.assertTrue(calls[0]['binary'])
        with self.assertRaises(ValueError): kubelet.acquire_summary('wrong',runner)
    def test_host_parser_preserves_ticks_sectors_memory_units_direction(self):
        texts={'stat':'cpu  1 2 3 4 5 6 7 8 9 10\nbtime 10\n','meminfo':'MemTotal: 1024 kB\nMemAvailable: 512 kB\n','diskstats':'8 0 sda 1 0 20 0 2 0 30 0 0 0 0\n','net/dev':'eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n'}
        rows=host.read_proc(texts,node='n1',requested_at=1,received_at=2,boot_id='boot')
        self.assertEqual(next(r for r in rows if r['metric']=='cpu.time' and r['dimensions']['state']=='user')['unit'],'ticks')
        self.assertEqual(next(r for r in rows if r['metric']=='memory.MemTotal')['value'],1024)
        self.assertEqual(next(r for r in rows if r['metric']=='disk.sectors' and r['dimensions']['direction']=='write')['value'],30)
        self.assertEqual(next(r for r in rows if r['metric']=='network.bytes' and r['dimensions']['direction']=='transmit')['value'],200)
        self.assertTrue(all(r['boot_id']=='boot' for r in rows))
    def test_counter_rate_uses_brackets_and_rejects_resets_lifecycle_and_overlap(self):
        a=dict(node='n',metric='network.bytes',source='proc',unit='bytes',boot_id='b',dimensions={'interface':'eth0','direction':'receive'},value=100,acquisition_started_at=1,acquisition_ended_at=2)
        b=dict(a,value=140,acquisition_started_at=5,acquisition_ended_at=6)
        result=host.counter_delta(a,b)
        self.assertEqual(result['delta'],40)
        self.assertEqual(result['rate_bounds'],[8,40/3])
        for patch in ({'value':1},{'boot_id':'restart'},{'dimensions':{'interface':'eth1'}},{'acquisition_started_at':1.5}):
            self.assertEqual(host.counter_delta(a,dict(b,**patch))['state'],'insufficient')
        self.assertEqual(host.counter_delta(None,b)['state'],'insufficient')
    def test_filesystem_only_explicit_owned_mounts_and_exact_device_match(self):
        seen=[]
        def stat(path):
            seen.append(path)
            return SimpleNamespace(f_blocks=100,f_bfree=40,f_bavail=30,f_frsize=4096,f_files=20,f_ffree=10)
        rows=filesystem.acquire_owned([{'mount':'/owned','device':'8:0','owned':True}],node='n',statvfs=stat,clock=iter([1,2]).__next__)
        self.assertEqual(seen,['/owned'])
        self.assertEqual(rows[0]['value'],409600)
        self.assertEqual(filesystem.compare_static(rows[0],rows[0])['state'],'match')
        other=copy.deepcopy(rows[0]);other['dimensions']['device']='8:1'
        self.assertEqual(filesystem.compare_static(rows[0],other)['state'],'insufficient')
        with self.assertRaises(ValueError): filesystem.acquire_owned([{'mount':'/bad','device':'x','owned':False}],node='n',statvfs=stat)
    def test_counter_rejects_ambiguous_rows_and_cached_source_times(self):
        a=dict(node='n',metric='network.bytes',source='proc',unit='bytes',boot_id='b',dimensions={'interface':'eth0','direction':'receive'},value=100,acquisition_started_at=1,acquisition_ended_at=2)
        b=dict(a,value=140,acquisition_started_at=5,acquisition_ended_at=6)
        self.assertEqual(host.counter_delta([a,a],b)['state'],'insufficient')
        self.assertEqual(host.counter_delta(dict(a,measurement_timestamp=0),dict(b,measurement_timestamp=0))['state'],'insufficient')
    def test_host_acquisition_reads_only_allowlist_and_brackets(self):
        seen=[]
        def read(path):
            seen.append(path)
            return 'cpu 1 2 3 4' if path=='/proc/stat' else 'boot' if path.endswith('boot_id') else ''
        rows=host.acquire_proc(node='n',read_text=read,clock=iter([10,12]).__next__)
        self.assertEqual(set(seen),{'/proc/stat','/proc/meminfo','/proc/diskstats','/proc/net/dev','/proc/sys/kernel/random/boot_id'})
        self.assertEqual((rows[0]['acquisition_started_at'],rows[0]['acquisition_ended_at']),(10,12))
    def test_bounded_adapter_retains_failed_attempt_before_recovery(self):
        replies=iter([b'bad-json',b'{"node":{"nodeName":"n"},"pods":[]}'])
        def runner(*args,**kwargs): return SimpleNamespace(returncode=0,stdout=next(replies),stderr=b'')
        r=kubelet.acquire_summary('n',runner)
        self.assertEqual(len(r['acquisition_attempts']),2)
        self.assertTrue(r['acquisition_recovered'])
        self.assertIn('stdout_base64',r['acquisition_attempts'][0])
        self.assertEqual(r['acquisition_started_at'],r['acquisition_attempts'][1]['requested_at_ns']/1e9)
