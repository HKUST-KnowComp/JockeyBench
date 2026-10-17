"""Replay audit: re-serve every successful data-tool response, check control responses against their digests, re-score races.

Data responses must reproduce their recorded digests, fees must match the
tariff, the ledger must reconcile with the receipts, and every sealed score
must be recomputed exactly from the sealed prediction.
"""

from dataclasses import replace as replace_config

from ..canonical import code_hash, digest
from ..data.source import Source
from ..errors import Rejected
from ..ledger import fee, reconcile
from ..protocol.metering import PROGRAM_BUDGET_VERSION, read_identity, repeat_index
from ..protocol.service import Service
from ..protocol.tools import DATA_TOOLS
from ..scoring import revenue_components, score


def replay(config, checkpoint, source=None):
    binding = checkpoint['binding']
    if binding['code'] != code_hash() or binding['config'] != config.public() or binding.get('budget_version') != PROGRAM_BUDGET_VERSION:
        raise Rejected('Replay requires the exact economic code and configuration')
    owned = source is None
    source = source or Source(config.source, config.limits.cache_mib)
    try:
        if source.manifest() != binding['source']:
            raise Rejected('Replay source mismatch')
        state = {**checkpoint, 'cursors': {}, 'audit': []}
        service = Service(source, config, state)
        checked = 0
        for row in checkpoint['audit']:
            if row.get('status') != 'ok':
                continue
            state.update(clock=row['clock'], phase=row['phase'], index=row['index'])
            # Imported warmup receipts retain their original run/arm namespace.
            shared_warmup = row['phase'] == 'warmup' and checkpoint.get('initial_audit_run_id')
            state['run_id'] = checkpoint['initial_audit_run_id'] if shared_warmup else checkpoint['run_id']
            service.config = replace_config(config, arm=checkpoint['initial_audit_arm']) if shared_warmup else config
            if row['tool'] in DATA_TOOLS:
                meter = []
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
        for race, expected in checkpoint['scores'].items():
            sealed = checkpoint['submissions'][race]
            actual = score(sealed['prediction'], source.settlements(race), config.tariff, delivery=sealed.get('delivery'))
            actual['format_fallback'] = sealed['format_fallback']
            if digest(actual) != digest(expected):
                raise Rejected('Sealed score replay mismatch')
            feedback = {'status': actual['status'], 'revenue_yen': actual['score_revenue_yen'], **revenue_components(actual)}
            if checkpoint['feedback'][race] != feedback:
                raise Rejected('Revenue feedback replay mismatch')
            verified_scores += 1
        audit_total = sum(r.get('_cost', {}).get('total_micro', 0) for r in checkpoint['audit'])
        unknown = any(j['status'] != 'settled' for j in checkpoint.get('compute_jobs', []))
        if not unknown and audit_total != totals['cost_micro']:
            raise Rejected('Tool receipts do not reconcile with ledger costs')
        if repeat_index(checkpoint['audit']) != checkpoint.get('read_digests', {}):
            raise Rejected('Replay repeat-read index mismatch')
        return {
            'verified_responses': checked,
            'ledger': totals,
            'audit_cost_micro': audit_total,
            'repeat_reads_verified': True,
            'verified_score_revenues': verified_scores,
            'uncertain_compute': unknown,
            'source_unchanged': True,
        }
    finally:
        if owned:
            source.close()
