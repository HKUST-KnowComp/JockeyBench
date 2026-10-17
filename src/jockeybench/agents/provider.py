"""Chat-completions transport with bounded retries and cost monitoring.

Failed attempts are recorded but never consume a model decision. Before every
attempt the worst-case cost is reserved; when the configured monitoring
threshold would be crossed the run stops for the operator (``ProviderCostLimit``).
"""

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

from ..canonical import packed
from ..errors import Pending, ProviderCostLimit, Rejected

MAX_RESPONSE_BYTES = 2 * 1024**2
RETRYABLE_STATUS = {408, 409, 429}
# Applied when a provider file omits the field; never merged into the settings, which are the agent identity.
RETRY_DEFAULTS = {'retry_attempts': 1, 'retry_base_seconds': 2, 'retry_max_seconds': 300}


def credential_name(settings):
    return settings.get('api_key_env', 'OPENAI_API_KEY')


def require_credential(settings):
    """The key is read from the configured environment variable only, never from files."""
    if not os.environ.get(credential_name(settings)):
        raise Rejected('Configured provider credential is unavailable; no request was sent')


def require_prices(settings):
    if settings.get('max_estimated_cost_usd') is not None and any(
        settings.get(k) is None for k in ('input_usd_per_million', 'output_usd_per_million')
    ):
        raise Rejected('max_estimated_cost_usd needs input_usd_per_million and output_usd_per_million (docs/MODELS.md)')


def request_reservation(settings, body):
    """Conservative worst-case USD for one request, or ``None`` when it cannot be bounded."""
    if not all(settings.get(k) is not None for k in ('input_usd_per_million', 'output_usd_per_million')):
        return None
    # Deliberately conservative UTF-8 byte proxy, including tool serialization overhead.
    input_units = 2 * len(packed(body)) + 4096
    # A documented provider bound is for reservation only; it is never sent as a hidden cap.
    output_units = body.get(settings['output_token_field']) or settings.get('reservation_output_tokens')
    if output_units is None:
        return None
    return (input_units * settings['input_usd_per_million'] + output_units * settings['output_usd_per_million']) / 1e6


def cost_exposure(state):
    outputs = {o['request_id']: o for o in state.get('model_outputs', [])}
    total, unbounded_unknown = 0.0, False
    for attempt in state.get('provider_attempts', []):
        output = outputs.get(attempt['request_id']) if attempt.get('status') == 'complete' else None
        amount = output.get('cost_usd') if output else None
        if amount is None:
            amount = attempt.get('reserved_cost_usd')
        if amount is None:
            unbounded_unknown = True
        else:
            total += amount
    return total, unbounded_unknown


def check_funding(service, settings, reservation):
    cap = settings.get('max_estimated_cost_usd')
    if cap is None:
        return
    exposure, unknown = cost_exposure(service.state)
    if unknown or exposure + (reservation or 0) >= cap:
        service.state['funding_stop'] = {
            'estimated_or_reserved_usd': exposure,
            'threshold_usd': cap,
            'unbounded_unknown_cost': unknown,
            'next_request_reservation_usd': reservation,
        }
        # This is a terminal funding status, never a fake provider-pending state.
        if service.state.get('pending'):
            service.state.setdefault('abandoned_provider_requests', []).append({**service.state['pending'], 'reason': 'funding_stop'})
        service.state['pending'] = None
        service.persist()
        raise ProviderCostLimit('Provider cost monitoring threshold or unknown unreserved cost requires an operator stop')


def parse_response(raw):
    """Decode and structurally validate a chat-completions response."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Provider response too large')
    result = json.loads(raw)
    packed(result)
    message = result['choices'][0]['message']
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        raise ValueError('Invalid provider message')
    calls = message.get('tool_calls') or []
    if not isinstance(calls, list) or len(calls) > 64:
        raise ValueError('Invalid tool-call list')
    ids = set()
    for call in calls:
        if (
            not isinstance(call, dict)
            or not isinstance(call.get('id'), str)
            or not call['id']
            or call['id'] in ids
            or not isinstance(call.get('function'), dict)
            or not isinstance(call['function'].get('name'), str)
            or not isinstance(call['function'].get('arguments'), str)
        ):
            raise ValueError('Invalid tool-call envelope')
        ids.add(call['id'])
    return result


def _count_failure(state):
    state['provider_failures'] = state.get('provider_failures', 0) + 1
    state['usage']['provider_failures'] = state['usage'].get('provider_failures', 0) + 1


def request(service, settings, body, request_id, phase_key):
    """Send one request; returns ``(result, attempt)`` or raises ``Pending`` after the retry cycle."""
    state = service.state
    reservation = request_reservation(settings, body)
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': request_id}
    secret = os.environ.get(credential_name(settings))
    if secret:
        headers['Authorization'] = 'Bearer ' + secret
    outgoing = urllib.request.Request(settings['base_url'].rstrip('/') + '/chat/completions', data=packed(body), headers=headers)
    attempts_per_invocation = settings.get('retry_attempts', RETRY_DEFAULTS['retry_attempts'])
    for retry in range(attempts_per_invocation):
        check_funding(service, settings, reservation)
        attempts = state.setdefault('provider_attempts', [])
        attempt = {
            'request_id': request_id,
            'phase': state['phase'],
            'index': state['index'],
            'status': 'pending',
            'reserved_cost_usd': reservation,
            'attempt_index': len(attempts),
        }
        attempts.append(attempt)
        state['pending'] = {'request_id': request_id, 'phase': phase_key, 'status': 'network_pending'}
        service.persist()
        raw, delay_hint, started = None, 0, time.monotonic()
        try:
            with urllib.request.urlopen(outgoing, timeout=settings['timeout_seconds']) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            result = parse_response(raw)
            # The caller atomically commits this successful receipt, model step and message.
            attempt.update(status='complete', elapsed_seconds=time.monotonic() - started)
            return result, attempt
        except urllib.error.HTTPError as exc:
            attempt.update(status='http_error', http_status=exc.code)
            if exc.code not in RETRYABLE_STATUS and exc.code < 500:
                attempt['elapsed_seconds'] = time.monotonic() - started
                _count_failure(state)
                state['pending'] = None
                service.persist()
                raise Rejected('Provider HTTP ' + str(exc.code) + '; check endpoint, credentials and generation settings') from exc
            try:
                hint = float((exc.headers or {}).get('Retry-After', 0))
                delay_hint = max(0, hint) if math.isfinite(hint) else 0
            except (TypeError, ValueError):
                pass
        except (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError):
            attempt['status'] = 'invalid_response' if raw is not None else 'network_error'
            if raw is not None:
                attempt.update(
                    response_discarded=True, response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest(), cost_usd=None
                )
                state['pending']['status'] = 'invalid_response'
        attempt['elapsed_seconds'] = time.monotonic() - started
        _count_failure(state)
        service.persist()
        if retry + 1 < attempts_per_invocation:
            limit, base = (settings.get(k, RETRY_DEFAULTS[k]) for k in ('retry_max_seconds', 'retry_base_seconds'))
            delay = min(limit, max(delay_hint, base * 2**retry))
            attempt['retry_delay_seconds'] = delay
            service.persist()
            time.sleep(delay)
    raise Pending('Provider retry cycle exhausted; checkpoint retained; failed attempts did not consume model decisions')
