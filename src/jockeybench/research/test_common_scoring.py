import copy,math,unittest
from dataclasses import replace
from jockeybench.research.common_scoring import evaluate,research_cost
from jockeybench.config import Tariff
from jockeybench.scoring import score as economic_score
from jockeybench.errors import Rejected

class CommonTests(unittest.TestCase):
 def setUp(self):
  self.tariff=Tariff(version='score-yen-v2',multiplier=.5,hit_bonus_yen=100)
  self.pred={'ranking':['1','2','3'],'probabilities':{'1':.5,'2':.3,'3':.2}}
  self.rows={str(i):{'finish':i,'result_present':True,'abnormal':0} for i in range(1,4)}
 def test_common_equal_across_deliveries(self):
  values=[evaluate(self.pred,self.rows,self.tariff,d) for d in ['predictor','active_research','execution_fallback','silent_uniform','explicit_uniform','no_strategy']]
  self.assertEqual(len({v['common_reward'] for v in values}),1)
  self.assertAlmostEqual(values[0]['common_B_contribution'],100*(1-1/3))
  self.assertEqual(values[1]['delivery'],'active_research')
  self.assertEqual(values[1]['legacy_components']['revenue_hit_yen'],0)
 def test_legacy_unchanged(self):
  before=copy.deepcopy(self.pred)
  a=economic_score(self.pred,self.rows,self.tariff,delivery='active_research')
  evaluate(self.pred,self.rows,self.tariff,'active_research')
  self.assertEqual(a,economic_score(self.pred,self.rows,self.tariff,delivery='active_research'))
  self.assertEqual(self.pred,before)
 def test_miss_negative(self):
  self.pred['ranking']=['2','1','3']
  self.assertAlmostEqual(evaluate(self.pred,self.rows,self.tariff,'active_research')['common_B_contribution'],-100/3)
 def test_top1_uses_ranking_not_max_probability(self):
  self.pred['ranking']=['2','1','3']
  self.assertFalse(evaluate(self.pred,self.rows,self.tariff,'active_research')['top1_hit'])
 def test_scratch_changes_n_and_first_choice(self):
  self.rows['1']['abnormal']='01';self.rows['2']['finish']=1
  x=evaluate(self.pred,self.rows,self.tariff,'active_research')
  self.assertEqual(x['n'],2);self.assertEqual(x['common_B_contribution'],50)
  p=.3/(.3+.2)
  self.assertAlmostEqual(x['log_reward'],100*math.log(2*(.98*p+.02/2)))
 def test_dead_heat(self):
  self.rows['2']['finish']=1
  x=evaluate(self.pred,self.rows,self.tariff,'active_research')
  self.assertAlmostEqual(x['log_reward'],100*math.log(3*((.98*.5+.02/3)+(.98*.3+.02/3))/2))
  self.assertAlmostEqual(x['common_B_contribution'],100*(1-1/3))
 def test_missing_results(self):
  self.rows['2']['result_present']=False
  x=evaluate(self.pred,self.rows,self.tariff,'active_research')
  self.assertIsNone(x['common_reward']);self.assertEqual(x['common_B_contribution'],0)
 def test_cancelled_and_all_scratched(self):
  for r in self.rows.values():r['cancelled']=True
  self.assertEqual(evaluate(self.pred,self.rows,self.tariff,'active_research')['status'],'cancelled')
  for r in self.rows.values():r['abnormal']=2
  self.assertEqual(evaluate(self.pred,self.rows,self.tariff,'active_research')['status'],'no_valid_runners')
 def test_invalid_not_salvaged(self):
  self.pred['ranking']=['1','1','3']
  with self.assertRaises(Rejected):evaluate(self.pred,self.rows,self.tariff,'active_research')
 def test_post_scratch_zero_mass_original_uniform(self):
  self.pred['probabilities']={'1':1.,'2':0.,'3':0.}
  self.rows['1']['abnormal']=1;self.rows['2']['finish']=1
  x=evaluate(self.pred,self.rows,self.tariff,'active_research')
  self.assertAlmostEqual(x['log_reward'],0);self.assertEqual(x['common_B_contribution'],50)
 def test_weighted_cost_and_cpu_refund(self):
  from dataclasses import asdict
  s={'binding':{'config':{'tariff':asdict(self.tariff)}},
     'ledger':{'cost_micro':1750000,'entries':[
      {'category':'execution_data','amount_micro':1000000},
      {'category':'calls','amount_micro':250000},
      {'category':'compute','amount_micro':1000000},
      {'category':'compute','amount_micro':-500000}]}}
  c=research_cost(s)
  self.assertEqual(c['weighted_research_cost'],1.75)
  self.assertEqual(c['raw_research_cost'],3.5)
  with self.assertRaises(Rejected):
   evaluate(self.pred,self.rows,replace(self.tariff,multiplier=1),'active_research')
 def test_A2_legacy_B_zero_common_B_100(self):
  x=evaluate(self.pred,self.rows,replace(self.tariff,hit_bonus_yen=0),'active_research')
  self.assertEqual(x['legacy_components']['revenue_hit_yen'],0)
  self.assertAlmostEqual(x['common_B_contribution'],100*(1-1/3))
