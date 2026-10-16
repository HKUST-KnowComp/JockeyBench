"""Representative immutable on-disk SQLite source, real hashing and broker path."""
import copy,hashlib,json,sqlite3,time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pytest
from jrdb_lh.common import digest,Rejected
from jrdb_lh.data import stat_signature
from active_research.aligned.integrity import IntegrityFailure
from active_research.finegrained_v3.source_binding import ensure,metrics
from active_research.finegrained_v3.policy_cpu import PolicyCPU
from active_research.finegrained_v3.policy_contract import interpret,synthetic_cards,SyntheticSDK,PolicyRuntimeError
from active_research.finegrained_v3.policy_executor import EvidenceSDK
from active_research.finegrained_v3.marginal_cost import MarginalAcquisition
from .test_marginal_cost import source,config,service,active,run,prepared,clone

@pytest.fixture
def disk(prepared,tmp_path):
    # Copy tiny legal fixture to a real SQLite file; real digest, no mocked hash.
    path=tmp_path/'frozen.sqlite'
    con=sqlite3.connect(path);prepared.source.con.backup(con);con.close()
    prepared.source.con.close();prepared.source.con=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);prepared.source.con.row_factory=sqlite3.Row
    prepared.source.path=path;prepared.source.signature=stat_signature(path)
    counts={'manifest':0}
    def verify():
        if stat_signature(path)!=prepared.source.signature:raise Rejected('Source changed')
    def manifest():
        counts['manifest']+=1;verify();m={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'receipt_digest':'representative-fixture'};verify();return m
    prepared.source.verify=verify;prepared.source.manifest=manifest
    prepared.config=replace(prepared.config,source=str(path));prepared.service.config=prepared.config
    prepared.state['binding']['source']=manifest();counts['manifest']=0
    return prepared,counts


def test_real_file_multiple_queries_once_then_restore_revalidates(disk):
    run,counts=disk;a=MarginalAcquisition(run)
    for d in (1,3,5):a.call('recent_history','01',d)
    a.call('condition_stats','01');a.call('training','01');a.call('training','02')
    assert counts['manifest']==1 and metrics(run)['full_verifications']==1
    restored=clone(run);MarginalAcquisition(restored).call('recent_history','01',5)
    assert counts['manifest']==2 and restored.state['verified_source_binding']==run.state['verified_source_binding']
    assert metrics(restored)['full_verifications']==1


@pytest.mark.parametrize('damage',['file','manifest','path','projection','tariff','record','sidecar'])
def test_source_mutation_scope_or_digest_change_hard_stops(disk,damage):
    run,counts=disk;ensure(run)
    if damage=='file':
        with run.source.path.open('ab') as f:f.write(b'changed')
    elif damage=='manifest':
        run.__dict__.pop('_verified_source_epoch');run.source.manifest=lambda:{'sha256':'mismatch'}
    elif damage=='path':run.config=replace(run.config,source=str(run.source.path.parent/'other.sqlite'))
    elif damage=='projection':run.config=replace(run.config,projection='objective-v4')
    elif damage=='tariff':run.config=replace(run.config,tariff=replace(run.config.tariff,multiplier=1))
    elif damage=='record':run.state['verified_source_binding']['config_digest']='corrupt'
    else:Path(str(run.source.path)+'.anonymization.json').write_text('{}')
    with pytest.raises(IntegrityFailure):MarginalAcquisition(run)


def test_slow_real_hash_and_complete_sdk_infrastructure_are_cpu_excluded(disk):
    run,counts=disk;run.state.update(active_trace=[],semantic_provenance=[],query_count=0)
    original=run.source.manifest;fake=[0.]
    def slow_manifest():
        value=original();fake[0]+=35.;return value
    run.source.manifest=slow_manifest
    sdk=EvidenceSDK(run)
    code='def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    sdk.call("recent_history", horse_number="01", depth=1)\n    sdk.call("recent_history", horse_number="01", depth=3)\n    sdk.call("condition_stats", horse_number="01")\n    return sdk.package()'
    original_call=sdk.call
    def costly_call(*a,**k):
        value=original_call(*a,**k);fake[0]+=40.;return value
    sdk.call=costly_call
    with patch('time.process_time',side_effect=lambda:fake[0]):interpret(code,run.state['active_initial']['card'],sdk,30)
    assert counts['manifest']==1
    assert sdk.interpreter_timing['trusted_cpu_seconds']==195.
    assert sdk.interpreter_timing['policy_cpu_seconds']==0 and sdk.interpreter_timing['limit_seconds']==30
    assert sdk.interpreter_timing['interpreter_steps']>0


def test_genuine_policy_computation_still_trips_original_30_cpu_seconds():
    card=synthetic_cards()[1];sdk=SyntheticSDK(card);clock=[0.]
    code='def collect_evidence(sdk, context):\n    x = 0\n    for i in range(64):\n        x = x + 1\n    sdk.call("track_context")\n    return sdk.package()'
    def cpu():clock[0]+=1.;return clock[0]
    with patch('time.process_time',side_effect=cpu),pytest.raises(PolicyRuntimeError,match='engineering limit'):
        interpret(code,card,sdk,30)
    assert sdk.interpreter_timing['policy_cpu_seconds']>30 and sdk.interpreter_timing['limit_seconds']==30
