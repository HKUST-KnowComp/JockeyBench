"""A2 experience offline statistics/leakage tests; no live provider."""
import copy,gzip,json,math
from pathlib import Path
import pytest
from jockeybench.errors import Rejected
from jockeybench.clock import release, cutoff
from jockeybench.research.runner import PROJECT
from jockeybench.research.experience.frame import *
from jockeybench.research.experience.model import validate_frame,PROMPT,EXPLANATION
from jockeybench.research.adaptive.model import PROMPT as OLD_PROMPT

DAY='2024-01-06'
OLD=PROJECT/'active_research_outputs/2026-09-28-A2-Formal-324/A2-DeepSeek-V4-324/A2-runs'

def record(rid='one',day=DAY,reward=10,seq=None):
 seq=['track_context','recent_history','recent_history','training'] if seq is None else seq
 d={}
 for area in DOMAINS:
  calls=seq.count(area)
  d[area]={'calls':calls,'raw_cost':float(calls),'known_raw_cost':float(calls),'unknown_cost_calls':0,
     'horses':min(calls,2) if area in HORSE_DOMAINS else None,
     'coverage':min(calls,2)/4 if area in HORSE_DOMAINS else None,
     'positions':[i+1 for i,x in enumerate(seq) if x==area]}
 return {'schema':SCHEMA,'day':day,'race_id':rid,'released_at':release(day),'source_digest':'synthetic',
   'active_runners':4,'queries':len(seq),'domains':d,'sequence':seq+['submit'],
   'raw_research_cost':len(seq)+2.,'weighted_research_cost':(len(seq)+2)/2,
   'research_cost_before_submit':len(seq)+1.,'horses_seen_before_submit':2,'areas_used_before_submit':len(set(seq)),
   'last_area_before_submit':seq[-1] if seq else None,'horse_coverage':.5,
   'depth_sum':3,'researched_horses':2,'queries_on_researched_horses':3,
   'validity':'strict_valid','salvaged':False,'fallback':False,
   'log_reward':reward-5,'B_contribution':5,'common_reward':reward,'winner_rank':1,'top1_hit':True,'top3_hit':True}

@pytest.fixture
def saved():
 return json.loads(gzip.decompress((OLD/'A2-daily-001/checkpoint.json.gz').read_bytes()))

def extract_saved(s):
 day=s['schedule'][0]['day'];race=s['schedule'][0]['races'][0]
 return extract(s,day,race,release(day))

def test_01_raw_saved_trace_area_stats(saved):
 r=extract_saved(saved)
 assert r['queries']==22
 assert r['domains']['track_context']['calls']==1
 assert sum(v['calls'] for v in r['domains'].values())==22
 assert r['domains']['track_context']['raw_cost']==.5

def test_02_horse_coverage_known():
 a=aggregate([record()])
 assert a['areas']['recent_history']['unique_horses_covered']==2
 assert a['areas']['recent_history']['horse_coverage_rate']==.5
 assert a['areas']['track_context']['horse_coverage_rate'] is None
 assert a['areas']['track_context']['unique_horses_covered'] is None

def test_03_breadth_depth():
 a=aggregate([record()])['overall']
 assert a['mean_horse_coverage']==.5 and a['median_horse_coverage']==.5
 assert a['mean_domains_per_researched_horse']==1.5
 assert a['mean_queries_per_researched_horse']==1.5

def test_04_transitions():
 a=aggregate([record()])['patterns']
 assert a['transition_counts']=={'track_context->recent_history':1,'recent_history->recent_history':1,'recent_history->training':1,'training->submit':1}
 assert a['transition_rates']['recent_history->training']==.5
 assert a['transition_rates']['training->submit']==1
 assert a['first_area_distribution']=={'track_context':1.}
 assert a['last_area_before_submit_distribution']=={'training':1.}

def test_05_stopping():
 s=aggregate([record()])['stopping']
 assert s=={'mean_queries_before_submit':4,'median_queries_before_submit':4,'mean_cost_before_submit':5,'mean_horses_seen_before_submit':2}

def test_06_cost(saved):
 r=extract_saved(saved)
 assert r['research_cost_before_submit']==50.28
 assert math.isclose(r['raw_research_cost'],2*saved['ledger']['cost_micro']/1e6)
 a=aggregate([record(),record('two')])
 assert a['overall']['total_research_cost']==12
 assert a['areas']['recent_history']['cost_share']==.5
 assert a['overall']['non_query_or_unattributed_raw_cost']==4

def test_07_median_split():
 rs=[record(str(i),reward=x) for i,x in enumerate([1,2,3,4])]
 a=aggregate(rs)['associations']
 assert a['median_common_reward']==2.5
 assert a['higher_n']==2 and a['lower_n']==2
 assert a['areas']['training']['higher']['mean_calls']==1
 assert a['areas']['track_context']['lower']['mean_horse_coverage'] is None

def test_08_small_and_constant_samples():
 a=aggregate([record()])['associations']
 assert a['areas']['training']['reason']=='insufficient_sample'
 assert a['areas']['training']['higher'] is None
 assert spearman([(1,2)]*7)['reason']=='insufficient_sample'
 assert spearman([(1,2)]*8)['reason']=='constant_input'
 assert spearman([(i,i) for i in range(8)])['value']==1
 assert spearman([(i,-i) for i in range(8)])['value']==-1

def test_09_rolling():
 old=[record('a')];new=[record('b','2024-01-07',20,seq=['training'])]
 f=make_frame(new,old,'2024-01-07',['b'],release('2024-01-07'))
 assert f['rolling_history']['overall']['race_count']==2
 assert f['rolling_history']['overall']['total_queries']==5
 assert f['rolling_history']['areas']['training']['calls']==2

def test_10_delta():
 old=[record('a')];new=[record('b','2024-01-07',20,seq=['training'])]
 f=make_frame(new,old,'2024-01-07',['b'],release('2024-01-07'))
 assert f['trend']['mean_queries_per_race']=={'current':1,'previous':4,'delta':-3}
 assert f['trend']['recent_history.call_share']=={'current':0.,'previous':.5,'delta':-.5}

def test_12_before_release_fail(saved):
 day=saved['schedule'][0]['day'];race=saved['schedule'][0]['races'][0]
 with pytest.raises(Rejected):extract(saved,day,race,release(day)-1)
 saved['release_ledger']={}
 with pytest.raises(Rejected):extract(saved,day,race,release(day))

def test_13_release_frame(saved):
 r=extract_saved(saved);f=make_frame([r],[],DAY,[r['race_id']],release(DAY))
 assert f['through_day']==DAY
 assert len(json.dumps(f).encode())<MAX_FRAME_BYTES
 validate_frame(f,cutoff('2024-01-07'))

def test_17_future_outcome_fail(saved):
 saved['schedule'][0]['day']='2024-01-07'
 with pytest.raises(Rejected):extract(saved,DAY,saved['schedule'][0]['races'][0],release(DAY))
 with pytest.raises(Rejected):make_frame([record()], [record('future','2024-01-07')],DAY,['one'],release(DAY))

def test_18_current_outcome_not_prediction_context():
 f=make_frame([record()],[],DAY,['one'],release(DAY))
 with pytest.raises(Rejected):validate_frame(f,cutoff(DAY))

def test_19_forbidden_domains(saved):
 saved['active_trace'][0]['action']['domain']='reference_odds'
 with pytest.raises(Rejected):extract_saved(saved)
 r=record();r['domains']['provider_signals']={}
 with pytest.raises(Rejected):make_frame([r],[],DAY,['one'],release(DAY))

def test_20_allowlisted_artifacts_only(saved):
 clean=extract_saved(saved)
 saved['extra_database']='FORBIDDEN_SECRET';saved['future_results']={'winner':'LEAK'}
 saved['scores'][saved['schedule'][0]['races'][0]]['private_market_log_gain']='SECRET_MARKET'
 # Private scorer fields are never projected; their presence does not affect statistics.
 other=extract_saved(saved)
 other.pop('source_digest');clean.pop('source_digest')
 assert other==clean
 f=make_frame([extract_saved(saved)],[],DAY,[clean['race_id']],release(DAY))
 assert 'SECRET' not in json.dumps(f) and 'private_market' not in json.dumps(f)

def test_prompt_unchanged_plus_exact_explanation():
 assert PROMPT==OLD_PROMPT+EXPLANATION

def test_no_query_transitions_or_fake_track_coverage():
 r=record(seq=[]);r.update(researched_horses=0,depth_sum=0,queries_on_researched_horses=0,horse_coverage=0)
 a=aggregate([r]);assert a['patterns']['transition_counts']=={}
 assert a['overall']['mean_domains_per_researched_horse'] is None

def test_ties_and_unscorable():
 rs=[record(str(i),reward=1) for i in range(8)]
 a=aggregate(rs)['associations'];assert a['lower_n']==0 and a['areas']['training']['higher'] is None
 rs[0]['common_reward']=None
 assert aggregate(rs)['outcomes']['unscorable_races']==1

def test_unknown_query_cost_not_zero():
 r=record();r['domains']['training'].update(raw_cost=None,known_raw_cost=0,unknown_cost_calls=1)
 a=aggregate([r]);assert a['areas']['training']['raw_cost'] is None
 assert a['areas']['training']['cost_share'] is None

def test_real_24_released_legacy_records_offline():
 rs=[extract_saved(json.loads(gzip.decompress(p.read_bytes()))) for p in sorted(OLD.glob('*/checkpoint.json.gz'))]
 assert len(rs)==24
 f=make_frame(rs,[],DAY,[r['race_id'] for r in rs],release(DAY))
 assert f['current_day']['overall']['total_queries']==436
 assert f['current_day']['overall']['strict_valid_count']==24
 assert abs(f['current_day']['outcomes']['B_contribution']-228.4037939184998)<1e-8
 assert abs(f['current_day']['overall']['weighted_research_cost']-773.45)<1e-8
