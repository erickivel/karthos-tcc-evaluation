import copy
import json
import unittest
from pathlib import Path

from evaluation.gke.models import COMPLETION_V2, build_matrix, validate_config, validate_schedule


class ComplementTests(unittest.TestCase):
    def test_public_cli_accepts_profile(self):
        from evaluation.gke.cli import parser
        args=parser().parse_args(['prepare','--id','complement','--matrix-profile','boutique-complement-v1'])
        self.assertEqual(args.matrix_profile,'boutique-complement-v1')

    def config(self):
        c = json.loads(Path('config/gke.completion-v2.example.yaml').read_text())
        c['execution_profile'] = 'boutique-complement-v1'
        c['complement_budget'] = dict(currency='BRL', reference_brl=800, ceiling_brl=1200,
            brl_per_usd=5, fx_margin=.1, fx_date='2026-11-19', fx_source='https://example.org/fx',
            cleanup_reserve_brl=120)
        c['limits'].update(gross_limit_usd=218.18, launch_ceiling_usd=196.36,
                           infrastructure_seconds=36000, cleanup_reserve_seconds=1800)
        return c

    def test_four_windows_in_original_second_cluster_order(self):
        p = json.loads(Path('protocols/thesis-completion-v2/protocol.yaml').read_text())
        p.update(execution_profile='boutique-complement-v1', combined_gross_limit_usd=218.18)
        rows = build_matrix(p, schedule=COMPLETION_V2)
        self.assertEqual([(r['cell'], r['phase']) for r in rows],
                         [('boutique-mesh','smoke'), ('boutique-mesh','baseline'), ('boutique-trace','smoke'), ('boutique-trace','baseline')])
        self.assertEqual({r['repeat'] for r in rows}, {1})
        self.assertTrue(all(not r['history_observation'] for r in rows))
        self.assertEqual(sum(r['duration_seconds'] for r in rows), 720)
        self.assertEqual(rows[0]['predecessor_window'], 'official-r2-smoke-boutique-mesh')
        validate_schedule(dict(schedule=COMPLETION_V2, protocol_definition=p, matrix=rows))
        p['baseline_rate'] = 3
        with self.assertRaises(ValueError): build_matrix(p, schedule=COMPLETION_V2)

    def test_brl_reference_is_not_the_launch_cutoff(self):
        from evaluation.gke.repair import validate_budget
        c = self.config()
        validate_config(c, schedule=COMPLETION_V2)
        # USD180 * BRL5.5 = BRL990, above reference but below reserved ceiling.
        m = dict(schedule=COMPLETION_V2, config=c,
                 protocol_definition=dict(execution_profile=c['execution_profile'], combined_gross_limit_usd=218.18),
                 cost_preview=dict(gross_estimate=180))
        validate_budget(m)
        m['cost_preview']['gross_estimate'] = 197
        with self.assertRaises(ValueError): validate_budget(m)

    def test_changed_ceiling_missing_fx_and_unreserved_cleanup_rejected(self):
        for field, value in [('ceiling_brl', 1300), ('fx_source', ''), ('cleanup_reserve_brl', 0), ('brl_per_usd', float('nan'))]:
            c = self.config(); c['complement_budget'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config(c, schedule=COMPLETION_V2)
        c=self.config();c['limits']['infrastructure_seconds']=64801
        with self.assertRaises(ValueError):validate_config(c,schedule=COMPLETION_V2)
