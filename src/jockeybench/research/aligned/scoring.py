"""Versioned derived evaluator. Never rewrites the trusted legacy score ledger."""
import copy
from jockeybench.errors import Rejected
from jockeybench.config import Tariff
from jockeybench.ledger import micro, reconcile
from jockeybench.research.common_scoring import from_saved as common_from_saved, research_cost

VERSION = 'common-aligned-delivery-v1'
DELIVERIES = ('direct_predictor','repaired_predictor','uniform_submit','execution_fallback','no_delivery')
ELIGIBLE = ('direct_predictor','repaired_predictor')

def is_uniform(prediction):
    values=prediction['probabilities'].values()
    # Exact equality across entrants, not an epsilon test that might hide a
    # weak but real preference. The original validator already normalizes.
    return len(set(values))==1

def from_saved(prediction,legacy_score,tariff,delivery):
    if delivery not in DELIVERIES:raise Rejected('Unknown aligned delivery')
    if delivery in ELIGIBLE and is_uniform(prediction):
        raise Rejected('Uniform prediction cannot claim predictor eligibility')
    if delivery in ('uniform_submit','execution_fallback') and not is_uniform(prediction):
        raise Rejected('Uniform delivery has non-uniform probabilities')
    row=common_from_saved(prediction,legacy_score,tariff,delivery)
    contribution=row['common_B_contribution'] if delivery in ELIGIBLE else 0.0
    reward=row['log_reward']+contribution if row['log_reward'] is not None else None
    return {**row,'evaluator':VERSION,'bonus_eligible':delivery in ELIGIBLE,
            'common_B_contribution':contribution,'winner_bonus':contribution,
            'common_reward':reward,'booked_common_reward_micro':
            micro(row['log_reward'])+micro(contribution) if reward is not None else 0}

def checkpoint_components(state):
    reconcile(state)
    tariff=Tariff(**state['binding']['config']['tariff'])
    rows=[]
    for rid,score in state['scores'].items():
        submitted=state['submissions'][rid]
        delivery=state['aligned_delivery'][rid]
        if submitted.get('delivery')!=delivery['delivery']:
            raise Rejected('Delivery provenance differs from sealed submission')
        row=from_saved(submitted['prediction'],score,tariff,delivery['delivery'])
        row.update(race_id=rid,generated_by=delivery['generated_by'])
        rows.append(row)
    cost=research_cost(state)
    reward=sum(r['booked_common_reward_micro'] for r in rows)/1e6
    return {'evaluator':VERSION,'races':rows,'cost':cost,
            'common_reward_booked':reward,'gross_reward':reward,
            'aligned_reward':reward-cost['weighted_research_cost'],
            'common_Net':reward-cost['weighted_research_cost']}
