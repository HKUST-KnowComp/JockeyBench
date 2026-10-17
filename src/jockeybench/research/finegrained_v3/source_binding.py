"""Process-local verified source epoch; persistent proof, no per-query full hash."""
import copy,time
from pathlib import Path
from jockeybench.canonical import digest
from jockeybench.data.source import stat_signature
from jockeybench.research.aligned.integrity import IntegrityFailure
VERSION='verified-source-epoch-v1'

def signatures(source):
    path=getattr(source,'path',None)
    if path is None:return None
    path=Path(path);side=Path(str(path)+'.anonymization.json')
    return {'database':stat_signature(path),'sidecar':stat_signature(side) if side.exists() else None}

def scope(run):
    source=run.source;path=getattr(source,'path',None)
    return {'version':VERSION,'run_id':run.state['run_id'],'source_manifest':copy.deepcopy(run.state['binding']['source']),
        'configured_source_path':str(Path(run.config.source).absolute()),
        'resolved_source_path':str(Path(path).resolve(strict=True)) if path is not None else None,
        'config_digest':digest(run.config.public()),'projection':run.config.projection,
        'tariff_digest':digest(run.config.tariff.public()),'file_signatures':signatures(source)}

def validate_record(state):
    record=state.get('verified_source_binding')
    if record is None:raise IntegrityFailure('Missing verified source binding')
    if record.get('digest')!=digest({k:v for k,v in record.items() if k!='digest'}) or record['version']!=VERSION or record['run_id']!=state['run_id'] or record['source_manifest']!=state['binding']['source']:
        raise IntegrityFailure('Verified source binding changed')
    config=state['binding'].get('config')
    if config and record['config_digest']!=digest(config):raise IntegrityFailure('Verified configuration changed')
    return record

def establish(run,manifest=None,verification_cpu=None):
    started=time.process_time()
    try:
        verified=run.source.manifest() if manifest is None else manifest
        if verified!=run.state['binding']['source']:raise IntegrityFailure('Frozen source digest/manifest mismatch')
        cfg=run.state['binding'].get('config')
        if cfg and cfg!=run.config.public():raise IntegrityFailure('Frozen source configuration mismatch')
        record=scope(run);record['digest']=digest(record)
        prior=run.state.get('verified_source_binding')
        if prior is not None and validate_record(run.state)!=record:raise IntegrityFailure('Restored source path/projection/tariff/stat binding mismatch')
        run.state['verified_source_binding']=copy.deepcopy(record)
        run._verified_source_epoch={'source':run.source,'record':copy.deepcopy(record),'verification_cpu_seconds':verification_cpu if verification_cpu is not None else time.process_time()-started,'full_verifications':1,'reuse_checks':0}
        return record
    except IntegrityFailure:raise
    except Exception as exc:raise IntegrityFailure('Source epoch verification failed: '+type(exc).__name__) from exc

def ensure(run):
    epoch=getattr(run,'_verified_source_epoch',None)
    if epoch is None:return establish(run)
    try:
        record=validate_record(run.state)
        if epoch['source'] is not run.source or record!=epoch['record'] or scope(run)!={k:v for k,v in record.items() if k!='digest'}:raise IntegrityFailure('Active immutable source epoch mismatch')
        if getattr(run.source,'projection_version',run.config.projection)!=run.config.projection:raise IntegrityFailure('Active source projection mismatch')
        run.source.verify()  # original writer/WAL/stat checks remain in force
        if signatures(run.source)!=record['file_signatures']:raise IntegrityFailure('Source or sidecar changed in verified epoch')
        cfg=run.state['binding'].get('config')
        if cfg and cfg!=run.config.public():raise IntegrityFailure('Active configuration mismatch')
        epoch['reuse_checks']+=1
        return record
    except IntegrityFailure:raise
    except Exception as exc:raise IntegrityFailure('Source epoch reuse verification failed: '+type(exc).__name__) from exc

def metrics(run):
    e=getattr(run,'_verified_source_epoch',None)
    return {'version':VERSION,'full_verifications':e['full_verifications'],'source_verification_cpu_seconds':e['verification_cpu_seconds'],'reuse_checks':e['reuse_checks']} if e else None

from jockeybench.runner import Run as BaseRun
class VerifiedRun(BaseRun):
    def __init__(self,config,run_id,agent,initial_from=None,source=None):
        if source is None:
            from jockeybench.data.source import Source as EconomicSource
            source=EconomicSource(config.source,config.limits.cache_mib)
        original=source.manifest;verified=[]
        def timed_manifest():
            t=time.process_time();m=original();verified.append((copy.deepcopy(m),time.process_time()-t));return m
        source.manifest=timed_manifest
        try:super().__init__(config,run_id,agent,initial_from,source)
        finally:source.manifest=original
        if len(verified)!=1:raise IntegrityFailure('Unexpected run initialization source verification count')
        establish(self,verified[0][0],verified[0][1]);self.persist()
