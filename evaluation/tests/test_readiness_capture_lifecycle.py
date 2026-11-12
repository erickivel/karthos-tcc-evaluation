import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke.readiness_live import ProbeDriver


class CaptureLifecycleTests(unittest.TestCase):
    def test_returning_to_a_cell_after_network_uses_new_capture_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            ctx=SimpleNamespace(result=root,deadline=time.time()+10000,state={'started_at':time.time()},write=lambda *a:None)
            probe=ProbeDriver(SimpleNamespace(ctx=ctx,cell=object()))
            probe.root=root
            paths=[]
            class Capture:
                def __init__(self,cell,path,deadline,config):self.path=path
                def start(self):
                    (self.path/'structured').mkdir(parents=True,exist_ok=False)
                    paths.append(self.path)
                    return self
                def stop(self,archive=False):return {'errors':[],'cleanup':{'restored':True}}
            smoke={'id':'trace-smoke','cell':'boutique-trace','phase':'smoke','scenario':'matched'}
            network={'id':'trace-network','cell':'boutique-trace','phase':'resource','scenario':'network'}
            cpu={'id':'trace-cpu','cell':'boutique-trace','phase':'resource','scenario':'cpu-memory'}
            storage={'id':'trace-storage','cell':'boutique-trace','phase':'resource','scenario':'storage'}
            with patch('evaluation.structured_capture.RepairCapture',Capture), patch('evaluation.gke.run.capture_clocks',return_value={'valid':True}):
                probe._open(smoke,root/'cases/smoke')
                probe._open(network,root/'cases/network')
                probe._open(cpu,root/'cases/cpu')
                probe._open(storage,root/'cases/storage')
            self.assertEqual(len(paths),3)
            self.assertEqual(len(set(paths)),3)
            self.assertTrue(all((path/'structured').is_dir() for path in paths))

if __name__=='__main__':unittest.main()
