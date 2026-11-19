import copy,json,unittest
from pathlib import Path
from evaluation.gke.models import build_matrix,validate_config,COMPLETION_V2
from evaluation.gke.repair import validate_budget
ROOT=Path(__file__).resolve().parents[2]
class FullBudgetTests(unittest.TestCase):
    def inputs(self):
        p=json.loads((ROOT/'protocols/thesis-completion-v2/protocol.yaml').read_text())
        c=json.loads((ROOT/'config/gke.completion-v2.example.yaml').read_text())
        p['combined_gross_limit_usd']=270
        c['limits']['gross_limit_usd']=270
        return p,c
    def test_full_matrix_270_preserves_two_clusters_and_fourteen_windows(self):
        p,c=self.inputs()
        rows=build_matrix(p,schedule=COMPLETION_V2)
        self.assertEqual(len(rows),14)
        self.assertEqual({r['repeat'] for r in rows},{1,2})
        self.assertEqual(sum(r['duration_seconds'] for r in rows),2280)
        validate_config(c,schedule=COMPLETION_V2)
    def test_budget_requires_matching_full_protocol_and_config(self):
        p,c=self.inputs()
        b={'prior_runtime_allowance_usd':180,'combined_estimate_usd':260,'gross_limit_usd':270,'shared_allowances_cumulative':True,'basis':'fixture'}
        c['readiness']['study_budget']=b
        m={'schedule':COMPLETION_V2,'config':c,'protocol_definition':p,'study_budget':b,'cost_preview':{'gross_estimate':80}}
        validate_budget(m)
        p['combined_gross_limit_usd']=200
        with self.assertRaises(ValueError):validate_budget(m)
    def test_unapproved_ceiling_stays_rejected(self):
        p,c=self.inputs();p['combined_gross_limit_usd']=271
        with self.assertRaises(ValueError):build_matrix(p,schedule=COMPLETION_V2)
