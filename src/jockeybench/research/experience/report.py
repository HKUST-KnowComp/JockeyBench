"""Experience protocol offline report and deterministic frame replay checks."""
import json,gzip
from pathlib import Path
from jockeybench.canonical import digest, file_hash
from jockeybench.clock import release
from jockeybench.errors import Rejected
from jockeybench.config import Config
from jockeybench.audit.replay import replay
from jockeybench.research.llm.runner import write_json
from jockeybench.research.adaptive.model import model_cost
from jockeybench.research.common_scoring import checkpoint_components
from .frame import extract,make_frame,neutral,PROTOCOL
from .model import verify_semantics

def build_report(out):
 out=Path(out);m=json.loads((out/'A2-manifest.json').read_text());cfg=Config.load(out/'A2-config.json')
 states=[];errors=[];raw_records=[];api=0.;unknown=0;calls=0;booked=0.
 for path in sorted(Path(cfg.managed_root).glob('A2-experience-*/checkpoint.json.gz')):
  s=json.loads(gzip.decompress(path.read_bytes()))
  if s['checkpoint_digest']!=digest({k:v for k,v in s.items() if k!='checkpoint_digest'}):raise Rejected('Checkpoint digest changed')
  states.append(s);api+=model_cost(s)['observed_or_estimated_usd'];unknown+=model_cost(s)['unknown_attempts']
  calls+=len(s.get('llm_trajectory',[]))
  try:
   replay(cfg,s);verify_semantics(s)
   if s['completed']:
    day=s['schedule'][0]['day'];race=s['schedule'][0]['races'][0]
    raw_records.append(extract(s,day,race,release(day)))
    booked+=checkpoint_components(s)['common_reward_booked']
  except Exception as exc:errors.append({'run_id':s['run_id'],'error':type(exc).__name__})
 history=[];previous=neutral();verified_days=[]
 events=json.loads((out/'A2-events.json').read_text()) if (out/'A2-events.json').exists() else []
 for batch in m['schedule']:
  day=batch['day'];saved=out/('A2-experience-frame-'+day+'.json')
  daily=[r for r in raw_records if r['day']==day]
  for state in states:
   if state['schedule'][0]['day']==day and state.get('memory_before')!=previous:
    errors.append({'day':day,'error':'same_day_experience_mismatch'})
  if not saved.exists():break
  try:
   rebuilt=make_frame(daily,history,day,batch['races'],release(day))
   if rebuilt!=json.loads(saved.read_text()):raise Rejected('Frame recomputation mismatch')
   es=[e for e in events if e.get('day')==day]
   gate=[e for e in es if e['kind']=='release_gate']
   releases=[e for e in es if e['kind']=='result_released']
   sealed=[e for e in es if e['kind']=='sealed']
   commit=[e for e in es if e['kind']=='experience_commit']
   if len(gate)!=1 or len(commit)!=1 or [e['race_id'] for e in sealed]!=batch['races'] or [e['race_id'] for e in releases]!=batch['races']:
    raise Rejected('Day event boundary mismatch')
   if not all(e['sequence']<gate[0]['sequence'] for e in sealed) or not all(gate[0]['sequence']<e['sequence']<commit[0]['sequence'] and e['clock']==release(day) for e in releases):
    raise Rejected('Release sequence mismatch')
   history.extend(daily);previous=rebuilt;verified_days.append(day)
  except Exception as exc:errors.append({'day':day,'error':type(exc).__name__});break
 status=json.loads((out/'A2-execution-status.json').read_text())
 errors.extend(status.get('errors',[]))
 errors.extend({'changed_code':n} for n,h in m['protected'].items() if file_hash(Path(__file__).resolve().parents[2]/n)!=h)
 common=booked;cost=sum(r['weighted_research_cost'] for r in raw_records)
 result={'protocol':PROTOCOL,'attempted':len(states),'completed':len(raw_records),'verified_days':verified_days,
   'common_reward':common,'weighted_research_cost':cost,'common_net':common-cost,
   'estimated_API_USD':api,'unknown_API_attempts':unknown,'model_calls':calls,
   'daily_reflection_API_calls':0,'daily_reflection_token_cost':0,
   'audit_errors':errors,'complete':len(raw_records)==m['maximum_real_races'] and len(verified_days)==len(m['schedule']) and not errors}
 write_json(out/'A2-experience-run-summary.json',result)
 return result
