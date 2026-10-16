"""Detached outcome-blind control plane; never restarts a scientific trajectory."""
import datetime
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from jrdb_lh.common import digest, file_hash, release, cutoff
from active_research.aligned.integrity import IntegrityFailure
from .policy_builder import write
from .gate import load_checkpoint, retries, run_gate
from .lifecycle import verify_frozen
from active_research.policy_amortized.semantic_json.view import audit_package


def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def invariant(name, present, required, valid=True):
    """Absence before the producer's commit is not corruption."""
    status = ('missing-when-required' if required else 'not-yet-applicable') if not present else ('valid' if valid else 'invalid')
    if status in ('missing-when-required','invalid'):
        raise IntegrityFailure(name+': '+status)
    return status


def evidence_lifecycle(s):
    from .audit_live import evidence_lifecycle as check
    return check(s)


def day_artifacts(out,days,events):
    """Frame and next policy become mandatory only after their commit events."""
    result={}
    for day,counts in days.items():
        kinds={e['kind'] for e in events if e['day']==day}
        frame_path=out/('frame-'+day+'.json');next_path=out/('next-policy-'+day+'.json')
        frame_required=bool(kinds & {'frame_commit','day_complete'}) or (out/('policy_update-'+day+'.json')).exists()
        checks={}
        checks['Frame']=invariant('Frame '+day,frame_path.exists(),frame_required)
        if frame_path.exists():
            f=json.loads(frame_path.read_text())
            invariant('Frame '+day,True,True,counts['released']==counts['planned'] and
                f.get('day')==day and f.get('release_clock')==release(day) and
                f.get('frame_digest')==digest({k:v for k,v in f.items() if k!='frame_digest'}))
        checks['next_policy']=invariant('next_policy '+day,next_path.exists(),'day_complete' in kinds)
        if next_path.exists():
            from .policy_contract import source_hash
            n=json.loads(next_path.read_text());version=n.get('policy_version','')
            records=list((out/'policies').glob('research_policy_'+version+'.json')) if version.startswith('v') and version[1:].isdigit() else []
            record=json.loads(records[0].read_text()) if len(records)==1 else {}
            invariant('next_policy '+day,True,True,frame_path.exists() and bool(record) and
                record.get('policy_hash')==n.get('policy_hash')==source_hash(record.get('source','')))
        result[day]=checks
    return result


def _inspect_snapshot(out,m):
    out=Path(out);verify_frozen(out,m)
    expected=[(b['day'],r) for b in m['schedule'] for r in b['races']]
    days={b['day']:{'planned':len(b['races']),'attempted':0,'sealed':0,'released':0} for b in m['schedule']}
    deliveries={};retry=0;contexts=0;costs=0;latest={};latest_time=None
    rows=[]
    for i,directory in enumerate(sorted((out/'runs').glob('PAR-*'))):
        if i>=len(expected) or directory.name!='PAR-'+str(i+1).zfill(3):raise IntegrityFailure('Coordinator exceeded scope/order')
        path=directory/'checkpoint.json.gz'
        if not path.exists():continue
        try:s=load_checkpoint(path)
        except Exception as exc:raise IntegrityFailure('checkpoint: invalid ('+str(exc)+')') from exc
        day,race=expected[i]
        if not s.get('trajectory_bound'):
            if s.get('audit') or s.get('submissions') or s.get('release_ledger') or s.get('scores'):
                raise IntegrityFailure('Unbound run has scientific activity')
            continue
        if s.get('schedule') and s['schedule']!=[{'day':day,'races':[race]}]:raise IntegrityFailure('Unexpected race/day')
        # Run constructor may persist its default schedule before the explicit binding.
        if not s.get('schedule'):continue
        days[day]['attempted']+=1
        sealed=bool(s.get('submissions'));released=bool(s.get('release_ledger'))
        days[day]['sealed']+=int(sealed);days[day]['released']+=int(released)
        if any(v!=release(day) for v in s.get('release_ledger',{}).values()):raise IntegrityFailure('Illegal release clock')
        if (s.get('scores') or s.get('aligned_scores')) and not released:raise IntegrityFailure('Unreleased outcome leakage')
        if any(a.get('tool')=='results' for a in s.get('audit',[])) and not released:raise IntegrityFailure('Future result access')
        for value in s.get('aligned_delivery',{}).values():
            if value.get('reason')=='hard_integrity_failure':raise IntegrityFailure('Hard integrity failure')
            key=value['delivery'];deliveries[key]=deliveries.get(key,0)+1
        evidence_checks=evidence_lifecycle(s)
        attempts=s.get('provider_attempts',[]);retry+=retries(attempts)
        contexts+=int(s.get('graceful_trigger')=='context_limit');costs+=int(bool(s.get('funding_stop')))
        latest={'day':day,'phase':s.get('phase'),'lifecycle_invariants':evidence_checks,'last_provider_state':attempts[-1]['status'] if attempts else None}
        latest_time=path.stat().st_mtime
        rows.append({'day':day,'sealed':sealed,'released':released})
    for d,v in days.items():
        if v['released'] and v['sealed']!=v['planned']:raise IntegrityFailure('Incomplete day release')
    events=json.loads((out/'events.json').read_text()) if (out/'events.json').exists() else []
    counts={};complete=[]
    for e in events:
        day=e['day']
        if day not in days:raise IntegrityFailure('Out-of-scope event')
        if e['kind']=='sealed':counts[day]=counts.get(day,0)+1
        if e['kind']=='release_gate' and (counts.get(day)!=days[day]['planned'] or e['clock']!=release(day)):
            raise IntegrityFailure('Release before all seals')
        if e['kind']=='day_complete':complete.append(day)
    gate=json.loads((out/'five-race-gate.json').read_text()) if (out/'five-race-gate.json').exists() else None
    if (len(rows)>5 or any(r['released'] for r in rows)) and not (gate and gate.get('passed')):
        raise IntegrityFailure('Race 6 or release without gate PASS')
    updates=sorted(out.glob('policy_update*.json'))
    for path in updates:
        day=path.stem.removeprefix('policy_update-')
        if day not in days or days[day]['released']!=days[day]['planned']:raise IntegrityFailure('Early updater')
        u=json.loads(path.read_text());retry+=retries(u.get('provider_attempts',[]))
        if not u.get('finished'):
            a=u.get('provider_attempts',[])
            latest={'day':day,'phase':'updater','last_provider_state':a[-1]['status'] if a else None}
    status=json.loads((out/'execution-status.json').read_text()) if (out/'execution-status.json').exists() else None
    if status and status.get('errors') and not status.get('engineering_gate_failed'):
        raise IntegrityFailure('Runner failed: '+json.dumps(status['errors']))
    artifacts=day_artifacts(out,days,events)
    return {'timestamp':now(),'day_artifact_invariants':artifacts,'attempted':sum(v['attempted'] for v in days.values()),
        'sealed':sum(v['sealed'] for v in days.values()),'released':sum(v['released'] for v in days.values()),
        'days':days,'completed_days':complete,'delivery':deliveries,'provider_retries':retry,
        'context_limit_races':contexts,'cost_safety_stop_races':costs,'latest_checkpoint_timestamp':latest_time,
        'checkpoint_age_seconds':max(0,time.time()-latest_time) if latest_time else None,
        'gate':None if gate is None else gate['passed'],'execution_status':status,**latest}


class SnapshotInFlight(RuntimeError):
    pass


def control_signature(out):
    paths=list((out/'runs').glob('PAR-*/checkpoint.json.gz'))+list(out.glob('policy_update*.json'))
    paths += [out/n for n in ('events.json','execution-status.json','five-race-gate.json') if (out/n).exists()]
    paths += list(out.glob('frame-*.json'))+list(out.glob('next-policy-*.json'))+list((out/'policies').glob('research_policy_v*.json'))
    return {str(p):(p.stat().st_mtime_ns,p.stat().st_size) for p in paths}


def inspect_snapshot(out,m):
    out=Path(out);before=control_signature(out)
    try:return _inspect_snapshot(out,m)
    except IntegrityFailure:
        # Atomic files can individually be valid while a multi-file snapshot crosses
        # seal/release/update commits. Defer only if those control files changed.
        if before!=control_signature(out):raise SnapshotInFlight('Control state advanced while sampled')
        raise


def supervise(out,interval=45):
    out=Path(out).resolve();m=json.loads((out/'manifest.json').read_text());verify_frozen(out,m)
    if not 30<=interval<=60:raise ValueError('30-60 second heartbeat required')
    if (out/'supervisor-start.json').exists() or (out/'started.json').exists():raise IntegrityFailure('No restart/resume')
    audit={'started_utc':now(),'manual_scientific_interventions':0,'manual_infrastructure_interventions':0,
        'automatic_restarts':0,'unexpected_exits':0,'poll_interval_seconds':interval,'completed':False}
    child=None;last={};anomalies=[];count=0
    try:
        # Publish supervisor identity BEFORE child can reach its gate.
        write(out/'supervisor-start.json',{**audit,'supervisor_pid':os.getpid()})
        with (out/'process.log').open('xb') as log:
            child=subprocess.Popen([sys.executable,'-B','-u','-m','active_research.finegrained_v3',str(out)],
                cwd=out.parent.parent,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        write(out/'supervisor-start.json',{**audit,'supervisor_pid':os.getpid(),'runner_pid':child.pid})
        while True:
            try:last=inspect_snapshot(out,m)
            except SnapshotInFlight:
                time.sleep(interval);continue
            count+=1
            last['process_alive']=child.poll() is None
            with (out/'supervisor-heartbeats.jsonl').open('a') as f:f.write(json.dumps(last)+'\n')
            write(out/'supervisor-latest.json',last)
            if (out/'five-race-gate-pending.json').exists() and not (out/'five-race-gate.json').exists():
                run_gate(out,m)
            for key in ('provider_retries','context_limit_races','cost_safety_stop_races'):
                if last[key] and not any(x.get('kind')==key and x.get('count')==last[key] for x in anomalies):
                    anomalies.append({'kind':key,'count':last[key],'timestamp':now(),'action':'continue unchanged protocol'})
            if child.poll() is not None:
                # The process may commit completion after the earlier snapshot.
                # Once it has exited, re-read its terminal commits before requiring them.
                last=inspect_snapshot(out,m)
                last['process_alive']=False
                status=last.get('execution_status')
                if status and status.get('engineering_gate_failed'):
                    audit['engineering_gate_failed']=True;break
                if child.returncode or not status or not status.get('finished'):
                    audit['unexpected_exits']+=1;raise IntegrityFailure('Unexpected exit; no restart')
                if not (last['sealed']==last['released']==324 and status['completed_days']==[b['day'] for b in m['schedule']]):
                    raise IntegrityFailure('Incomplete terminal trajectory')
                audit['completed']=True;break
            time.sleep(interval)
    except Exception as exc:
        anomalies.append({'kind':'hard_stop','reason':str(exc),'timestamp':now()})
        if child is not None and child.poll() is None:
            os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    audit.update(ended_utc=now(),heartbeat_count=count,anomalies=anomalies,last_snapshot=last,
                 source_snapshot_hash=file_hash(out/'frozen-run-sources.tar.gz'))
    write(out/'supervisor-audit.json',audit)
    if audit['completed']:
        try:
            if m['scientific_arm']=='frozen_research_program':
                from .frozen_report import finalize
            else:
                from .report import finalize
            finalize(out)
        except Exception as exc:
            write(out/'report-error.json',{'execution_completed':True,'type':type(exc).__name__,'error':str(exc)})
            write(out/'supervisor-finished.json',{'completed':False,'execution_completed':True,'report_error':str(exc),'ended_utc':now()})
            return 1
    elif not (out/'SemanticJSON-Light-v1-5Race-Gate-Failure-Report.md').exists():
        (out/'SemanticJSON-Light-v1-Stopped-Report.md').write_text('# Formal trajectory stopped\n\nNo restart.\n\n```json\n'+json.dumps(audit,indent=2)+'\n```\n')
    write(out/'supervisor-finished.json',{'completed':audit['completed'],'engineering_gate_failed':audit.get('engineering_gate_failed',False),'ended_utc':now()})
    return 0 if audit['completed'] else 1


if __name__=='__main__':raise SystemExit(supervise(sys.argv[1]))
