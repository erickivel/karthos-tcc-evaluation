import tempfile
import unittest
from pathlib import Path
from evaluation.gke.models import write
from evaluation.gke.thesis_comparison import _structure

class StructureScopeTests(unittest.TestCase):
    def test_only_attested_owned_helpers_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            nodes=[{'id':i,'name':i,'kind':k,'namespace':'app'} for i,k in [('app','workload'),('pod','pod'),('helper','workload'),('helper-pod','pod')]]
            nodes.append({'id':'node','name':'node','kind':'node'})
            edges=[{'source':a,'target':b,'relation':r} for a,b,r in [('app','pod','contains'),('helper','helper-pod','contains'),('pod','node','runs_on'),('helper-pod','node','runs_on')]]
            write(p/'api-nodes.json',{'data':{'nodes':nodes}});write(p/'api-edges.json',{'data':{'edges':edges}})
            write(p/'versions-after.json',{'nodes':[{'name':'node','slot':'app-a'}]})
            full=_structure(p,'app')
            doc={'kind':'Job','metadata':{'name':'helper','namespace':'app','uid':'uid','labels':{'evaluation.karthos.dev/owner':'gke-qualification-v1'}}}
            write(p/'generator-job-status.json',doc)
            scoped=_structure(p,'app')
            self.assertEqual(sum(full[0].values())-sum(scoped[0].values()),2)
            doc['metadata']['labels']={};write(p/'generator-job-status.json',doc)
            self.assertEqual(_structure(p,'app'),full)
