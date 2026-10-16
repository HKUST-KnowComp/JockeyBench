"""Deterministic released-Frame projection and complete-component request fitter.

Raw Frames, policy records, validation logs and the unpruned session stay in audit
storage. This module never executes a policy, fetches evidence or calls a model.
"""
import ast
import copy
import json
from collections import Counter
from pathlib import Path
from jrdb_lh.common import Rejected, digest, packed, release
from active_research.aligned.model import ContextLimit, guard_body
from active_research.adaptive.domains import SUPPORTED
from . import FRAME

VERSION = 'released-finegrained-updater-context-v1'
TARGET_BYTES = 200000
GUARD_BYTES = 262144
RECENT_FRAMES = 2
CHANGE_HISTORY = 3


def ratio(a, b):
    return a / b if b else None


def checked_frame(frame, clock):
    if (frame.get('protocol') != FRAME or frame.get('release_clock') != release(frame['day'])
            or clock < release(frame['day'])):
        raise Rejected('Updater released Frame boundary')
    if frame.get('frame_digest') != digest({k: v for k, v in frame.items() if k != 'frame_digest'}):
        raise Rejected('Updater Frame digest')
    rows = frame['per_race']
    if len(rows) != frame['races_executed'] or not rows:
        raise Rejected('Updater incomplete released Frame')
    if any(r['day'] != frame['day'] or r['release_clock'] != release(frame['day']) for r in rows):
        raise Rejected('Updater released row binding')
    return frame


def statistics(frames):
    """Counts/sums first, then rates; never average unequal-day percentages."""
    rows = [r for f in frames for r in f['per_race']]
    n = len(rows)
    fields = sum(r['field_size'] for r in rows)
    gross = sum(r['gross_reward'] for r in rows)
    cost = sum(r['weighted_research_cost'] for r in rows)
    observed1 = [r['top1_hit'] for r in rows if r['top1_hit'] is not None]
    observed3 = [r['top3_hit'] for r in rows if r['top3_hit'] is not None]
    ranks = [r['winner_rank'] for r in rows if r['winner_rank'] is not None]
    overall = {'race_count': n, 'runner_opportunities': fields, 'gross': gross,
        'weighted_research_cost': cost, 'net': gross - cost,
        'Top1': ratio(sum(observed1), len(observed1)), 'Top3': ratio(sum(observed3), len(observed3)),
        'mean_winner_rank': ratio(sum(ranks), len(ranks)), 'winner_rank_sum': sum(ranks),
        'winner_rank_observations': len(ranks), 'top1_hits': sum(observed1), 'top3_hits': sum(observed3),
        'log_reward': sum(r['log_reward'] for r in rows),
        'B_contribution': sum(r['B_contribution'] for r in rows),
        'cost_scope': 'Saved formal released-Frame ledger; historical warmup/API not in Frame'}
    domains = {}
    for domain in SUPPORTED:
        records = [r['finegrained']['domains'][domain] for r in rows]
        x = {k: sum(v[k] for v in records) for k in ('logical_policy_actions',
            'native_acquisition_operations', 'newly_acquired_cells', 'local_projections',
            'cache_only_actions', 'horses_queried', 'weighted_cost', 'raw_cost')}
        x['races_used'] = sum(v['logical_policy_actions'] > 0 for v in records)
        x['race_usage_rate'] = ratio(x['races_used'], n)
        x['horse_coverage'] = ratio(x['horses_queried'], fields) if domain != 'track_context' else None
        if domain == 'recent_history':
            for key in ('initial_depth_distribution', 'final_depth_distribution'):
                c = Counter({0: 0, 1: 0, 3: 0, 5: 0})
                for v, r in zip(records, rows):
                    c.update({int(k): count for k, count in v[key].items()})
                    c[0] += r['field_size'] - v['queried_horses']
                x[key] = {str(k): c[k] for k in (0, 1, 3, 5)}
            for key in ('final_depth_sum', 'queried_horses', 'initial_depth1_horses',
                        'queried_depth3_horses', 'expand_1_to_3_count', 'expand_3_to_5_count'):
                x[key] = sum(v[key] for v in records)
            x['mean_final_depth'] = ratio(x['final_depth_sum'], fields)
            x['mean_final_depth_queried'] = ratio(x['final_depth_sum'], x['queried_horses'])
            x['stop_at_depth1_rate'] = ratio(x['final_depth_distribution']['1'], fields)
            x['expand_1_to_3_rate'] = ratio(x['expand_1_to_3_count'], x['initial_depth1_horses'])
            x['expand_3_to_5_rate'] = ratio(x['expand_3_to_5_count'], x['queried_depth3_horses'])
        elif domain == 'training':
            x['races_acquired'] = sum(v['native_acquisition_operations'] > 0 for v in records)
            x['race_acquisition_rate'] = ratio(x['races_acquired'], n)
            x['horse_projection_use_rate'] = x['horse_coverage']
            x['native_purchase_scope'] = 'Once per race whole-race training; local horse projections'
        elif domain == 'condition_stats':
            x['trigger_rate'] = x['horse_coverage']
            x['history_cache_reuse_count'] = x['cache_only_actions']
            x['incremental_history_acquisition_count'] = x['native_acquisition_operations']
            x['incremental_new_history_cells'] = x['newly_acquired_cells']
        if domain in ('training', 'condition_stats'):
            # Preserve the condition information distribution, not horse identities
            # or one copy of the ControlView per trigger.
            triggers = [t for v in records for t in v['trigger_states']]
            x['trigger_history_record_distribution'] = dict(sorted(Counter(
                str(t['records_returned']) for t in triggers).items()))
            x['trigger_information_state_categories'] = dict(sorted(Counter(
                t['information_state_category'] for t in triggers).items()))
            x['trigger_missing_slots_sum'] = sum(t['missing_count'] for t in triggers)
            x['trigger_control_summaries'] = {}
            for key in ('records_returned', 'missing_count', 'missing_ratio', 'freshness_days',
                        'sample_starts', 'labelled_starts', 'same_surface_records', 'same_distance_records'):
                values = [t[key] for t in triggers if type(t[key]) in (int, float)]
                x['trigger_control_summaries'][key] = {'observations': len(values),
                    'mean': ratio(sum(values), len(values)), 'min': min(values) if values else None,
                    'max': max(values) if values else None}
        domains[domain] = x
    actions = sum(x['logical_policy_actions'] for x in domains.values())
    native = sum(x['native_acquisition_operations'] for x in domains.values())
    early = sum(r['finegrained']['stopping']['horse_level_early_stop_count'] for r in rows)
    domain_uses = sum(r['finegrained']['stopping']['mean_domains_per_horse'] * r['field_size'] for r in rows)
    stopping = {'logical_policy_actions': actions, 'native_acquisitions': native,
        'actions_per_race': ratio(actions, n), 'native_purchases_per_race': ratio(native, n),
        'domains_per_horse': ratio(domain_uses, fields), 'horse_level_early_stop_count': early,
        'horse_level_early_stop_rate': ratio(early, fields),
        'new_evidence_cells': sum(x['newly_acquired_cells'] for x in domains.values()),
        'local_projections': sum(x['local_projections'] for x in domains.values()),
        'cache_only_actions': sum(x['cache_only_actions'] for x in domains.values())}
    return {'overall': overall, 'domains': domains, 'stopping': stopping}


def project_frame(frame):
    return {'representation': VERSION, 'day': frame['day'], 'release_clock': frame['release_clock'],
        'policy_version': frame['per_race'][0]['policy_version'], **statistics([frame]),
        'observed_association': copy.deepcopy(frame['observed_association'])}


def changes(source):
    """Exact AST predicates and acquisition expressions, no inferred rationale."""
    tree = ast.parse(source)
    return {'acquisition_expressions': [ast.unparse(n) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ('call', 'stop_horse', 'stop_domain')],
        'branch_predicates': [ast.unparse(n.test) for n in ast.walk(tree) if isinstance(n, ast.If)]}


def context(current, frame, clock, folder):
    checked_frame(frame, clock)
    if frame['policy_identity']['policy_hash'] != current['policy_hash']:
        raise Rejected('Updater current policy binding')
    prior = []
    for path in sorted(Path(folder).glob('frame-????-??-??.json')):
        day = path.stem[6:]
        if day >= frame['day']:
            continue  # Do not open future Frame files, including offline replay.
        old = json.loads(path.read_text())
        if old['day'] != day:
            raise Rejected('Updater history filename/day mismatch')
        prior.append(checked_frame(old, clock))
    versions = []
    for path in sorted((Path(folder) / 'policies').glob('research_policy_v*.json')):
        if path.stem[len('research_policy_'):] > current['policy_version']:
            continue  # Never read future policy sources on saved-run replay.
        p = json.loads(path.read_text())
        if p.get('created_after_day') and p['created_after_day'] > frame['day']:
            raise Rejected('Updater future policy record')
        versions.append(p)
    history = []
    for a, b in zip(versions, versions[1:]):
        before, after = changes(a['source']), changes(b['source'])
        history.append({'after_day': b['created_after_day'], 'from_version': a['policy_version'],
            'to_version': b['policy_version'], 'source_changed': a['policy_hash'] != b['policy_hash'],
            'behavioral_structure_changed': before != after, 'before': before, 'after': after})
    history = history[-CHANGE_HISTORY:]
    current_view = project_frame(frame)
    previous = project_frame(prior[-1]) if prior else None
    # Numeric deltas are descriptive, with the previous/current denominators visible.
    def delta(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            return {k: delta(a[k], b.get(k)) for k in a if isinstance(a[k], (dict, int, float))}
        return a - b if type(a) in (int, float) and type(b) in (int, float) else None
    payload = {'context_version': VERSION, 'task': 'Revise the acquisition program or keep it.',
        'current_policy': {'source': current['source'], 'policy_version': current['policy_version'],
            'policy_hash': current['policy_hash']},
        'current_day_frame': current_view,
        'cumulative_historical_summary': {'through_day': frame['day'], **statistics(prior + [frame])},
        'prior_historical_summary': statistics(prior),
        'trend': {'previous_day': previous, 'current_day': current_view,
            'delta': delta(current_view, previous) if previous else None},
        'recent_frames': [project_frame(f) for f in prior[-RECENT_FRAMES:]],
        'previous_policy_changes': history[:-1],
        'latest_policy_change': history[-1] if history else {'status': 'initial_policy; no earlier saved change'}}
    # Hashes and full source records are audit only except the required current identity.
    audit = {'version': VERSION, 'current_frame_digest': frame['frame_digest'],
        'historical_frame_digests': [f['frame_digest'] for f in prior],
        'current_policy_hash': current['policy_hash'], 'legal_release_clock': clock,
        'projection_digest': digest(payload), 'raw_frames_preserved': True}
    return payload, audit


def fit(settings, messages, tools):
    """Protect anchors/newest round; evict only complete optional components."""
    from .transport import body
    unpruned = body(settings, copy.deepcopy(messages), tools)
    result = copy.deepcopy(messages)
    payload = json.loads(result[1]['content'])
    evicted = []
    def request():
        result[1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        return body(settings, result, tools)
    req = request()
    # Maintain indices into the full, immutable transcript, even after evictions.
    indices = list(range(len(result)))
    while len(packed(req)) > TARGET_BYTES:
        before = len(packed(req))
        if payload['recent_frames']:
            item = payload['recent_frames'].pop(0)
            identity = {'component': 'oldest recent detailed Frame', 'day': item['day']}
        elif payload['previous_policy_changes']:
            item = payload['previous_policy_changes'].pop(0)
            identity = {'component': 'old previous policy change', 'version': item['to_version']}
        else:
            rounds = []
            i = 2
            while i < len(result):
                assistant = result[i]
                calls = assistant.get('tool_calls', [])
                if assistant.get('role') == 'assistant' and calls:
                    needed = {c['id'] for c in calls}
                    j = i + 1
                    while j < len(result) and result[j].get('role') == 'tool':
                        needed.discard(result[j].get('tool_call_id'))
                        j += 1
                    if not needed:
                        rounds.append((i, j))
                    i = j
                else:
                    i += 1
            if len(rounds) <= 1:
                raise ContextLimit('updater essential/newest context exceeds 200000 target; nothing safe to evict')
            start, end = rounds[0]
            item = result[start:end]
            identity = {'component': 'complete assistant/tool round', 'round_start_message_index': indices[start]}
            del result[start:end]
            del indices[start:end]
        req = request()
        evicted.append({**identity, 'before_bytes': before, 'after_bytes': len(packed(req)),
            'evicted_digest': digest(item)})
    size = guard_body(req)
    assert GUARD_BYTES - size >= 40000
    return req, {'version': VERSION, 'unpruned_bytes': len(packed(unpruned)),
        'request_bytes': size, 'headroom': GUARD_BYTES - size, 'target_bytes': TARGET_BYTES,
        'evictions': evicted, 'request_digest': digest(req), 'unpruned_digest': digest(unpruned)}


def lifecycle_checkpoint(session):
    requests = len(session.get('llm_trajectory', []))
    completed = len(session.get('model_outputs', []))
    receipts = session.get('updater_context_requests', [])
    decision = session.get('updater_decision_validation', {})
    finish = bool(session.get('updater_session_usage', {}).get('finish_received'))
    context_failure = 'ContextLimit' in str(session.get('terminal_reason'))
    passed = bool(requests and completed and finish and decision.get('valid')
        and session.get('updater_decision_parsed') and not context_failure
        and receipts and all(r['request_bytes'] <= TARGET_BYTES and r['headroom'] >= 40000 for r in receipts))
    return {'passed': passed, 'provider_requests': requests, 'completed_responses': completed,
        'context_limit': context_failure, 'candidate_or_keep_decision_parsed': bool(session.get('updater_decision_parsed')),
        'validation_executed': bool(decision), 'decision_validation': decision, 'finish_received': finish,
        'saved': session.get('saved', False), 'accepted_policy_hash': session.get('accepted_policy_hash'),
        'max_request_bytes': max((r['request_bytes'] for r in receipts), default=None),
        'minimum_headroom': min((r['headroom'] for r in receipts), default=None),
        'criteria': 'Lifecycle only; no quality, policy-change or saved-policy requirement'}
