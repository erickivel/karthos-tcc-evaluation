import unittest
from evaluation.diagnostics import analyse_packet_probe as analyse
class PacketAnalysisTests(unittest.TestCase):
 def row(self,t,src='10.42.0.250',sport='8080',dst='127.0.0.6',dport='50000',length='0',fin='0',rst='0'):
  return {'frame.time_epoch':t,'frame.number':'1','tcp.stream':'7','ip.src':src,'tcp.srcport':sport,'ip.dst':dst,'tcp.dstport':dport,'tcp.len':length,'tcp.flags.fin':fin,'tcp.flags.reset':rst}
 def test_preserves_nanoseconds_and_node_first_close(self):
  rows=[self.row('100.100000001',length='500'),self.row('106.100000003',fin='1')]
  report=analyse.summarize_node_closes(rows,'10.42.0.250')
  self.assertEqual(report['closes'][0]['idle_nanoseconds'],6000000002)
  self.assertEqual(report['closes'][0]['initiator'],'application_endpoint')
 def test_peer_close_is_not_relabelled_as_application_close(self):
  rows=[self.row('100',length='5'),self.row('101',src='127.0.0.6',sport='50000',dst='10.42.0.250',dport='8080',fin='1'),self.row('102',fin='1')]
  report=analyse.summarize_node_closes(rows,'10.42.0.250')
  self.assertEqual(report['closes'][0]['initiator'],'local_proxy_endpoint')
  self.assertEqual(len(report['closes']),1)
 def test_absent_preceding_payload_is_insufficient_for_idle_age(self):
  report=analyse.summarize_node_closes([self.row('100',rst='1')],'10.42.0.250')
  self.assertIsNone(report['closes'][0]['idle_nanoseconds'])
 def test_other_connections_do_not_join_the_local_application_hop(self):
  self.assertEqual(analyse.summarize_node_closes([self.row('100',dst='10.42.0.251',fin='1')],'10.42.0.250')['closes'],[])

 def test_linux_tshark_boolean_flags(self):
  rows=[self.row('100.1',length='5',fin='False',rst='False'),self.row('106.1',fin='True',rst='False')]
  report=analyse.summarize_node_closes(rows,'10.42.0.250')
  self.assertEqual(report['closes'][0]['flag'],'FIN')
  self.assertEqual(report['closes'][0]['idle_nanoseconds'],6000000000)
  self.assertEqual(report['reset_frames'],0)
