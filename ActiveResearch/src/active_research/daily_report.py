"""A2 unattended evidence audit/report; no provider calls and no new evaluator."""
import gzip
import json
import math
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo
from jrdb_lh.common import digest, cutoff, release, file_hash
from jrdb_lh.config import Config
from jrdb_lh.audit import replay
from active_research.runner import PROJECT
from active_research.adaptive.runner import verify_semantics
from active_research.adaptive.domains import parse, neutral, validate_policy
from active_research.adaptive.model import PROMPT
from active_research.daily_session import verify_session
from active_research.llm.runner import write_json


def micro(value):
    return int((Decimal(str(value))*1000000).to_integral_value(rounding=ROUND_HALF_UP))


def session_accounting(results, sessions, usd_to_yen):
    """API accounting only; no protocol fees or prediction scores are recomputed."""
    known = sum(s.get('estimated_usd', 0) for s in sessions)
    unknown = sum(s.get('model_cost', {}).get('unknown_attempts', 1) for s in sessions)
    complete = all(r.get('net_B_after_model_estimate') is not None for r in results)
    return {'daily_model_usd_estimate':known, 'daily_unknown_attempts':unknown,
            'experiment_net_after_model_estimate':sum(r['net_B_after_model_estimate'] for r in results)-known*usd_to_yen
                if complete and not unknown else None}


def audit_race(out, config, result, manifest):
    path=Path(config.managed_root)/result['run_id']/'checkpoint.json.gz'
    s=json.loads(gzip.decompress(path.read_bytes()))
    assert s['checkpoint_digest']==digest({k:v for k,v in s.items() if k!='checkpoint_digest'})
    rid=s['schedule'][0]['races'][0]; day=s['schedule'][0]['day']
    replay_result=replay(config,s)
    semantics=verify_semantics(s)
    entrants=[x['number'] for x in s['active_initial']['card']['runners']]
    peers=next(b['races'] for b in manifest['schedule'] if b['day']==day)
    inputs=[]; native=[]; invalid=[]
    for t in s.get('llm_trajectory',[]):
        assert t['request']['messages'][0]['content']==PROMPT
        obs=json.loads(t['request']['messages'][1]['content'])
        if obs['mode']!='prediction': continue
        assert obs['research_policy']==s['memory_before']
        assert obs['basic_race_card']['race_id']==rid
        assert 'actual_result' not in obs and 'daily_races' not in obs
        assert obs['ledger']['settled_revenue_yen']==0
        text=json.dumps(obs)
        assert all(peer not in text for peer in peers if peer!=rid)
        inputs.append(digest(obs['research_policy']))
        message=t.get('response',{}).get('message',{})
        try:
            action=parse(message,entrants,rid)
            if action['type']=='submit':
                raw=json.loads(message['tool_calls'][0]['function']['arguments'])
                total=sum(raw['probabilities'].values())
                native.append({'status':'strict_valid' if abs(total-1)<1e-12 else 'normalized',
                               'raw_probability_sum':total,'raw':raw,'validated':action['prediction']})
        except Exception as exc:
            invalid.append({'phase':t['phase'],'error_type':type(exc).__name__})
    fees=Counter(); raw=Counter(); calls=[]
    for a in s['audit']:
        c=a.get('_cost')
        if a['phase']=='predict':
            assert a['clock']==cutoff(day)
            assert a['tool'] in {'card','training','horse_history','submit'}
            assert a['arguments']['race_id']==rid
        if a['tool']=='results': assert a['clock']==release(day)
        if not c: continue
        if a['tool'] in {'card','training','horse_history','results'} and a.get('status')=='ok':
            raw['evidence_micro']+=micro(Decimal(str(config.tariff.field_yen))*c['charged_cells'])
            raw['call_micro']+=micro(config.tariff.call_yen)
            assert c['data_micro']==micro(Decimal(str(config.tariff.field_yen))*Decimal('.5')*c['charged_cells'])
            assert c['call_micro']==micro(Decimal(str(config.tariff.call_yen))*Decimal('.5'))
            assert c['cpu_micro']==0
        for k in ['data_micro','call_micro','cpu_micro']: fees[k]+=c.get(k,0)
        assert c['total_micro']==sum(c.get(k,0) for k in ['data_micro','call_micro','cpu_micro'])
        calls.append({'tool':a['tool'],'arguments':a['arguments'],'clock':a['clock'],'cost':c})
    assert fees['cpu_micro']==0  # This direct-action track performs no charged compute.
    total=sum(fees.values())
    assert total==s['ledger']['cost_micro']
    assert sum(raw.values())*.5==total
    assert config.tariff.hit_bonus_yen==0 and config.tariff.multiplier==.5
    calc=None
    if rid in s['scores']:
        released=json.loads((out/(result['run_id']+'-released-result.json')).read_text())
        records=released['actual_result']['records']
        pred=s['submissions'][rid]['prediction']
        scratches=[x['number'] for x in records if str(x['values'].get('abnormal','')).strip().lstrip('0') in {'1','2'}]
        winners=[x['number'] for x in records if x['values'].get('finish')==1 and x['number'] not in scratches]
        active=[n for n in pred['ranking'] if n not in scratches]; n=len(active)
        mass=sum(pred['probabilities'][x] for x in active)
        probabilities={x:pred['probabilities'][x]/mass if mass else 1/n for x in active}
        reward=100*math.log(n*sum(.98*probabilities[x]+.02/n for x in winners)/len(winners))
        assert math.isclose(reward,s['scores'][rid]['score_revenue_yen'],abs_tol=1e-10)
        assert micro(reward)==s['ledger']['revenue_micro']
        assert native[-1]['validated']==pred
        assert s['submissions'][rid]['delivery']=='active_research'
        assert not s['submissions'][rid]['format_fallback']
        usd=sum(x['cost_usd'] for x in s['model_outputs'])
        net=(micro(reward)-total)/1e6
        assert math.isclose(net,result['net_score'],abs_tol=1e-9)
        if result['net_B_after_model_estimate'] is not None:
            assert math.isclose(net-usd*150,result['net_B_after_model_estimate'],abs_tol=1e-9)
        calc={'raw_cost':sum(raw.values())/1e6,'raw_breakdown_micro':dict(raw),
              'multiplier':.5,'weighted_cost':total/1e6,'weighted_breakdown_micro':dict(fees),
              'reward':reward,'B':0,'net':net,'model_usd_estimate':usd,
              'net_after_model_estimate':net-usd*150,'winners':winners,'scratches':scratches}
    return {'run_id':result['run_id'],'race_id':rid,'day':day,'runners':len(entrants),
            'replay':replay_result,'semantics':semantics,'memory_before':s['memory_before'],
            'memory_hash':digest(s['memory_before']),'actual_request_memory_hashes':inputs,
            'native_submissions':native,'invalid_actions':invalid,'salvage':False,'fallback':False,
            'cost_recalculation':calc,'calls':calls,'checkpoint':str(path),
            'daily_reflection_requests':sum(t['phase']=='reflection' for t in s.get('llm_trajectory',[])),
            'model_turns':s.get('model_turns',0),'query_count':s.get('query_count',0),
            'completed':result.get('completed',False)}


def build_report(out):
    def load(name,default):
        p=out/name
        return json.loads(p.read_text()) if p.exists() else default
    manifest=load('A2-manifest.json',{})
    status=load('A2-execution-status.json',{'errors':[{'type':'execution_not_finished'}]})
    results=load('A2-results.json',[])
    config=Config.load(out/'A2-config.json')
    if not results and not status.get('finished'):
        # Read-only recovery of interrupted checkpoints for reporting, never resume the agent.
        from types import SimpleNamespace
        from active_research.llm.runner import result_report
        settings=load('A2-provider.json',{})
        for path in sorted(Path(config.managed_root).glob('A2-daily-*/checkpoint.json.gz')):
            try:
                state=json.loads(gzip.decompress(path.read_bytes()))
                proxy=SimpleNamespace(state=state,config=config,agent=SimpleNamespace(settings=settings),
                                      storage=SimpleNamespace(path=path.parent))
                row=result_report(proxy,failure='interrupted_execution')
                row['completed']=False
                results.append(row)
            except Exception:
                results.append({'run_id':path.parent.name,'completed':False,'report_error':'checkpoint_unreadable'})
        status['total_attempts']=len(results)
    events=load('A2-events.json',[])
    rows=[]; failures=[]; days=[]
    for result in results:
        try: rows.append(audit_race(out,config,result,manifest))
        except Exception as exc:
            failures.append({'run_id':result.get('run_id'),'audit_error':type(exc).__name__})
    memory=neutral()
    daily_sessions=[json.loads(p.read_text()) for p in sorted((out/'A2-daily-sessions').glob('*/A2-session.json'))]
    for batch in manifest.get('schedule',[]):
        day=batch['day']
        try:
            group=[r for r in rows if r['day']==day]
            assert len(group)==len(batch['races'])
            assert all(r['memory_before']==memory for r in group)
            assert all(set(r['actual_request_memory_hashes'])=={digest(memory)} for r in group)
            ev=[e for e in events if e.get('day')==day]
            sealed=[e for e in ev if e['kind']=='sealed']
            gate=[e for e in ev if e['kind']=='release_gate']
            released=[e for e in ev if e['kind']=='result_released']
            ref=[e for e in ev if e['kind']=='reflection_start']
            commit=[e for e in ev if e['kind']=='memory_commit']
            assert [e['race_id'] for e in sealed]==batch['races']
            assert len(gate)==len(ref)==len(commit)==1
            assert max(e['sequence'] for e in sealed)<gate[0]['sequence']
            assert len(released)==len(batch['races'])
            assert all(gate[0]['sequence']<e['sequence']<ref[0]['sequence'] for e in released)
            assert all(e['clock']==release(day) for e in released)
            session_path=out/'A2-daily-sessions'/day/'A2-session.json'
            independent=session_path.exists()
            assert sum(r['daily_reflection_requests'] for r in group)==(0 if independent else 1)
            reflection=load('A2-daily-reflection-'+day+'.json',{})
            payload=load('A2-daily-reflection-input-'+day+'.json',{})
            assert [x['race_id'] for x in payload['daily_races']]==batch['races']
            assert reflection['input_digest']==digest(payload)
            assert reflection['old_policy']==memory
            if independent:
                session=json.loads(session_path.read_text())
                verify_session(session,payload)
                assert session['reflection']==reflection
                assert any(x['run_id']==session['run_id'] for x in daily_sessions)
            else:
                owner=next(r for r in group if r['daily_reflection_requests'])
                state=json.loads(gzip.decompress(Path(owner['checkpoint']).read_bytes()))
                request=next(t for t in state['llm_trajectory'] if t['phase']=='reflection')
                assert json.loads(request['request']['messages'][1]['content'])==payload
            validate_policy(reflection['new_policy'],batch['races'][-1])
            days.append({'day':day,'memory_before':digest(memory),'memory_after':digest(reflection['new_policy']),
                         'races':batch['races'],'reflection_count':1,'release_clock':release(day),'status':'PASS'})
            memory=reflection['new_policy']
        except Exception as exc:
            failures.append({'day':day,'lifecycle_audit_error':type(exc).__name__})
    changed=[f for f,h in manifest.get('protected',{}).items() if file_hash(PROJECT/f)!=h]
    if changed: failures.append({'protected_changes':changed})
    scan=load('A2-credential-scan.json',{})
    if scan.get('credential_scan')!='passed': failures.append({'credential_scan':'not_passed'})
    ready=bool(rows) and len(rows)==manifest.get('maximum_real_races') and len(days)==len(manifest['schedule']) and not failures and not status['errors']
    audit={'experiment':'A2','rows':rows,'days':days,'failures':failures,'execution':status,
           'protected_changes':changed,'credential_scan':scan,'ready':ready,
           'daily_sessions':daily_sessions,
           **session_accounting(results,daily_sessions,config.tariff.usd_to_yen)}
    write_json(out/'A2-independent-audit.json',audit)
    write_json(out/'A2-daily-accounting.json', {'sessions':daily_sessions,'daily_model_usd_estimate':audit['daily_model_usd_estimate'],'experiment_net_after_model_estimate':audit['experiment_net_after_model_estimate']})
    date=datetime.now(ZoneInfo('Europe/Berlin')).date().isoformat()
    title=date+'-A2-Daily-Lifecycle-and-Cost-Multiplier-Alignment-Report.md'
    lines=['# '+date+' — A2 Daily Lifecycle & Cost Multiplier Alignment Report','',
      '## 1. 本轮目标','A2 — Adaptive Active Research, 4-Tool Setting。Adaptive memory ON；daily feedback lifecycle alignment；cost multiplier 1.0 → 0.5；B remains 0。仅运行小规模验证，不是正式实验。','',
      '## 2. 修改内容','Changed：新增 `active_research/daily.py`（日级协调与异常收尾）、`daily_report.py`（独立审计和报告）、`test_daily.py`（离线时序/失败测试）；本轮配置仅调整 multiplier 和输出路径。',
      'Unchanged：原 adaptive prompt/provider/model、四工具、prediction schema、40-turn/30-minute safety、服务/ledger/scoring/cutoff/sandbox、原 visualization。受保护文件哈希变化：'+str(changed)+'。',
      '每场仍使用原独立 Run 和原 ledger/dedup；每日 reflection 使用独立 session，其 turn、deadline、provider 费用不进入最后一场。预算来自冻结的 A2-reflection-budget.json；结果读取仍记入各场原 ledger。日级 API 费用仅在 experiment aggregate 扣除一次，见 A2-daily-accounting.json。','',
      '## 3. Daily Lifecycle','day-start memory → 全部所选比赛独立 research/submit → 全部成功 seal 后统一进入 20:30 JST release → 一次 daily reflection → 次日 memory。任一比赛失败则当天不释放；reflection 无效则停止，不补造 memory。20:30 为 benchmark 虚拟时钟，不是等待真实当地时间。',
      '支持任意多日、多场 plan；离线覆盖 10-day/324-event 调度。实际只执行下列小规模计划。独立 run 从 neutral memory 开始，禁止恢复、覆盖或自动重试整场。','',
      '## 4. Smoke Test','Provider: DeepInfra；Model: XiaomiMiMo/MiMo-V2.5-Pro。','',
      '| Date | Race ID | Race number | Runners | Complete | Model turns | Queries | Submission |',
      '|---|---|---:|---:|---|---:|---:|---|']
    entries={x['race_id']:x for x in manifest.get('races',[])}
    for r in rows:
        e=entries[r['race_id']]
        valid=r['native_submissions'][-1]['status'] if r['native_submissions'] else 'invalid'
        lines.append(f"| {r['day']} | {r['race_id']} | {e.get('race_number')} | {r['runners']} | {r['completed']} | {r['model_turns']} | {r['query_count']} | {valid} |")
    lines+=['','实际启动数：'+str(status.get('total_attempts',0))+'。已审计场数：'+str(len(rows))+'。未审计/中断项目见下方错误，不能计为 PASS。','',
      '## 5. Memory Freeze Audit','']
    for r in rows: lines.append(f"- {r['day']} / {r['race_id']}: `{r['memory_hash']}`；已核对全部 {len(r['actual_request_memory_hashes'])} 次 prediction 请求中的实际 memory。")
    lines+=['','## 6. Result Release Audit',
      '检查持久化 event 顺序：全部 sealed < release gate < 各场 result released < daily reflection。检查所有预测服务调用 clock=前一日21:00 JST、绑定当前 race、没有 results 调用；每次模型 observation 中无当天其他 race ID、无 actual_result/daily_races、settled revenue=0。原 replay 重新从固定数据库生成工具响应；semantic audit 核对 horse/domain 参数与投影摘要。',
      '这些检查验证本轮 orchestration 和既有数据边界；不构成底层历史数据采集时间完全无泄漏的证明。沿用原 modeled-release 数据语义。','',
      '## 7. Daily Reflection Audit','']
    for d in days: lines.append(f"- {d['day']}: PASS；reflection={d['reflection_count']}；release={d['release_clock']}（20:30 JST）；实际请求含全部所选 race: {', '.join(d['races'])}。")
    lines+=['','每日输入包含每场 final prediction、实际结果、score、成本、semantic query 顺序/参数/分配和停止方式；不传原始数据库或未来比赛。policy 保留既有六个权重槽位（仅四工具可调用）、≤1200字符 notes、updated_after_race=当天最后一场的 schema。','',
      '## 8. Next-Day Memory Audit','']
    for d in days: lines.append(f"- {d['day']}: `{d['memory_before']}` → daily reflection → `{d['memory_after']}`。")
    lines+=['','审计逐日验证下一日全部实际 prediction 请求与前一日 reflection 输出内容完全相同。Day 2 仅一场，未在真实模型中覆盖 Day 2 多场冻结；该分支由离线测试覆盖。','',
      '## 9. Cost Multiplier Audit','由原始 service receipts 的 charged_cells 和固定原价独立计算 raw C，再核对 weighted ledger=0.5×C；从已释放 result、sealed probabilities 独立重算 log reward，与原 scorer 和整数 ledger 比对。CPU收费=0表示本轨道没有 compute 调用。','',
      '| Race | Raw C | Weighted 0.5 C | Reward | B | Net | Model USD estimate | Net after model estimate |',
      '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        c=r['cost_recalculation']
        if c: lines.append(f"| {r['race_id']} | {c['raw_cost']:.6f} | {c['weighted_cost']:.6f} | {c['reward']:.6f} | 0 | {c['net']:.6f} | {c['model_usd_estimate']:.9f} | {c['net_after_model_estimate']:.6f} |")
    lines+=['','Base prices unchanged: field=.01, call=.5, CPU=.2；API输入/输出配置价及 USD→score-yen=150 不变。API金额为 provider/configured estimate，不是账单确认；失败调用未知费用不会伪装成零。',
      '完整逐调用费用、参数、raw/weighted 分项、非法输出、原生/normalized submission、最终 ranking/probabilities 见 `A2-independent-audit.json`；完整请求/响应/provenance/ledger 保存在各 checkpoint（只在远程机器）。','',
      '## 10. Bug / Risk','']
    if status['errors'] or failures:
        lines+=['Major：本轮有执行或审计未通过；必须处理后才能进入正式实验。原始异常类型及位置：','```json',json.dumps({'execution':status['errors'],'audit':failures},ensure_ascii=False,indent=2),'```']
    else: lines+=['Critical/Major：本轮未检出阻止所测生命周期完成的工程错误。']
    lines+=['Observation：prediction prompt 保持不变；reflection prompt 仅将输入描述修正为 previous race day。输入覆盖可以审计，模型是否充分利用每场信息不能仅靠输入存在证明。',
      'Observation：prompt 展示的是 base service prices，实际 ledger/receipts 带 multiplier=.5。B=0 仍与 Modeling B=100 不同，本轮明确不处理，不应将当前结果声称为严格横向消融。',
      'Observation：daily batching 仅改变日级反馈协调；各场 ledger、dedup 和 provider ceiling 仍沿用既有独立单场 Run 的作用域，不等同 Modeling 的单一跨日 ledger 状态。',
      'Observation：reflection 达到独立 session 预算或返回非法 memory 时记录 daily_reflection_failed；保留已完成 race/score，停止下一日，不伪造 memory。','',
      '## 11. Readiness',
      '**Ready for larger controlled experiment（工程意义）**' if ready else '**Not ready**',
      '小规模验证不能证明不存在 bug，也未实际负载验证完整 324 场。本轮没有注册正式 cohort，没有启动完整实验；正式执行仍需下一步明确指令。','',
      'Artifacts: `A2-manifest.json`, `A2-events.json`, `A2-results.json`, `A2-independent-audit.json`, `A2-execution-status.json`, `A2-offline-tests.txt`, `A2-credential-scan.json`, daily reflection inputs/outputs, `A2-runs/*/checkpoint.json.gz`。',
      'Remote output: `'+str(out)+'`。']
    (out/title).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(out/'A2-final-status.json',{'report':title,'ready':ready,'audited_races':len(rows),
               'audit_failures':failures,'execution_errors':status['errors']})


if __name__=='__main__':
    import sys
    build_report(Path(sys.argv[1]).resolve())
