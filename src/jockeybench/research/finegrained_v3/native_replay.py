"""Frozen economic replay plus previously paid continuation-cursor proofs."""
from jockeybench.errors import Rejected
from jockeybench.canonical import digest, code_hash
from .marginal_cost import checked

def apply_rebinding(service, checkpoint, tool, args):
    token=args.get('cursor')
    if not token or token in service.state['cursors']:return
    proofs=checkpoint.get('marginal_acquisition',{}).get('cursor_rebindings',[])
    proof=next((p for p in proofs if p['to_cursor']==token),None)
    if proof is None:raise Rejected('Unproved marginal continuation')
    checked(proof)
    if tool!=proof['tool'] or {k:v for k,v in args.items() if k!='cursor'}!=proof['to_arguments']:
        raise Rejected('Continuation query binding')
    before=proof['from_arguments'];after=proof['to_arguments']
    if {k:v for k,v in before.items() if k!='limit'}!={k:v for k,v in after.items() if k!='limit'}:
        raise Rejected('Continuation changed historical scope')
    old=service.state['cursors'].get(proof['from_cursor'])
    if old is None or service._cursor(tool,before,old['position'])!=proof['from_cursor']:
        raise Rejected('Continuation predecessor not delivered')
    if service._cursor(tool,after,old['position'])!=token:raise Rejected('Continuation HMAC changed')

replay = lambda config, checkpoint: replay_economic(config,checkpoint)

def replay_economic(config, checkpoint, source=None):
    from dataclasses import replace
    from jockeybench.data.source import Source as EconomicSource
    from jockeybench.protocol.service import Service as EconomicService
    from jockeybench.protocol.tools import DATA_TOOLS
    if config.protocol != 'economic-v1':
        raise Rejected('Research replay supports only economic-v1')
    from jockeybench.ledger import reconcile
    from jockeybench.protocol.metering import PROGRAM_BUDGET_VERSION, read_identity, repeat_index
    from jockeybench.ledger import fee
    if (checkpoint['binding']['code'] != code_hash() or checkpoint['binding']['config'] != config.public()
        or checkpoint['binding'].get('budget_version') != PROGRAM_BUDGET_VERSION):
        raise Rejected('Replay requires the exact economic code and configuration')
    owned = source is None
    source = source or EconomicSource(config.source, config.limits.cache_mib)
    source.projection_version = config.projection
    try:
        if source.manifest() != checkpoint['binding']['source']:
            raise Rejected('Replay source mismatch')
        state = {**checkpoint, 'cursors': {}, 'audit': []}
        service = EconomicService(source, config, state)
        checked = 0
        for row in checkpoint['audit']:
            if row.get('status') != 'ok':
                continue
            state.update(clock=row['clock'], phase=row['phase'], index=row['index'])
            # Imported warmup receipts retain their original run/arm namespace.
            # This also handles an initial snapshot shared through another child.
            shared_warmup = row['phase'] == 'warmup' and checkpoint.get('initial_audit_run_id')
            state['run_id'] = checkpoint['initial_audit_run_id'] if shared_warmup else checkpoint['run_id']
            service.config = replace(config, arm=checkpoint['initial_audit_arm']) if shared_warmup else config
            if row['tool'] in DATA_TOOLS:
                meter = []
                apply_rebinding(service, checkpoint, row['tool'], row['arguments'])
                actual = service.dispatch(row['tool'], {k: v for k, v in row['arguments'].items() if k != 'max_spend'}, meter)
                key, value_digest = read_identity(row['tool'], row['arguments'], actual)
                if key != row.get('read_key') or value_digest != row.get('data_digest') or sum(meter) != row['evidence_cells']:
                    raise Rejected('Repeat-read data identity mismatch')
                if row['_cost']['data_micro'] != fee(service.config.tariff, 'field_yen', row['charged_cells']):
                    raise Rejected('Repeat-read evidence fee mismatch')
                actual.update(row['envelope'])
            else:
                actual = row['control_result']
            if digest(actual) != row['response_digest']:
                raise Rejected('Economic response replay mismatch')
            checked += 1
        totals = reconcile(checkpoint)
        verified_scores = 0
        if config.tariff.version == 'score-yen-v2':
            from jockeybench.scoring import score as economic_score, revenue_components
            for race, expected in checkpoint['scores'].items():
                sealed = checkpoint['submissions'][race]
                actual = economic_score(sealed['prediction'], source.settlements(race), config.tariff,
                                        delivery=sealed.get('delivery'))
                actual['format_fallback'] = sealed['format_fallback']
                if digest(actual) != digest(expected):
                    raise Rejected('Sealed score replay mismatch')
                feedback = {'status': actual['status'], 'revenue_yen': actual['score_revenue_yen'],
                            **revenue_components(actual)}
                if checkpoint['feedback'][race] != feedback:
                    raise Rejected('Revenue feedback replay mismatch')
                verified_scores += 1
        audit_total = sum(r.get('_cost', {}).get('total_micro', 0) for r in checkpoint['audit'])
        unknown = any(j['status'] != 'settled' for j in checkpoint.get('compute_jobs', []))
        if not unknown and audit_total != totals['cost_micro']:
            raise Rejected('Tool receipts do not reconcile with ledger costs')
        if repeat_index(checkpoint['audit']) != checkpoint.get('read_digests', {}):
            raise Rejected('Replay repeat-read index mismatch')
        return {'verified_responses': checked, 'ledger': totals, 'audit_cost_micro': audit_total, 'repeat_reads_verified': True,
                **({'verified_score_revenues': verified_scores} if config.tariff.version == 'score-yen-v2' else {}),
                'uncertain_compute': unknown, 'source_unchanged': True}
    finally:
        if owned:
            source.close()
