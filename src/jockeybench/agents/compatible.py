"""A tool-calling model session over any OpenAI-compatible chat-completions endpoint.

Each session (warmup, daily reflection, repair) is one conversation: the system
message is the task specification, the user message is the task payload, and
the model calls the protocol tools until it answers without tool calls. The
conversation is checkpointed after every step, so a crash resumes it exactly.
"""

import json
import math
from urllib.parse import urlsplit

from ..canonical import digest, packed
from ..errors import BudgetExceeded, Pending, Rejected
from ..protocol.service import model_call_cap
from ..protocol.specification import program_context
from ..protocol.tools import tool_specs
from . import provider

SETTINGS = {
    'model',
    'base_url',
    'api_key_env',
    'timeout_seconds',
    'temperature',
    'seed',
    'reasoning_effort',
    'output_token_field',
    'input_usd_per_million',
    'output_usd_per_million',
    'max_estimated_cost_usd',
    'retry_attempts',
    'retry_base_seconds',
    'retry_max_seconds',
    'reservation_output_tokens',
}
SAMPLING = ('temperature', 'seed', 'reasoning_effort')
# Task text of each session; the task payload adds the session memory, ledger and tariff.
WARMUP_TASK = 'Initial exploration. Build an initial strategy from scratch and save it. No predictions yet.'
REPAIR_TASK = (
    'Repair the candidate program with remaining session resources, or choose uniform. If format_repair has an error field, '
    'that candidate was rejected and discarded and the last accepted workspace was restored; current_strategy_files show '
    'the .py/.json/.txt/.md files of that workspace.'
)
REFLECT_TASK = 'Day complete. You may change strategy or leave it unchanged. No new predictions until next batch.'
TRIM_NOTICE = (
    ' Earlier completed tool rounds may be removed to stay within context limits. Retrieve needed facts again '
    'or use your saved strategy notes; no automatic learned summary is supplied.'
)


def _is_number(value, low, high, *, integer=False, low_open=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value):
        return False
    return (low < value if low_open else low <= value) and value <= high


def validate_settings(settings):
    if not settings.get('model') or not settings.get('base_url'):
        raise Rejected('Provider requires model and base_url')
    url = urlsplit(settings['base_url'])
    if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise Rejected('Provider base_url must be an HTTP endpoint without credentials, query or fragment')
    if set(settings) - SETTINGS:
        raise Rejected('Unknown provider setting')
    if not _is_number(settings['timeout_seconds'], 0, 3600, low_open=True):
        raise Rejected('Provider timeout must be between 0 and 3600 seconds')
    if settings['output_token_field'] not in {'max_tokens', 'max_completion_tokens'}:
        raise Rejected('Unsupported output token field')
    if 'reservation_output_tokens' in settings and not _is_number(
        settings['reservation_output_tokens'], 0, math.inf, integer=True, low_open=True
    ):
        raise Rejected('reservation_output_tokens must be a positive integer documented provider bound')
    if 'max_estimated_cost_usd' in settings and not _is_number(settings['max_estimated_cost_usd'], 0, math.inf, low_open=True):
        raise Rejected('Cost monitoring threshold must be a finite positive amount')
    for field in ('input_usd_per_million', 'output_usd_per_million'):
        if settings.get(field) is not None and not _is_number(settings[field], 0, math.inf):
            raise Rejected('Configured token rates must be finite nonnegative amounts')
    defaults = provider.RETRY_DEFAULTS
    if not _is_number(settings.get('retry_attempts', defaults['retry_attempts']), 1, 10, integer=True):
        raise Rejected('retry_attempts must be from 1 to 10')
    for field in ('retry_base_seconds', 'retry_max_seconds'):
        if not _is_number(settings.get(field, defaults[field]), 0, 300):
            raise Rejected('Retry delays must be finite values from 0 to 300 seconds')


def fit_context(body, context, state, cap):
    """Deterministic FIFO removal of complete older rounds; the cap never grows."""
    messages = body['messages']
    while len(packed(body)) > cap:
        end = 3
        while end < len(messages) and messages[end]['role'] == 'tool':
            end += 1
        if not (len(messages) > 2 and messages[2]['role'] == 'assistant' and end < len(messages)):
            raise BudgetExceeded('Provider context byte limit reached even after bounded observations; checkpoint retained')
        removed = messages[2:end]
        del messages[2:end]
        context['discarded_rounds'] = context.get('discarded_rounds', 0) + 1
        state.setdefault('context_events', []).append({'kind': 'discarded_round', 'phase_key': context['key'], 'digest': digest(removed)})
        if TRIM_NOTICE not in messages[0]['content']:
            messages[0]['content'] += TRIM_NOTICE


def usage_and_cost(result, settings):
    """Token usage as reported, and the best available USD estimate with its basis."""
    usage = result.get('usage')
    usage = dict(usage) if isinstance(usage, dict) else {}
    for field in ('prompt_tokens', 'completion_tokens'):
        if type(usage.get(field)) is not int or not 0 <= usage[field] <= 10**9:
            usage[field] = None
    reported = usage.get('estimated_cost')
    if type(reported) in (int, float) and math.isfinite(reported) and reported >= 0:
        return usage, reported, 'provider_reported_estimate'
    rates = settings.get('input_usd_per_million'), settings.get('output_usd_per_million')
    if None not in rates and usage['prompt_tokens'] is not None and usage['completion_tokens'] is not None:
        expense = (usage['prompt_tokens'] * rates[0] + usage['completion_tokens'] * rates[1]) / 1e6
        if math.isfinite(expense):
            return usage, expense, 'configured_uncached_rate_estimate'
    return usage, None, 'unavailable'


class CompatibleAgent:
    def __init__(self, settings):
        self.settings = {'timeout_seconds': 300, 'output_token_field': 'max_tokens', **settings}
        validate_settings(self.settings)
        self.identity = {k: v for k, v in self.settings.items() if k != 'api_key_env'}
        self.identity.update(kind='compatible', retry_policy='bounded-backoff-successful-decision-budget', version=4)

    def warmup(self, service):
        self._session(service, {'task': WARMUP_TASK})

    def repair_strategy(self, service, failure):
        self._session(service, {'task': REPAIR_TASK, 'format_repair': failure})

    def reflect(self, service, feedback):
        self._session(service, {'task': REFLECT_TASK, 'feedback': feedback})

    def _session(self, service, task):
        state, limits = service.state, service.config.limits
        system, task = program_context(service, task)
        phase_key = digest([state['index'], state['phase'], {k: task.get(k) for k in ('task', 'race_id', 'format_repair')}])
        context = state.setdefault('model_context', {})
        if context.get('key') != phase_key:
            state['model_context'] = context = {
                'key': phase_key,
                'turns': 0,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(task)}],
            }
        messages = context['messages']
        if context.get('done'):
            return context.get('final', '')
        while True:
            # After a crash between a tool call and its reply, finish the queue before asking the model again.
            queue = context.get('tool_queue', [])
            while queue:
                call = queue[0]
                messages.append(
                    {'role': 'tool', 'tool_call_id': call['id'], 'content': packed(self._serve(service, context, call, phase_key)).decode()}
                )
                queue.pop(0)
                service.persist()
            if context['turns'] >= limits.model_turns:
                raise BudgetExceeded('Provider turn budget exhausted')
            body = {
                'model': self.settings['model'],
                'messages': messages,
                'tools': tool_specs(limits.page_size),
                self.settings['output_token_field']: limits.model_output_tokens,
            }
            body.update({k: self.settings[k] for k in SAMPLING if k in self.settings})
            fit_context(body, context, state, limits.context_bytes)
            if state['usage'].get('model_calls', 0) >= model_call_cap(service.config, state, repair='format_repair' in task):
                raise BudgetExceeded('Provider call budget for this phase is exhausted')
            request_id = digest([state['run_id'], phase_key, context['turns'], body])
            result, attempt = provider.request(service, self.settings, body, request_id, phase_key)
            message = result['choices'][0]['message']
            usage, expense, basis = usage_and_cost(result, self.settings)
            state['model_outputs'].append(
                {
                    'request_id': request_id,
                    'message': message,
                    'usage': usage,
                    'cost_usd': expense,
                    'cost_basis': basis,
                    'response_model': result.get('model'),
                    'finish_reason': result['choices'][0].get('finish_reason'),
                    'elapsed_seconds': attempt['elapsed_seconds'],
                }
            )
            state['usage']['model_calls'] = state['usage'].get('model_calls', 0) + 1
            state['pending'] = None
            context['turns'] += 1
            messages.append(message)
            context['tool_queue'] = list(message.get('tool_calls') or [])
            if not context['tool_queue']:
                # The received final output and session completion are one commit.
                context.update(done=True, final=message.get('content', ''))
            service.persist()
            if not context['tool_queue']:
                return context['final']

    def _serve(self, service, context, call, phase_key):
        """Execute one model tool call; protocol errors become an error response the model can read."""
        limits = service.config.limits
        try:
            function = call['function']  # provider.parse_response guarantees the envelope shape.
            try:
                arguments = json.loads(function['arguments'])
            except ValueError:
                arguments = []  # Invalid requests still consume a service step.
            name = function['name']
            # Reserve delivery space before executing a tool; otherwise a nearly
            # full context could reject even a tiny receipt forever.
            headroom = min(limits.response_bytes, limits.context_bytes // 3)
            preview = {'model': self.settings['model'], 'messages': context['messages'], 'tools': tool_specs(limits.page_size)}
            fit_context(preview, context, service.state, limits.context_bytes - headroom - 4096)
            service.observation_limit = headroom
            return service.call(name, arguments, operation_id='provider-' + phase_key + '-' + call['id'])
        except (BudgetExceeded, Pending):
            raise
        except Exception as exc:
            return service.error_response(exc)
