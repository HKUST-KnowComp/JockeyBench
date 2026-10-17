"""Fixed reference programs R0-R3: evaluator baselines whose formulas are never shown to agents.

* R0 saves nothing: every race falls back to the uniform forecast.
* R1 fits a regularised conditional logit on card features of initial-support races.
* R2 adds each runner's last five labelled starts (``form``).
* R3 adds the objective-v5 fields and jockey/trainer ``connections``.

The fit runs once, in the sandbox, during warmup; reflection never changes it.
"""

from ..data.fields import CHA, CONNECTIONS, CYB, KKA, KYI, LABELS, PEDIGREE, RACE

# The program saved as strategy.py. It is executed verbatim in the sandbox.
PROGRAM = '''import json, math
import numpy as np
from pathlib import Path
PARAMS = Path('parameters.json')
def matrix(card, form=None):
    hist = {r['number']: r['starts'] for r in (form or {}).get('runners', [])}
    rows = []
    for r in card['runners']:
        f, t = r['facts'], r['training']
        values = [f.get(k) for k in ['earned_prize','class_prize','carried_weight_tenths_kg','layoff_weeks','age_days','draw','starts_since_stabling']]
        for i in [0,1]:
            values[i] = math.log1p(max(0, values[i])) if values[i] is not None else None
        values += [t.get('early_time'),t.get('late_time')]
        if form is not None:
            starts = hist.get(r['number'], [])
            finishes = [s['values']['finish'] for s in starts if s['values']['finish'] is not None and s['values']['finish'] > 0]
            values += [sum(finishes)/len(finishes) if finishes else None,
                       min(finishes) if finishes else None,
                       sum(x==1 for x in finishes)/len(finishes) if finishes else None,
                       sum(x<=3 for x in finishes)/len(finishes) if finishes else None,
                       len(finishes)]
        rows.append(values)
    x = np.array([[np.nan if v is None else v for v in r] for r in rows],dtype=float)
    missing = np.isnan(x).astype(float)
    present = np.isfinite(x)
    count = present.sum(axis=0)
    mean = np.nansum(x,axis=0)/np.maximum(count,1)
    centered = np.where(present,x-mean,0)
    sd = np.sqrt((centered**2).sum(axis=0)/np.maximum(count,1))
    z = centered/np.where(sd>0,sd,1)
    return np.column_stack([z,missing-missing.mean(axis=0)])
def predict(sdk, context):
    params = json.loads(PARAMS.read_text())
    card = sdk.call('card',race_id=context['race_id'])
    form = sdk.call('form',race_id=context['race_id'],k=5) if params['form'] else None
    x = matrix(card,form)
    score = x @ np.array(params['weights'])
    p = np.exp(score-score.max());p /= p.sum()
    probs = {r['number']:float(p[i]) for i,r in enumerate(card['runners'])}
    return {'ranking':sorted(probs,key=lambda n:(-probs[n],n)),'probabilities':probs}
'''

# The warmup fit, run once through ``compute``.
FIT = '''from scipy.optimize import minimize
examples = []
cursor = None
while len(examples) < TARGET:
    page = sdk.call('history',limit=1,scope='support',include_form=K,cursor=cursor)
    for e in page['examples']:
        if not e['labels']: continue
        labels = e['labels']
        y = np.array([float(labels.get(r['number'],{}).get('finish')==1) for r in e['card']['runners']])
        if y.sum(): examples.append((matrix(e['card'], e.get('form')), y/y.sum()))
    cursor = page['next_cursor']
    if cursor is None: break
if not examples: raise ValueError('No support labels')
def objective(w):
    loss,grad = 0.,np.zeros_like(w)
    for x,y in examples:
        scores = x@w;scores-=scores.max();p=np.exp(scores);p/=p.sum()
        loss -= float(y@np.log(np.maximum(p,1e-100)));grad += x.T@(p-y)
    return (loss+5*float(w@w))/len(examples),(grad+10*w)/len(examples)
fit=minimize(objective,np.zeros(examples[0][0].shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':100})
PARAMS.write_text(json.dumps({'weights':fit.x.tolist(),'form':bool(K),'training_races':len(examples)}))
result={'training_races':len(examples),'converged':bool(fit.success)}
'''

V5_FEATURES = """        def scalar(value):
            if isinstance(value, str):
                import hashlib
                return int(hashlib.sha256(value.encode()).hexdigest()[:8],16)/2**32
            return value
        values += [scalar(f.get(k)) for k in RUNNER_FIELDS]
        values += [scalar(t.get(k)) for k in WORKOUT_FIELDS]
        values += [scalar(card['facts'].get(k)) for k in RACE_FIELDS]
        for k in PAST_FIELDS:
            observed = [scalar(s['values'].get(k)) for s in hist.get(r['number'], []) if s['values'].get(k) is not None]
            values.append(sum(observed)/len(observed) if observed else None)
        conn = {x['number']: x['values'] for x in (connections or {}).get('runners', [])}.get(r['number'], {})
        values += [conn.get(k) for k in CONNECTION_FIELDS]
"""
KINDS = ('R0', 'R1', 'R2', 'R3')
DEFAULT_KIND, DEFAULT_SAMPLES = 'R0', 87


def program_v5():
    """R2's program with the reviewed v5 fields appended; same fit and regularisation."""
    additions = V5_FEATURES
    for token, fields in [
        ('RUNNER_FIELDS', KYI + KKA + PEDIGREE),
        ('WORKOUT_FIELDS', CHA + CYB),
        ('RACE_FIELDS', RACE),
        ('PAST_FIELDS', LABELS),
        ('CONNECTION_FIELDS', CONNECTIONS),
    ]:
        additions = additions.replace(token, repr(fields))
    return (
        PROGRAM.replace('def matrix(card, form=None):', 'def matrix(card, form=None, connections=None):')
        .replace('        rows.append(values)', additions + '        rows.append(values)')
        .replace('x = matrix(card,form)', "x = matrix(card,form,sdk.call('connections',race_id=context['race_id']))")
    )


class ProgramReference:
    def __init__(self, kind=DEFAULT_KIND, samples=DEFAULT_SAMPLES):
        if kind not in KINDS or type(samples) is not int or not 1 <= samples <= 200:
            raise ValueError('Reference must be R0/R1/R2/R3 with 1..200 support races')
        self.kind, self.samples = kind, samples
        self.identity = {'kind': 'sdk-conditional-logit-reference', 'reference': kind, 'samples': samples, 'version': 1}

    def warmup(self, service):
        if self.kind == 'R0':
            return
        service.call(
            'save_strategy', {'files': {'strategy.py': program_v5() if self.kind == 'R3' else PROGRAM}}, operation_id='reference-code'
        )
        k = '5' if self.kind in {'R2', 'R3'} else '0'  # past starts read per runner
        code = "exec(open('strategy.py').read())\n" + (
            FIT.replace('TARGET', str(self.samples)).replace('include_form=K', f'include_form={k}').replace('bool(K)', f'bool({k})')
        )
        if self.kind == 'R3':
            code = code.replace(
                "matrix(e['card'], e.get('form'))", "matrix(e['card'], e.get('form'), sdk.call('connections',race_id=e['card']['race_id']))"
            )
        service.call('compute', {'code': code}, operation_id='reference-fit')

    def reflect(self, service, feedback):
        # Fixed method and fixed parameters; new objective form is queried at prediction time.
        return
