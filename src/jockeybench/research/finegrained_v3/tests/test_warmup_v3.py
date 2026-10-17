import copy,json,gzip
from unittest.mock import patch
import pytest
from jockeybench.canonical import packed, digest
from jockeybench.research.finegrained_v3 import warmup
from jockeybench.research.finegrained_v3 import warmup_context_fit as fitter
from jockeybench.research.policy_warmup.tests.test_warmup import source,profile,mod,support,caller

CODE='def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    return sdk.package()\n'
def body(sizes=(100000,100000,100000)):
    messages=[{'role':'system','content':'frozen system contract'},{'role':'user','content':'initial support task'}]
    for i,n in enumerate(sizes):messages.extend([
        {'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':'history','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':str(i),'content':'x'*n}])
    return {'messages':messages,'tools':[{'function':{'name':'history'}}],'model':'fixture'}

def test_fitter_complete_rounds_and_full_raw_audit():
    b=body();raw=copy.deepcopy(b);s={};f=fitter.fit(b,s,'warmup-1')
    assert b==raw and f['messages']==b['messages'][:2]+b['messages'][4:]
    assert packed(s['warmup_context_raw_requests'][0]['raw_request'])==packed(raw)
    assert f['tools']==raw['tools'] and len(packed(f))<262144
    assert s['warmup_context_evictions'][0]['round_digest']==digest(raw['messages'][2:4])

def test_partial_and_latest_rounds_protected():
    b=body();b['messages'][2]['tool_calls'].append({'id':'missing'})
    s={};f=fitter.fit(b,s,'warmup-1')
    assert f['messages'][2:4]==b['messages'][2:4] and f['messages'][-2:]==b['messages'][-2:]
    assert s['warmup_context_evictions'][0]['round_index']==1

def test_unfit_request_stops_without_truncation():
    b=body((300000,));s={}
    with pytest.raises(fitter.ContextLimit):fitter.fit(b,s,'warmup-1')
    assert s['warmup_context_raw_requests'][0]['raw_request']==b

def test_fresh_warmup_contract_save_accounting_and_transcript(monkeypatch,support,profile,tmp_path):
    monkeypatch.setattr(warmup,'support_types',lambda mod:lambda *args:support)
    support.projection_version='objective-v4';profile=copy.deepcopy(profile);profile['config']['projection']='objective-v4'
    steps=[('inspect_contract',{}),('validate_research_policy',{'source':CODE}),('save_research_policy',{'source':CODE}),('finish',{})]
    out=tmp_path/'v3';p=warmup.build(out,profile,caller(lambda i,s:steps[i]))
    assert p['source']==CODE and p['parent_policy_hash'] is None
    session=json.loads((out/'warmup-session.json').read_text());assert len(session['llm_trajectory'])==4
    assert 'information acquisition efficiency' in session['llm_trajectory'][0]['request']['messages'][0]['content']
    assert p['model_usage']['logical_requests']==3
    assert json.loads((out/'warmup-accounting.json').read_text())['builder_session_usage']['logical_requests']==4
    cp=json.loads(gzip.decompress((out/'warmup-runs/historical-support/checkpoint.json.gz').read_bytes()))
    assert len(cp['warmup_context_raw_requests'])==4
    transcript=json.loads((out/'warmup-unpruned-transcript.json').read_text())
    assert transcript['messages']==cp['warmup_unpruned_messages']
    assert len([m for m in transcript['messages'] if m['role']=='tool'])==4

def test_wrong_model_response_is_hard_stop(tmp_path):
    from types import SimpleNamespace
    from jockeybench.research.finegrained_v3.transport import checked_provider,MODEL
    from jockeybench.research.aligned.integrity import IntegrityFailure
    service=SimpleNamespace(state={},persist=lambda:None,path=tmp_path/'updater-session.json')
    module=SimpleNamespace(request=lambda *a:({'model':'wrong-model'},{}))
    with pytest.raises(IntegrityFailure):checked_provider(module).request(service,{}, {'model':MODEL},'r','prediction-1')
    assert service.state['model_identity_hard_stop']['observed']=='wrong-model'
    assert service.state['model_identity_hard_stop']['redacted_response']['model']=='wrong-model'
    assert service.state['pending'] is None
    assert json.loads((tmp_path/'model-identity-hard-stop.json').read_text())==service.state['model_identity_hard_stop']
    module.request=lambda *a:pytest.fail('Provider must not be called after identity failure')
    with pytest.raises(IntegrityFailure):checked_provider(module).request(service,{}, {'model':MODEL},'r2','prediction-2')
    from jockeybench.research.finegrained_v3.lifecycle import verify_frozen
    with pytest.raises(IntegrityFailure,match='durable model identity'):verify_frozen(tmp_path,{})
