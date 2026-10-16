"""A2 Experience Frame v1: pure projections of released, saved artifacts."""
import copy,json,math,statistics
from collections import Counter
from jrdb_lh.common import Rejected,release,cutoff,digest
from active_research.common_scoring import checkpoint_components,VERSION as EVALUATOR
from active_research.adaptive.domains import SUPPORTED,parse

PROTOCOL='A2-experience-guided-v1'
SCHEMA='A2-experience-frame-v1'
DOMAINS=tuple(SUPPORTED)
HORSE_DOMAINS=tuple(d for d in DOMAINS if d!='track_context')
MAX_FRAME_BYTES=65536
COST_CATEGORIES={'execution_data','exploration_data','calls','compute'}

def mean(values):
 v=[x for x in values if x is not None]
 return statistics.mean(v) if v else None

def ratio(a,b):return a/b if b else None

def extract(state,day,race,clock):
 """Explicit allowlist projection; no source/database/provider dependency."""
 if clock!=release(day) or state['clock']!=clock or state.get('release_ledger',{}).get(race)!=clock:
  raise Rejected('Experience requires the exact legal day release')
 if state['schedule']!=[{'day':day,'races':[race]}] or not state.get('completed'):
  raise Rejected('Experience race/day binding or completion mismatch')
 if set(state['scores'])!={race} or set(state['submissions'])!={race}:
  raise Rejected('Foreign race in saved score/submission')
 components=checkpoint_components(state)
 common=components['races'][0]
 card=state['active_initial']['card']
 if card['race_id']!=race:raise Rejected('Experience card binding mismatch')
 entrants={x['number'] for x in card['runners']}
 fact=common.get('scoring_outcome')
 active=set(fact['active']) if fact else {x['number'] for x in card['runners'] if not x.get('announced_cancelled')}
 if not active.issubset(entrants):raise Rejected('Foreign active runner')
 events=state.get('active_trace',[])
 sealed=[i for i,e in enumerate(events) if e.get('sealed')]
 if len(sealed)!=1 or sealed[0]!=len(events)-1:raise Rejected('Experience requires one final sealed submission')
 queries=[]
 for event in events:
  if event['clock']!=cutoff(day):raise Rejected('Trace clock violates cutoff')
  action=event.get('action',{})
  if action.get('type')!='query':continue
  d=action['domain']
  if d not in DOMAINS:raise Rejected('Forbidden domain in experience')
  horse=action.get('horse_number')
  if d in HORSE_DOMAINS and horse not in entrants:raise Rejected('Foreign horse in experience')
  cost=event.get('cost')
  raw=cost['total_micro']/1e6/.5 if cost else None
  queries.append({'domain':d,'horse':horse if d in HORSE_DOMAINS else None,
     'raw_cost':raw,'position':len(queries)+1,'error':bool(event.get('error')),
     'repeat':bool(event.get('repeated_action'))})
 final=events[-1];turn=final['turn']
 native=state['llm_trajectory'][turn-1]['response']['message']
 parsed=parse(native,list(entrants),race)
 prediction=state['submissions'][race]['prediction']
 if parsed.get('type')!='submit' or parsed['prediction']['ranking']!=prediction['ranking']:
  raise Rejected('Native submission differs from sealed prediction')
 if any(abs(parsed['prediction']['probabilities'][k]-prediction['probabilities'][k])>1e-12 for k in prediction['probabilities']):
  raise Rejected('Native probabilities differ from sealed prediction')
 raw_args=json.loads(native['tool_calls'][0]['function']['arguments'])
 validity='strict_valid' if abs(sum(raw_args['probabilities'].values())-1)<1e-12 else 'normalized'
 if state['submissions'][race].get('format_fallback'):validity='fallback'
 selected={q['horse'] for q in queries if q['horse'] in active}
 depth=[len({q['domain'] for q in queries if q['horse']==h}) for h in selected]
 before_micro=sum(e['amount_micro'] for e in state['ledger']['entries'] if e['category'] in COST_CATEGORIES and e['phase']=='predict')
 before_raw=before_micro/1e6/.5
 if not math.isclose(before_micro/1e6,state['prediction_phase_cost']['cost_yen'],abs_tol=1e-7):
  raise Rejected('Prediction cost snapshot differs from saved ledger')
 active_rank=fact['active'] if fact else []
 winners=fact['winners'] if fact else []
 winner_rank=min((active_rank.index(w)+1 for w in winners),default=None)
 domains={}
 for d in DOMAINS:
  qs=[q for q in queries if q['domain']==d]
  hs={q['horse'] for q in qs if q['horse'] in active}
  domains[d]={'calls':len(qs),'raw_cost':sum(q['raw_cost'] for q in qs) if all(q['raw_cost'] is not None for q in qs) else None,
     'known_raw_cost':sum(q['raw_cost'] or 0 for q in qs),'unknown_cost_calls':sum(q['raw_cost'] is None for q in qs),
     'horses':len(hs) if d in HORSE_DOMAINS else None,
     'coverage':ratio(len(hs),len(active)) if d in HORSE_DOMAINS else None,
     'positions':[q['position'] for q in qs]}
 return {'schema':SCHEMA,'day':day,'race_id':race,'released_at':clock,
   'source_digest':digest({k:state[k] for k in ('active_trace','ledger','scores','submissions','release_ledger')}),
   'active_runners':len(active),'queries':len(queries),'domains':domains,
   'sequence':[q['domain'] for q in queries]+['submit'],
   'raw_research_cost':components['cost']['raw_research_cost'],
   'weighted_research_cost':components['cost']['weighted_research_cost'],
   'research_cost_before_submit':before_raw,'horses_seen_before_submit':len(selected),
   'areas_used_before_submit':len({q['domain'] for q in queries}),
   'last_area_before_submit':queries[-1]['domain'] if queries else None,
   'horse_coverage':ratio(len(selected),len(active)),
   'depth_sum':sum(depth),'researched_horses':len(selected),
   'queries_on_researched_horses':sum(q['horse'] in selected for q in queries),
   'validity':validity,'salvaged':False,'fallback':validity=='fallback',
   'log_reward':common['log_reward'],'B_contribution':common['common_B_contribution'],
   'common_reward':common['common_reward'],'winner_rank':winner_rank,
   'top1_hit':common.get('top1_hit'),'top3_hit':winner_rank<=3 if winner_rank is not None else None}

def ranks(values):
 ordered=sorted(range(len(values)),key=lambda i:values[i]);out=[0.]*len(values);i=0
 while i<len(ordered):
  j=i+1
  while j<len(ordered) and values[ordered[j]]==values[ordered[i]]:j+=1
  for k in ordered[i:j]:out[k]=(i+j+1)/2
  i=j
 return out

def spearman(pairs):
 pairs=[p for p in pairs if None not in p];n=len(pairs)
 if n<8:return {'value':None,'n':n,'reason':'insufficient_sample'}
 x=ranks([p[0] for p in pairs]);y=ranks([p[1] for p in pairs])
 mx=mean(x);my=mean(y);den=math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
 return {'value':sum((a-mx)*(b-my) for a,b in zip(x,y))/den if den else None,'n':n,
         'reason':None if den else 'constant_input'}

def aggregate(records):
 n=len(records);queries=sum(r['queries'] for r in records);horse_total=sum(r['active_runners'] for r in records)
 researched=sum(r['researched_horses'] for r in records)
 first=Counter(r['sequence'][0] for r in records if r['queries'])
 last=Counter(r['last_area_before_submit'] for r in records if r['queries'])
 transitions=Counter()
 for r in records:transitions.update(a+'->'+b for a,b in zip(r['sequence'],r['sequence'][1:]))
 outgoing=Counter()
 for key,value in transitions.items():outgoing[key.split('->')[0]]+=value
 known_area_cost=sum(v['known_raw_cost'] for r in records for v in r['domains'].values())
 costs_complete=not any(v['unknown_cost_calls'] for r in records for v in r['domains'].values())
 areas={}
 for d in DOMAINS:
  values=[r['domains'][d] for r in records];calls=sum(v['calls'] for v in values)
  cost=sum(v['known_raw_cost'] for v in values);unknown=sum(v['unknown_cost_calls'] for v in values)
  areas[d]={'calls':calls,'call_share':ratio(calls,queries),'races_used':sum(v['calls']>0 for v in values),
   'race_usage_rate':ratio(sum(v['calls']>0 for v in values),n),'raw_cost':cost if not unknown else None,
   'known_raw_cost':cost,'unknown_cost_calls':unknown,
   'cost_share':ratio(cost,known_area_cost) if costs_complete else None,
   'unique_horses_covered':sum(v['horses'] for v in values) if d in HORSE_DOMAINS else None,
   'horse_coverage_rate':ratio(sum(v['horses'] for v in values),horse_total) if d in HORSE_DOMAINS else None,
   'horse_coverage_applicability':'race_horse_pairs' if d in HORSE_DOMAINS else 'not_applicable',
   'mean_query_position':mean([x for v in values for x in v['positions']]),
   'first_use_rate':ratio(first[d],n),'last_before_submit_rate':ratio(last[d],n)}
 scored=[r for r in records if r['common_reward'] is not None]
 median=statistics.median(r['common_reward'] for r in scored) if scored else None
 higher=[r for r in scored if r['common_reward']>=median]
 lower=[r for r in scored if r['common_reward']<median]
 sufficient=len(higher)>=2 and len(lower)>=2
 associations={}
 for d in DOMAINS:
  def group(rs):
   return {'n':len(rs),'mean_calls':mean([r['domains'][d]['calls'] for r in rs]),
    'mean_raw_area_cost':mean([r['domains'][d]['raw_cost'] for r in rs]),
    'cost_sample_n':sum(r['domains'][d]['raw_cost'] is not None for r in rs),
    'mean_horse_coverage':mean([r['domains'][d]['coverage'] for r in rs])}
  associations[d]={'higher':group(higher) if sufficient else None,'lower':group(lower) if sufficient else None,
                   'reason':None if sufficient else 'insufficient_sample'}
 corr={d+'_calls_vs_common_reward':spearman([(r['domains'][d]['calls'],r['common_reward']) for r in records]) for d in DOMAINS}
 for k in ('horse_coverage','queries','raw_research_cost'):
  corr[k+'_vs_common_reward']=spearman([(r[k],r['common_reward']) for r in records])
 overall={'race_count':n,'total_queries':queries,'mean_queries_per_race':mean([r['queries'] for r in records]),
  'median_queries_per_race':statistics.median(r['queries'] for r in records) if n else None,
  'total_research_cost':sum(r['raw_research_cost'] for r in records),
  'weighted_research_cost':sum(r['weighted_research_cost'] for r in records),
  'mean_research_cost_per_race':mean([r['raw_research_cost'] for r in records]),
  'mean_horse_coverage':mean([r['horse_coverage'] for r in records]),
  'median_horse_coverage':statistics.median(r['horse_coverage'] for r in records if r['horse_coverage'] is not None) if any(r['horse_coverage'] is not None for r in records) else None,
  'mean_domains_per_researched_horse':ratio(sum(r['depth_sum'] for r in records),researched),
  'mean_queries_per_researched_horse':ratio(sum(r['queries_on_researched_horses'] for r in records),researched),
  'strict_valid_count':sum(r['validity']=='strict_valid' for r in records),'normalized_count':sum(r['validity']=='normalized' for r in records),
  'salvage_count':sum(r['salvaged'] for r in records),'fallback_count':sum(r['fallback'] for r in records),
  'query_raw_cost_known':known_area_cost,'non_query_or_unattributed_raw_cost':sum(r['raw_research_cost'] for r in records)-known_area_cost}
 return {'measurement':{'cost_unit':'unweighted protocol score-yen; excludes model/API USD',
   'weighted_cost_multiplier':0.5,'area_cost_share_denominator':'sum of attributed four-area query costs',
   'coverage_unit':'active race-horse pairs after legally released scratches',
   'depth_mean':'pooled researched active race-horse pairs',
   'transition_rates':'conditional on preceding area, reset at each race',
   'first_last_denominator':'all races, including zero-query races',
   'winner_rank':'best predicted rank among official tied winners; observation only',
   'association_scope':'median split of this summary population; cumulative population for rolling_history'},
  'overall':overall,'areas':areas,
  'patterns':{'transition_counts':dict(sorted(transitions.items())),
   'transition_rates':{k:v/outgoing[k.split('->')[0]] for k,v in sorted(transitions.items())},
   'first_area_distribution':{k:v/n for k,v in sorted(first.items())},
   'last_area_before_submit_distribution':{k:v/n for k,v in sorted(last.items())},
   'zero_query_races':sum(r['queries']==0 for r in records)},
  'stopping':{'mean_queries_before_submit':overall['mean_queries_per_race'],'median_queries_before_submit':overall['median_queries_per_race'],
   'mean_cost_before_submit':mean([r['research_cost_before_submit'] for r in records]),
   'mean_horses_seen_before_submit':mean([r['horses_seen_before_submit'] for r in records])},
  'outcomes':{'scored_races':len(scored),'unscorable_races':n-len(scored),
   'mean_log_reward':mean([r['log_reward'] for r in scored]),'B_contribution':sum(r['B_contribution'] for r in scored),
   'mean_common_reward':mean([r['common_reward'] for r in scored]),'mean_winner_rank':mean([r['winner_rank'] for r in scored]),
   'Top1_rate':mean([int(r['top1_hit']) for r in scored]),'Top3_rate':mean([int(r['top3_hit']) for r in scored])},
  'associations':{'interpretation':'observed association, not causal contribution or true information value',
   'split':'common_reward >= median: higher; < median: lower; minimum 2 races per group',
   'median_common_reward':median,'higher_n':len(higher),'lower_n':len(lower),'areas':associations,'spearman':corr}}

def neutral():
 return {'protocol':PROTOCOL,'schema':SCHEMA,'through_day':None,'current_day':None,'rolling_history':None,'trend':{}}

def make_frame(records,history,day,races,clock):
 if clock!=release(day) or [r['race_id'] for r in records]!=races or len(set(races))!=len(races):
  raise Rejected('Experience day release/schedule mismatch')
 for r in records:
  if r['schema']!=SCHEMA or r['day']!=day or r['released_at']!=clock or set(r['domains'])!=set(DOMAINS):
   raise Rejected('Experience source version/day/domain mismatch')
 for r in history:
  if r['day']>=day or r['released_at']>cutoff(day) or set(r['domains'])!=set(DOMAINS):
   raise Rejected('Future or forbidden history')
 if len({r['race_id'] for r in history+records})!=len(history+records):
  raise Rejected('Duplicate experience race')
 current=aggregate(records);rolling=aggregate(history+records)
 prev_day=max((r['day'] for r in history),default=None)
 previous=aggregate([r for r in history if r['day']==prev_day]) if prev_day else None
 pairs={'mean_queries_per_race':('overall','mean_queries_per_race'),'mean_horse_coverage':('overall','mean_horse_coverage'),
        'mean_research_cost_per_race':('overall','mean_research_cost_per_race')}
 def delta(a,b):return {'previous':b,'current':a,'delta':a-b if a is not None and b is not None else None}
 trend={name:delta(current[a][b],previous[a][b] if previous else None) for name,(a,b) in pairs.items()}
 for d in DOMAINS:trend[d+'.call_share']=delta(current['areas'][d]['call_share'],previous['areas'][d]['call_share'] if previous else None)
 frame={'protocol':PROTOCOL,'schema':SCHEMA,'through_day':day,'current_day':current,'rolling_history':rolling,'trend':trend}
 if len(json.dumps(frame,allow_nan=False).encode())>MAX_FRAME_BYTES:raise Rejected('Experience frame exceeds versioned compact limit')
 return frame
