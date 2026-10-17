"""common-score-yen-v2-b100-ungated-v1: read-only standardized evaluation."""
import copy
import math
from dataclasses import replace
from jockeybench.errors import Rejected
from jockeybench.scoring import score as economic_score, sealed_revenue, validate, revenue_components
from jockeybench.config import Tariff
from jockeybench.ledger import micro, reconcile

VERSION = 'common-score-yen-v2-b100-ungated-v1'
B = 100.0
MULTIPLIER = 0.5

def compatible(tariff):
    if tariff.version!='score-yen-v2' or tariff.score_scale!=100 or tariff.smoothing!=.02:
        raise Rejected('Common evaluator requires original v2 log-score parameters')
    if tariff.multiplier!=MULTIPLIER:
        raise Rejected('Common comparison requires the fixed 0.5 cost multiplier')

def from_saved(prediction, legacy_score, tariff, delivery):
    compatible(tariff)
    pred=validate(prediction,list(prediction['ranking']))
    base={'evaluator':VERSION,'B':B,'multiplier':MULTIPLIER,'delivery':delivery,
          'prediction':copy.deepcopy(pred),'status':legacy_score['status'],
          'legacy_components':copy.deepcopy(revenue_components(legacy_score))}
    if legacy_score['status']!='ok':
        return {**base,'log_reward':None,'common_B_contribution':0.0,
                'common_reward':None,'booked_common_reward_micro':0,'scoring_outcome':None}
    fact=legacy_score['scoring_outcome']
    # Reuse trusted log-score implementation, never pretend to be a predictor.
    log=sealed_revenue(pred,fact,replace(tariff,hit_bonus_yen=0),delivery)['revenue_log_yen']
    if not math.isclose(log,base['legacy_components']['revenue_log_yen'],abs_tol=1e-9):
        raise Rejected('Saved legacy log reward mismatch')
    n=len(fact['active']);hit=fact['active'][0] in fact['winners']
    contribution=B*(int(hit)-1/n)
    return {**base,'n':n,'top1_hit':hit,'log_reward':log,'common_B_contribution':contribution,
            'common_reward':log+contribution,'booked_common_reward_micro':micro(log)+micro(contribution),
            'scoring_outcome':copy.deepcopy(fact)}

def evaluate(prediction, rows, tariff, delivery):
    legacy=economic_score(prediction,rows,tariff,delivery=delivery)
    return from_saved(validate(prediction,list(rows)),legacy,tariff,delivery)

def research_cost(state):
    tariff=Tariff(**state['binding']['config']['tariff'])
    compatible(tariff)
    categories=('execution_data','exploration_data','calls','compute')
    weighted={k:sum(e['amount_micro'] for e in state['ledger']['entries'] if e['category']==k)/1e6 for k in categories}
    total=sum(weighted.values())
    if not math.isclose(total,state['ledger']['cost_micro']/1e6,abs_tol=1e-7):
        raise Rejected('Protocol cost total mismatch')
    # Preserve refunds/reservations too. All accepted records already used multiplier .5.
    return {'weighted_components':weighted,'raw_components':{k:v/MULTIPLIER for k,v in weighted.items()},
            'raw_research_cost':total/MULTIPLIER,'weighted_research_cost':total,'multiplier':MULTIPLIER,
            'raw_cost_basis':'original net ledger receipts divided by their bound multiplier; includes reservations/refunds'}

def checkpoint_components(state):
    reconcile(state)
    tariff=Tariff(**state['binding']['config']['tariff'])
    races=[]
    for rid,score in state['scores'].items():
        sealed=state['submissions'][rid]
        row=from_saved(sealed['prediction'],score,tariff,sealed.get('delivery'))
        row.update(race_id=rid,format_fallback=bool(sealed.get('format_fallback')))
        races.append(row)
    costs=research_cost(state)
    reward=sum(r['booked_common_reward_micro'] for r in races)/1e6
    return {'evaluator':VERSION,'run_id':state['run_id'],'races':races,'cost':costs,
            'common_reward_booked':reward,'common_Net':reward-costs['weighted_research_cost'],
            'legacy_reward_booked':state['ledger']['revenue_micro']/1e6,
            'legacy_Net':(state['ledger']['revenue_micro']-state['ledger']['cost_micro'])/1e6}
