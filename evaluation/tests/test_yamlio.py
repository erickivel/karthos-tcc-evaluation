"""Shared safe configuration parsing; use the controller's installed dependency."""
import importlib.util
import json
import unittest
from unittest.mock import patch
from evaluation import yamlio

class YAMLTests(unittest.TestCase):
    def test_json_mapping_needs_no_external_parser(self):
        value={'receivers':{'otlp':{}},'service':{'pipelines':{}}}
        with patch.object(yamlio.subprocess,'run',side_effect=AssertionError('external parser')):
            self.assertEqual(yamlio.load_mapping(json.dumps(value)),value)
    @unittest.skipUnless(importlib.util.find_spec('yaml'),'requires pinned runtime PyYAML')
    def test_yaml_preserves_aliases_literals_and_environment_expressions(self):
        text='''defaults: &defaults
  endpoint: https://${env:NODE_IP}:10250
  insecure_skip_verify: true
receivers:
  kubelet_stats: *defaults
note: |
  line one
  line two
'''
        with patch.object(yamlio.subprocess,'run',side_effect=AssertionError('Ruby unavailable')):
            value=yamlio.load_mapping(text)
        self.assertEqual(value['receivers']['kubelet_stats']['endpoint'],'https://${env:NODE_IP}:10250')
        self.assertTrue(value['receivers']['kubelet_stats']['insecure_skip_verify'])
        self.assertEqual(value['note'],'line one\nline two\n')
    def test_collector_requires_exactly_one_mapping(self):
        for text in ('','null','[]','first: 1\n---\nsecond: 2\n'):
            with self.subTest(text=text),self.assertRaises(ValueError):yamlio.load_mapping(text)
    @unittest.skipUnless(importlib.util.find_spec('yaml'),'requires pinned runtime PyYAML')
    def test_unsafe_object_tags_are_rejected(self):
        with self.assertRaises(ValueError):yamlio.load_mapping('x: !!python/object/apply:builtins.print [unsafe]\n')
