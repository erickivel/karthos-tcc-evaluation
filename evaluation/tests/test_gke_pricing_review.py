import json,unittest
from pathlib import Path
from evaluation.gke.prepare import cost_preview
ROOT=Path(__file__).resolve().parents[2]
class PricingReviewTests(unittest.TestCase):
 def setUp(self):
  self.config=json.loads((ROOT/'config/gke.example.yaml').read_text())
  self.config['readiness']['pricing_review']={'currency':'USD','credits_assumed':0,'checked_on':'2026-11-02','detail':[{'id':'compute','quantity':60,'unit_rate_usd':.13402284},{'id':'storage','quantity':100,'unit_rate_usd':.1}],'gross_estimate':0,'items':{'incorrect':0},'references':[]}
 def test_recomputes_from_quantities_and_rates_instead_of_claimed_total(self):
  r=cost_preview(self.config)
  self.assertEqual(r['gross_estimate'],18.04)
  self.assertEqual(r['items']['storage'],10)
  self.assertEqual(r['launch_ceiling'],80)
 def test_large_review_is_not_replaced_by_the_old_smaller_estimate(self):
  self.config['readiness']['pricing_review']['detail'][0]['quantity']=1000
  self.assertGreater(cost_preview(self.config)['gross_estimate'],80)
 def test_rejects_invalid_values_currency_and_credit_subtraction(self):
  for value in (-1,float('nan'),float('inf')):
   self.config['readiness']['pricing_review']['detail'][0]['quantity']=value
   with self.assertRaises(ValueError):cost_preview(self.config)
  self.setUp();self.config['readiness']['pricing_review']['credits_assumed']=1
  with self.assertRaises(ValueError):cost_preview(self.config)
  self.setUp();self.config['readiness']['pricing_review']['currency']='BRL'
  with self.assertRaises(ValueError):cost_preview(self.config)
