"""Native delta acquisitions and scoped owned-cache local projections."""
import copy,time
from jrdb_lh.common import Rejected, cutoff, digest
from jrdb_lh.metering import example_cells, read_identity
from jrdb_lh.ledger import fee, reconcile
from jrdb_lh.economic_service import EconomicService
from active_research.adaptive.domains import project

VERSION = 'native-delta-owned-cache-v1'


def without_digest(value):
    return {k:v for k,v in value.items() if k!='digest'}


def seal(value):
    value['digest']=digest(without_digest(value))


def checked(value):
    if value.get('digest')!=digest(without_digest(value)):
        raise Rejected('Marginal checkpoint digest mismatch')


def native_pages(data):
    pages=[p for h in data['histories'].values() for p in h['pages']]
    for k in ('training','track'):
        if data[k] is not None:pages.append(data[k]['page'])
    return pages


def audit_owned_cache(state,complete=False):
    """Payload-only proof checks for supervisor commits and offline policy replay."""
    data=state.get('marginal_acquisition')
    events=[e for e in state.get('active_trace',[]) if e.get('evidence_id')]
    prov=state.get('semantic_provenance',[])
    if data is None:
        if events or prov:raise Rejected('Missing owned acquisition cache')
        return {'logical_actions':0,'native_acquisition_operations':0,'newly_acquired_cells':0,'local_projections':0,'cache_only_actions':0}
    b=data['binding'];race=state['schedule'][0]['races'][0]
    if data['version']!=VERSION or b['run_id']!=state['run_id'] or b['race_id']!=race or b['clock']!=cutoff(state['schedule'][0]['day']) or b['source']!=state['binding']['source']:
        raise Rejected('Owned cache source/temporal binding')
    cfg=state['binding'].get('config')
    if cfg and (b['projection']!=cfg['projection'] or b['tariff']!=cfg['tariff']):raise Rejected('Owned cache native configuration')
    for number,h in data['histories'].items():
        checked(h)
        if h['examples']!=[e for p in h['pages'] for e in p['response']['examples']] or len(h['examples'])>5:
            raise Rejected('Owned historical window changed')
        ids=[e['card']['race_id'] for e in h['examples']]
        if len(ids)!=len(set(ids)):raise Rejected('Duplicate owned historical starts')
        for p in h['pages']:
            if p['tool']!='horse_history' or p['arguments']['race_id']!=race or p['arguments']['number']!=number or p['arguments']['order']!='recent':raise Rejected('Owned horse query changed')
    for k in ('training','track'):
        if data[k] is not None:
            checked(data[k]);page=data[k]['page']
            if page['tool']!=('training' if k=='training' else 'card') or page['arguments']!={'race_id':race}:raise Rejected('Owned native batch scope')
    pages=native_pages(data);by_native={p['operation']:p for p in pages}
    if len(by_native)!=len(pages):raise Rejected('Duplicate native owned receipt')
    audit={r['operation']:r for r in state.get('audit',[])}
    for p in pages:
        r=audit[p['operation']]
        if r.get('status')!='ok' or r['tool']!=p['tool'] or r['arguments']!=p['arguments'] or r['response_digest']!=digest(p['response']) or r['_cost']!=p['receipt']:
            raise Rejected('Native owned payload/receipt linkage')
    by_logical={};charged=set()
    for row in data['logical_actions']:
        checked(row)
        if row['logical_id'] in by_logical:raise Rejected('Duplicate logical acquisition')
        by_logical[row['logical_id']]=row
        if row['source_manifest']!=b['source'] or row['clock']!=b['clock']:raise Rejected('Logical source changed')
        new=row['new_native_operations']
        if set(new)&charged or len(set(new))!=len(new):raise Rejected('Native receipt attributed twice')
        if not set(new)<=set(row['native_sources']):raise Rejected('Native invoice outside logical source')
        charged.update(new);receipts=[by_native[k]['receipt'] for k in new]
        for k in ('call_micro','data_micro','cpu_micro','total_micro','evidence_cells','charged_cells'):
            if row['receipt'][k]!=sum(r.get(k,0) for r in receipts):raise Rejected('Logical native invoice changed')
        sources=[by_native[k] for k in row['native_sources']];domain=row['action']['domain']
        if not sources or not set(row['native_sources'])<=charged:raise Rejected('Unowned logical source')
        if domain in ('training','track_context'):
            key='training' if domain=='training' else 'track'
            if row['native_sources']!=[data[key]['page']['operation']]:raise Rejected('Logical batch source scope')
            raw=copy.deepcopy(sources[0]['response'])
        else:
            number=row['action']['horse_number']
            expected=[p['operation'] for p in data['histories'][number]['pages'] if p['operation'] in charged]
            if row['native_sources']!=expected:raise Rejected('Logical history horse/order changed')
            raw={k:copy.deepcopy(v) for k,v in sources[0]['response'].items() if k not in {'_cost','_ledger','_usage','_budget'}}
            examples=[e for p in sources for e in p['response']['examples']];limit=row['action'].get('depth',5)
            raw['examples']=copy.deepcopy(examples[:limit])
            raw['next_cursor']='owned-cache-more' if len(examples)>limit else sources[-1]['response']['next_cursor']
        if raw!=row['backend_response'] or project(row['action'],raw)!=row['observation'] or digest(row['observation'])!=row['semantic_view_digest']:
            raise Rejected('Complete logical evidence changed')
        if row['native_acquisition_operations']!=len(new) or row['newly_acquired_cells']!=row['receipt']['charged_cells'] or row['local_projection']!=(not bool(new)) or row['local_projection_operations']!=1:
            raise Rejected('Marginal accounting counters changed')
    if complete and charged!=set(by_native):raise Rejected('Unattributed native purchase')
    if len(events)>len(by_logical) or len(prov)>len(by_logical):raise Rejected('Unknown logical event')
    for p in prov:
        row=by_logical[p['logical_id']]
        op='logical-'+digest([b['run_id'],row['logical_id'],row['action']])
        view=row['observation'];eid=digest([op,row['action'],view])
        if p['service_operation']!=op or p['evidence_id']!=eid or p['backend_response']!=row['backend_response'] or p['logical_digest']!=row['digest'] or p['response_digest']!=digest(row['backend_response']) or p['semantic_view_digest']!=digest(view) or p['native_sources']!=row['native_sources'] or p['source_manifest']!=b['source'] or p['clock']!=b['clock'] or p['race_id']!=race:
            raise Rejected('Logical provenance binding')
    by_prov={p['service_operation']:p for p in prov}
    if len(by_prov)!=len(prov):raise Rejected('Duplicate logical provenance')
    for e in events:
        p=by_prov[e['service_operation']];row=by_logical[p['logical_id']]
        if e['action']!=row['action'] or e['cost']!=row['receipt'] or e['evidence_id']!=p['evidence_id'] or e['observation_digest']!=row['semantic_view_digest'] or e['native_acquisition_operations']!=row['native_acquisition_operations'] or e['newly_acquired_cells']!=row['newly_acquired_cells'] or e['local_projection']!=row['local_projection'] or e['local_projection_operations']!=1:
            raise Rejected('Logical event invoice/source changed')
        if digest(e['control_before'])!=e['control_before_digest'] or digest(e['control_after'])!=e['control_after_digest']:raise Rejected('ControlView digests changed')
    if complete and (len(events)!=len(prov) or len(events)!=len(by_logical)):raise Rejected('Incomplete logical acquisition')
    return {'logical_actions':len(by_logical),'native_acquisition_operations':len(pages),
        'newly_acquired_cells':sum(p['receipt']['charged_cells'] for p in pages),
        'local_projections':sum(r['local_projection_operations'] for r in data['logical_actions']),
        'cache_only_actions':sum(r['local_projection'] for r in data['logical_actions'])}


class MarginalAcquisition:
    """Trusted acquisition/cache; policies must never receive this object.

    Data are stored only in the run checkpoint, not in generated policy code.
    Each logical action has a complete observation and native source references.
    """
    def __init__(self,run):
        self.run=run
        s=run.state
        from .source_binding import ensure
        verified=ensure(run)
        binding={'run_id':s['run_id'],'clock':s['clock'],'race_id':s['schedule'][0]['races'][0],
            'source':copy.deepcopy(verified['source_manifest']),'projection':run.config.projection,
            'tariff':run.config.tariff.public()}
        self.data=s.setdefault('marginal_acquisition',{'version':VERSION,'binding':binding,
            'histories':{},'training':None,'track':None,'logical_actions':[],'cursor_rebindings':[]})
        if self.data['version']!=VERSION or self.data['binding']!=binding:
            raise Rejected('Marginal cache source/clock/config changed')
        for h in self.data['histories'].values():checked(h)
        if self.data['training'] is not None:checked(self.data['training'])
        if self.data['track'] is not None:checked(self.data['track'])
        for row in self.data['logical_actions']:checked(row)

    def _legal(self):
        s=self.run.state
        if s['clock']!=cutoff(s['schedule'][0]['day']) or s.get('scores') or s.get('release_ledger'):
            raise Rejected('Marginal acquisition outside original release boundary')
        if s['clock']!=self.data['binding']['clock']:raise Rejected('Marginal acquisition clock changed')

    def _native(self,name,args,identity):
        # Original EconomicService operation idempotence protects the crash window
        # between paid delivery and saving the composed logical observation.
        op='v3-marginal-'+digest([self.data['binding'],identity,name,args])
        started=time.process_time()
        try:answer=self.run.service.call(name,args,operation_id=op)
        finally:self.run._native_acquisition_cpu_seconds=getattr(self.run,'_native_acquisition_cpu_seconds',0.)+time.process_time()-started
        row=next(r for r in reversed(self.run.state['audit']) if r['operation']==op)
        return {'operation':op,'tool':name,'arguments':copy.deepcopy(args),
            'response':copy.deepcopy(answer),'receipt':copy.deepcopy(row['_cost'])}

    def _continuation(self,h,args):
        old=h['pages'][-1];token=old['response']['next_cursor']
        service=self.run.service
        entry=service.state['cursors'].get(token)
        if not entry:raise Rejected('Missing paid continuation cursor')
        previous={k:v for k,v in old['arguments'].items() if k!='cursor'}
        current={k:v for k,v in args.items() if k!='cursor'}
        if {k:v for k,v in previous.items() if k!='limit'}!={k:v for k,v in current.items() if k!='limit'}:
            raise Rejected('Continuation may change only page size')
        if service._cursor('horse_history',previous,entry['position'])!=token:
            raise Rejected('Paid cursor binding changed')
        new=service._cursor('horse_history',current,entry['position'])
        proof={'tool':'horse_history','from_cursor':token,'to_cursor':new,
            'from_arguments':previous,'to_arguments':current}
        seal(proof)
        if proof not in self.data['cursor_rebindings']:self.data['cursor_rebindings'].append(proof)
        # This is metadata derived from an already delivered HMAC cursor, never a
        # hidden lookup of unpurchased starts. Native dispatch still validates it.
        return new

    def _history(self,number,depth):
        h=self.data['histories'].get(number)
        if h is None:
            h={'examples':[],'pages':[],'exhausted':False,'maximum_requested_depth':0,'maximum_policy_depth':0}
            self.data['histories'][number]=h;seal(h)
        checked(h)
        new=[]
        if len(h['examples'])<depth and not h['exhausted']:
            limit=depth-len(h['examples'])
            args={'race_id':self.data['binding']['race_id'],'number':number,'limit':limit,'order':'recent'}
            if h['pages']:args['cursor']=self._continuation(h,args)
            page=self._native('horse_history',args,['history',number,depth,len(h['examples'])])
            examples=page['response']['examples']
            old_ids={e['card']['race_id'] for e in h['examples']}
            new_ids=[e['card']['race_id'] for e in examples]
            if len(examples)>limit or old_ids.intersection(new_ids) or len(new_ids)!=len(set(new_ids)):
                raise Rejected('Native marginal page overlaps or exceeds purchased window')
            h['examples'].extend(copy.deepcopy(examples));h['pages'].append(page);new.append(page)
            h['exhausted']=page['response']['next_cursor'] is None
        h['maximum_requested_depth']=max(depth,h['maximum_requested_depth']);seal(h)
        raw={k:copy.deepcopy(v) for k,v in h['pages'][0]['response'].items()
            if k not in {'_cost','_ledger','_usage','_budget'}}
        raw['examples']=copy.deepcopy(h['examples'][:depth])
        raw['next_cursor']='owned-cache-more' if len(h['examples'])>depth else h['pages'][-1]['response']['next_cursor']
        return raw,new,[p['operation'] for p in h['pages']]

    def call(self,domain,horse_number=None,depth=None,logical_id=None):
        self._legal()
        card=self.run.state.get('active_initial',{}).get('card')
        if not card or card['race_id']!=self.data['binding']['race_id']:
            raise Rejected('Existing paid initial card required; no hidden card lookup')
        card_numbers=[r['number'] for r in card['runners']]
        if domain not in {'recent_history','training','condition_stats','track_context'}:raise Rejected('Four original domains only')
        if domain=='track_context':
            if horse_number is not None:raise Rejected('Track has no horse')
        elif horse_number not in card_numbers:raise Rejected('Unknown current runner')
        if domain=='recent_history':
            depth=5 if depth is None else depth
            if type(depth) is not int or depth not in (1,3,5):raise Rejected('History bounded at 1/3/5')
        elif depth is not None:raise Rejected('No depth for complementary domains')
        action={'type':'query','domain':domain}
        if horse_number is not None:action['horse_number']=horse_number
        if domain=='recent_history':action['depth']=depth
        logical_id=logical_id or 'logical-'+str(len(self.data['logical_actions'])+1)
        previous=next((r for r in self.data['logical_actions'] if r['logical_id']==logical_id),None)
        if previous:
            if previous['action']!=action:raise Rejected('Logical operation identity conflict')
            return copy.deepcopy(previous)
        if len(self.data['logical_actions'])>=self.run.config.limits.strategy_calls:
            raise Rejected('Original logical policy call limit')
        if domain in {'recent_history','condition_stats'}:
            old=self.data['histories'].get(horse_number)
            if domain=='recent_history' and old and depth<old['maximum_policy_depth']:
                raise Rejected('History depth must be monotone')
            raw,new,sources=self._history(horse_number,depth if domain=='recent_history' else 5)
            if domain=='recent_history':
                h=self.data['histories'][horse_number];h['maximum_policy_depth']=depth;seal(h)
        elif domain=='training':
            new=[];batch=self.data['training']
            if batch is None:
                page=self._native('training',{'race_id':self.data['binding']['race_id']},['training-race'])
                batch={'page':page};seal(batch);self.data['training']=batch;new=[page]
            checked(batch);raw=copy.deepcopy(batch['page']['response']);sources=[batch['page']['operation']]
        else:
            if self.data['track'] is not None:raise Rejected('Track context exactly once')
            page=self._native('card',{'race_id':self.data['binding']['race_id']},['track-once'])
            self.data['track']={'page':page};seal(self.data['track'])
            raw=copy.deepcopy(page['response']);new=[page];sources=[page['operation']]
        view=project(action,raw)
        receipt={k:sum(p['receipt'].get(k,0) for p in new) for k in
            ('call_micro','data_micro','cpu_micro','total_micro','evidence_cells','charged_cells')}
        receipt.update(repeat=not bool(new),basis='Original native receipts for newly delivered delta pages; cached projection has no native request')
        row={'logical_id':logical_id,'action':action,'observation':view,
            'semantic_view_digest':digest(view),'native_sources':sources,
            'new_native_operations':[p['operation'] for p in new],'receipt':receipt,
            'source_manifest':copy.deepcopy(self.data['binding']['source']),'clock':self.run.state['clock'],
            'backend_response':copy.deepcopy(raw),'native_acquisition_operations':len(new),
            'newly_acquired_cells':receipt['charged_cells'],'local_projection':not bool(new),'local_projection_operations':1}
        seal(row);self.data['logical_actions'].append(row);self.run.persist()
        return copy.deepcopy(row)

    def verify(self):
        """Read-only replay of native receipts, cursor proofs and full views."""
        s=self.run.state;reconcile(s)
        for h in self.data['histories'].values():
            checked(h)
            if h['examples']!=[e for p in h['pages'] for e in p['response']['examples']]:
                raise Rejected('Cached history is not its delivered native pages')
        if self.data['training'] is not None:checked(self.data['training'])
        if self.data['track'] is not None:checked(self.data['track'])
        rs={r['operation']:r for r in s['audit'] if r.get('status')=='ok'}
        pages=[p for h in self.data['histories'].values() for p in h['pages']]
        if self.data['training'] is not None:pages.append(self.data['training']['page'])
        if self.data['track'] is not None:pages.append(self.data['track']['page'])
        for p in pages:
            r=rs[p['operation']]
            if r['tool']!=p['tool'] or r['arguments']!=p['arguments'] or r['response_digest']!=digest(p['response']) or r['_cost']!=p['receipt']:
                raise Rejected('Marginal native source/receipt changed')
        # Native replay starts from an empty cursor registry, as the original
        # replay does. Rebinding proofs are replayed only from earlier paid pages.
        state={**s,'cursors':{},'audit':[]};service=EconomicService(self.run.source,self.run.config,state)
        for r in s['audit']:
            if r.get('status')!='ok' or r['tool'] not in {'horse_history','training','card'}:continue
            args=r['arguments'];token=args.get('cursor')
            if token and token not in state['cursors']:
                proof=next((p for p in self.data['cursor_rebindings'] if p['to_cursor']==token),None)
                if proof is None:raise Rejected('Unproved continuation cursor')
                checked(proof);old=state['cursors'].get(proof['from_cursor'])
                if old is None:raise Rejected('Cursor predecessor was not delivered')
                if {k:v for k,v in proof['from_arguments'].items() if k!='limit'}!={k:v for k,v in proof['to_arguments'].items() if k!='limit'}:
                    raise Rejected('Rebinding changed historical query')
                if service._cursor('horse_history',proof['to_arguments'],old['position'])!=token:
                    raise Rejected('Rebinding token changed')
            meter=[];actual=service.dispatch(r['tool'],args,meter)
            key,data_digest=read_identity(r['tool'],args,actual)
            if key!=r['read_key'] or data_digest!=r['data_digest'] or sum(meter)!=r['evidence_cells']:
                raise Rejected('Native marginal replay changed')
            if r['_cost']['data_micro']!=fee(self.run.config.tariff,'field_yen',r['charged_cells']):
                raise Rejected('Native cell tariff changed')
            if r['_cost']['call_micro']!=fee(self.run.config.tariff,'call_yen'):
                raise Rejected('Native call tariff changed')
            actual.update(r['envelope'])
            if digest(actual)!=r['response_digest']:raise Rejected('Native response changed')
        seen=set()
        by_operation={p['operation']:p for p in pages}
        for row in self.data['logical_actions']:
            checked(row)
            if set(row['new_native_operations'])&seen:raise Rejected('Logical invoice reuses a paid receipt')
            seen.update(row['new_native_operations'])
            new=[by_operation[k] for k in row['new_native_operations']]
            if any(row['receipt'][k]!=sum(p['receipt'].get(k,0) for p in new) for k in
                ('call_micro','data_micro','cpu_micro','total_micro','evidence_cells','charged_cells')):
                raise Rejected('Logical marginal invoice changed')
            source=[by_operation[k] for k in row['native_sources']]
            if row['action']['domain'] in ('training','track_context'):raw=source[0]['response']
            else:
                limit=row['action'].get('depth',5)
                raw={'examples':[e for p in source for e in p['response']['examples']][:limit]}
            if project(row['action'],raw)!=row['observation'] or digest(row['observation'])!=row['semantic_view_digest']:
                raise Rejected('Full logical evidence changed')
        if seen!={p['operation'] for p in pages}:raise Rejected('Unassigned native acquisition receipt')
        return {'native_tariff':'unchanged','logical_actions':len(self.data['logical_actions']),
            'native_receipts':len(pages),'complete_evidence':True,
            'duplicate_data_purchase':self.data['training'] is not None,
            'history_duplicate_start_purchase':False,
            'training_overlaps_paid_initial_card':self.data['training'] is not None,
            'accounting_version':VERSION,'accepted_semantics':'Native extra call fees retained; training once per race; shared owned history'}
