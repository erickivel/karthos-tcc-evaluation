import copy
import unittest
from evaluation.gke import volume_provenance as v


class VolumeProvenanceTests(unittest.TestCase):
    def point(self,uid='agent',value=30):
        return {'metric':'k8s.pod.volume.available','source':'kubelet','collector_uid':uid,'node':'n','node_uid':'nid','pod_uid':'pid','unit':'By','value':value,'dimensions':{'volume':'data'},'raw_resource':{'k8s.pod.uid':'pid'},'raw_attributes':{'karthos_source':'kubelet'},'raw_point':{'timeUnixNano':'5000000000','asInt':str(value)}}

    def test_forwarded_identity_recovers_producer_but_keeps_capture_hop(self):
        source=self.point();gateway=self.point('gateway');before=copy.deepcopy(gateway)
        index=v.source_index([source,gateway],{('agent','n')});result=v.attribute(gateway,index)
        self.assertEqual(result['state'],'matched');self.assertEqual(result['observed']['collector_uid'],'agent')
        self.assertEqual(result['observed']['capture_collector_uid'],'gateway');self.assertEqual(gateway,before)
        self.assertEqual(result['source_observation_indices'],[0])

    def test_multiple_producers_are_ambiguous_and_different_values_are_failures(self):
        source=self.point();other=self.point('other');gateway=self.point('gateway')
        index=v.source_index([source,other],{('agent','n'),('other','n')})
        self.assertEqual(v.attribute(gateway,index)['state'],'insufficient')
        index=v.source_index([source],{('agent','n')})
        self.assertEqual(v.attribute(self.point('gateway',31),index)['state'],'fail')

    def test_timestamp_dimension_and_uid_mismatches_never_choose_closest_or_largest(self):
        index=v.source_index([self.point()],{('agent','n')})
        for field,value in [('pod_uid','other'),('node_uid','other'),('dimensions',{'volume':'different'}),('raw_point',{'timeUnixNano':'6000000000','asInt':'30'})]:
            point=self.point('gateway');point[field]=value
            self.assertEqual(v.attribute(point,index)['state'],'insufficient')
