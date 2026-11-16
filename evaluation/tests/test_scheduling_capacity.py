import unittest
from evaluation.gke.scheduling_capacity import pod_cpu, capacity_report
class CapacityTests(unittest.TestCase):
 def test_restartable_init_and_overhead(self):
  c=lambda cpu,**kw:dict(resources={'requests':{'cpu':cpu}},**kw)
  p={'spec':{'containers':[c('200m')],'initContainers':[c('100m',restartPolicy='Always'),c('500m')],'overhead':{'cpu':'25m'}}}
  self.assertEqual(pod_cpu(p),625)
 def test_agent_reservation_blocks_before_helm(self):
  nodes={'items':[{'metadata':{'name':'a'},'status':{'allocatable':{'cpu':'3920m'}}}]}
  pods={'items':[{'metadata':{'name':'app','namespace':'app'},'spec':{'nodeName':'a','containers':[{'resources':{'requests':{'cpu':'3900m'}}}]}}]}
  self.assertFalse(capacity_report(nodes,pods,100)['ready'])
 def test_rollout_surge_is_not_hidden_by_steady_delta(self):
  from evaluation.gke.scheduling_capacity import rollout_capacity
  nodes={'items':[{'metadata':{'name':'n'},'status':{'allocatable':{'cpu':'1000m'}}}]}
  pod={'metadata':{'name':'p','namespace':'ns','labels':{'app':'g'}},'spec':{'nodeName':'n','containers':[{'resources':{'requests':{'cpu':'500m'}}}]}}
  work={'kind':'Deployment','metadata':{'name':'g','namespace':'ns'},'spec':{'replicas':1,'selector':{'matchLabels':{'app':'g'}},'template':{'spec':{'containers':[{'resources':{'requests':{'cpu':'600m'}}}]}}}}
  report=rollout_capacity(nodes,{'items':[pod]},[{'before_workload':work,'after_workload':work}])
  self.assertFalse(report['ready']);self.assertEqual(report['nodes'][0]['remaining_cpu_m'],-200)
 def test_additions_respect_affinity_and_taints(self):
  from evaluation.gke.scheduling_capacity import additions_capacity
  nodes={'items':[{'metadata':{'name':'a','labels':{'slot':'a'}},'status':{'allocatable':{'cpu':'100m'}}},{'metadata':{'name':'b','labels':{'slot':'b'}},'spec':{'taints':[{'key':'reserved','effect':'NoSchedule'}]},'status':{'allocatable':{'cpu':'1000m'}}}]}
  pod={'kind':'Pod','metadata':{'name':'test'},'spec':{'nodeSelector':{'slot':'a'},'containers':[{'resources':{'requests':{'cpu':'200m'}}}]}}
  self.assertFalse(additions_capacity(nodes,{'items':[]},[pod])['ready'])
  pod['spec']['nodeSelector']={'slot':'b'}
  self.assertFalse(additions_capacity(nodes,{'items':[]},[pod])['ready'])
  pod['spec']['tolerations']=[{'key':'reserved','operator':'Exists'}]
  self.assertTrue(additions_capacity(nodes,{'items':[]},[pod])['ready'])
 def test_unknown_pod_level_cpu_cannot_silently_undercount(self):
  with self.assertRaises(ValueError):pod_cpu({'spec':{'resources':{'requests':{'cpu':'2'}}}})
