import json
import unittest
from pathlib import Path
from evaluation.gke.models import COMPLETION_V2, build_matrix, validate_config
from evaluation.gke.complement_budget import limits
from evaluation.gke.repair import validate_budget

PROFILE = 'thesis-robustness-full-v1'

class RobustnessFullTests(unittest.TestCase):
    def config(self):
        c=json.loads(Path('config/gke.complement.example.yaml').read_text())
        c['execution_profile']=PROFILE
        c['complement_budget'].update(brl_per_usd=5, prior_spending_brl=100,
            prior_spending_basis='Conservative allowance for failed complementary preparation, including accrued resources.')
        c['limits'].update(gross_limit_usd=200,launch_ceiling_usd=172.72)
        return c

    def protocol(self):
        p=json.loads(Path('protocols/thesis-completion-v2/protocol.yaml').read_text())
        p.update(execution_profile=PROFILE,combined_gross_limit_usd=200)
        return p

    def test_cli_and_original_fourteen_windows_with_history(self):
        from evaluation.gke.cli import parser
        self.assertEqual(parser().parse_args(['prepare','--id','full','--matrix-profile',PROFILE]).matrix_profile,PROFILE)
        p=self.protocol(); original=dict(p);original.pop('execution_profile')
        self.assertEqual(build_matrix(p,schedule=COMPLETION_V2),build_matrix(original,schedule=COMPLETION_V2))
        self.assertEqual(len(build_matrix(p,schedule=COMPLETION_V2)),14)

    def test_prior_spend_subtracted_before_cleanup_reserve(self):
        c=self.config(); result=limits(c)
        self.assertEqual(result['gross_limit_usd'],200)
        self.assertEqual(result['launch_ceiling_usd'],172.72)
        validate_config(c,schedule=COMPLETION_V2)
        m=dict(schedule=COMPLETION_V2,config=c,protocol_definition=self.protocol(),cost_preview=dict(gross_estimate=170))
        validate_budget(m)
        m['cost_preview']['gross_estimate']=173
        with self.assertRaises(ValueError):validate_budget(m)

    def test_missing_invalid_or_exhausted_prior_allowance_rejected(self):
        for value in (None,-1,True,float('nan'),float('inf'),1050):
            c=self.config();c['complement_budget']['prior_spending_brl']=value
            with self.subTest(value=value),self.assertRaises(ValueError):limits(c)
        c=self.config();c['complement_budget'].pop('prior_spending_brl')
        with self.assertRaises(ValueError):limits(c)
        for value in ('',None,'   ',True):
            c=self.config();c['complement_budget']['prior_spending_basis']=value
            with self.subTest(basis=value),self.assertRaises(ValueError):limits(c)

    def test_full_profile_requires_sealed_analysis(self):
        c=self.config();c.pop('evidence_pipeline')
        with self.assertRaises(ValueError):validate_config(c,schedule=COMPLETION_V2)

    def test_two_cluster_forecast_includes_rebuild_and_history(self):
        from evaluation.gke.recoverable_run import planning_forecast
        c=self.config()
        c['readiness']['phase_estimates_seconds']={k:1 for k in c['readiness']['phase_estimates_seconds']}
        f=planning_forecast(c,build_matrix(self.protocol(),schedule=COMPLETION_V2))
        self.assertEqual(f['launch_windows'],14)
        self.assertGreater(f['rebuild_seconds'],0)
        self.assertGreater(f['history_seconds'],0)

    def test_full_deadline_can_cover_reviewed_two_cluster_forecast(self):
        c=self.config();c['limits']['infrastructure_seconds']=72000
        validate_config(c,schedule=COMPLETION_V2)
        c['limits']['infrastructure_seconds']=86401
        with self.assertRaises(ValueError):validate_config(c,schedule=COMPLETION_V2)

    def test_bootstrap_handshake_budget_is_per_cluster_and_full_profile_only(self):
        from evaluation.gke.recoverable_run import planning_forecast
        c=self.config();matrix=build_matrix(self.protocol(),schedule=COMPLETION_V2)
        c['readiness']['phase_estimates_seconds']={k:1 for k in c['readiness']['phase_estimates_seconds']}
        full=planning_forecast(c,matrix)
        legacy=dict(c);legacy.pop('execution_profile')
        old=planning_forecast(legacy,matrix)
        self.assertEqual(full.get('authorization_bootstrap_seconds'),1200)
        self.assertEqual(full['seconds']-old['seconds'],1200)
        one=planning_forecast(c,[r for r in matrix if r['repeat']==1])
        self.assertEqual(one.get('authorization_bootstrap_seconds'),600)
        self.assertEqual(planning_forecast(c,[]).get('authorization_bootstrap_seconds'),0)
