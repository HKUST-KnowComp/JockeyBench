"""Versioned usage metadata; policy provenance never stands in for session cost."""
import copy
import statistics

PROTOCOL = 'policy-session-usage-v1'


def summarize(state, scope, session=None, finish_received=False):
    outputs = state.get('model_outputs', [])
    requests = state.get('llm_trajectory', [])
    attempts = state.get('provider_attempts', [])
    latencies = [r['logical_request_elapsed_seconds'] for r in requests
                 if r.get('logical_request_elapsed_seconds') is not None]
    cost = sum(o.get('cost_usd') or 0 for o in outputs)
    return {
        'accounting_protocol': PROTOCOL, 'scope': scope, 'session': session,
        'finish_received': finish_received,
        'logical_requests': len(requests), 'provider_attempts': len(attempts),
        'input_tokens': sum(o.get('prompt_tokens') or 0 for o in outputs),
        'output_tokens': sum(o.get('completion_tokens') or 0 for o in outputs),
        'estimated_cost_usd': cost, 'cost_usd': cost,
        'unknown_cost_outputs': sum(o.get('cost_usd') is None for o in outputs),
        'failed_attempts': sum(a['status'] != 'complete' for a in attempts),
        'logical_request_latencies_seconds': latencies,
    }


def capture_session(state, role, session, finish_received):
    value = summarize(state, 'complete_session', session, finish_received)
    value['termination'] = 'finish' if finish_received else 'terminated_without_finish'
    state[role + '_session_usage'] = copy.deepcopy(value)
    return value


def session_totals(states, role):
    """New snapshots or complete legacy transcripts; never policy.model_usage."""
    rows = []
    for state in states:
        value = state.get(role + '_session_usage')
        if value is None:
            value = summarize(state, 'complete_session', state.get('run_id'))
        if value.get('scope') != 'complete_session':
            raise ValueError('Report requires complete session usage')
        rows.append(value)
    keys = ('logical_requests', 'provider_attempts', 'input_tokens', 'output_tokens',
            'estimated_cost_usd', 'unknown_cost_outputs', 'failed_attempts')
    result = {key: sum(row[key] for row in rows) for key in keys}
    latencies = [x for row in rows for x in row['logical_request_latencies_seconds']]
    result['latency_seconds'] = {
        'n': len(latencies), 'sum': sum(latencies),
        'mean': statistics.mean(latencies) if latencies else None,
        'median': statistics.median(latencies) if latencies else None,
        'max': max(latencies) if latencies else None,
    }
    result.update(accounting_protocol=PROTOCOL, scope='complete_session')
    return result
