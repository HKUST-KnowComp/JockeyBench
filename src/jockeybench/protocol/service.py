"""The tool broker: the only boundary between an agent (or its sandboxed program) and the archive.

Every call is metered, priced and appended to the audit log. Calls that carry
an ``operation_id`` are idempotent across crashes: a completed read is replayed
and verified, a completed control call returns its stored result, and an
interrupted mutation is rolled back rather than executed twice.
"""

import copy
import hashlib
import hmac
import math
from datetime import date

from ..canonical import digest, packed
from ..clock import cutoff
from ..errors import BudgetExceeded, BudgetLimit, JobFailed, Pending, Rejected, safe_error
from ..ledger import MICRO, book, compute_cost, fee, ledger, summary
from .delivery import SAVE_WARNING
from .metering import (
    card_cells,
    connection_cells,
    coverage_cells,
    example_cells,
    form_cells,
    read_identity,
    result_cells,
    statistics_cells,
    training_cells,
)
from .tools import DATA_TOOLS, GROUPS, READ_TOOLS, REQUIRED, TOOL_ARGS

ENVELOPE_RESERVE = 2048  # bytes kept free for the accounting envelope of a data response
USAGE_KEYS = (
    'calls',
    'bytes',
    'evidence_cells',
    'model_calls',
    'seconds',
    'cpu_seconds',
    'strategy_calls',
    'strategy_evidence_cells',
    'strategy_bytes',
)


def model_call_cap(config, state, repair=None):
    """Absolute phase ceiling; a repair session may use its reserve and all unused calls."""
    limits = config.limits
    cap = limits.warmup_model_calls if state['phase'] == 'warmup' else limits.batch_model_calls
    if repair is None:
        repair = state.get('repair_session', False)
    return cap if repair else max(0, cap - min(cap, limits.repair_model_calls))


class Service:
    # Worker permissions can be narrowed by a setting; trusted outer-loop calls
    # still use the same metering and audit path.
    read_tools = frozenset(READ_TOOLS)

    def __init__(self, source, config, state, persist=lambda: None):
        self.source, self.config, self.state, self.persist = source, config, state, persist
        self.executor = None
        # Set by the agent loop: bytes still deliverable into the model context.
        self.observation_limit = None

    # -- accounting ------------------------------------------------------------------
    def charge(self, rate, units=1):
        # The re-execution audit quotes the live fees, so max_spend binds as it did; ledger.book records nothing.
        return fee(self.config.tariff, rate, units)

    def phase_caps(self):
        limits, warm = self.config.limits, self.state['phase'] == 'warmup'
        return {
            'tool_calls': limits.warmup_calls if warm else limits.batch_calls,
            'evidence_cells': limits.warmup_evidence_cells if warm else limits.batch_evidence_cells,
            'bytes': limits.warmup_bytes if warm else limits.batch_bytes,
        }

    def budget(self, response_bytes=0):
        """Remaining allowances, shown to the agent in every response as ``_budget``."""
        limits, used = self.config.limits, self.state['usage']
        if self.state.get('strategy_job_usage') is not None:
            response_bytes = 0  # Strategy replies do not consume session bytes.
        caps = {'model_calls': model_call_cap(self.config, self.state), **self.phase_caps()}
        remaining = {
            k: max(0, v - used.get('calls' if k == 'tool_calls' else k, 0) - (response_bytes if k == 'bytes' else 0))
            for k, v in caps.items()
        }
        remaining['model_calls'] = min(
            remaining['model_calls'], max(0, limits.model_turns - self.state.get('model_context', {}).get('turns', 0))
        )
        if remaining['model_calls'] <= 3 or any(remaining[k] <= caps[k] * 0.2 for k in ('evidence_cells', 'tool_calls', 'bytes')):
            remaining['warning'] = SAVE_WARNING
        return remaining

    def error_response(self, exc):
        return {'error': safe_error(exc), '_budget': self.budget()}

    def data_limit(self, cap, used, limit, requested):
        exc = BudgetLimit(cap, used, limit, requested)
        session = self.state.get('active_session') or f"{self.state['phase']}:{self.state['index']}"
        events = self.state.setdefault('limit_events', [])
        if not any(e.get('session') == session and e.get('cap') == cap for e in events):
            events.append(
                {
                    'phase': self.state['phase'],
                    'index': self.state['index'],
                    'session': session,
                    'reason': str(exc),
                    'recoverable': True,
                    **exc.details,
                }
            )
        raise exc

    # -- cursors ---------------------------------------------------------------------
    def _cursor(self, tool, arguments, position):
        """An opaque HMAC token bound to this run, clock, phase, tool and query."""
        if position is None:
            return None
        scope = [
            self.state['run_id'],
            self.state['clock'],
            self.state['phase'],
            tool,
            {k: v for k, v in arguments.items() if k != 'cursor'},
            position,
        ]
        token = hmac.new(self.source.key, packed(scope), hashlib.sha256).hexdigest()
        self.state['cursors'][token] = {'scope': scope, 'position': position}
        return token

    def _position(self, tool, args, default=''):
        token = args.get('cursor')
        if token is None:
            return default
        entry = self.state['cursors'].get(token)
        if not entry or self._cursor(tool, args, entry['position']) != token:
            raise Rejected('Cursor does not belong to this run, time and query')
        return entry['position']

    def _page_size(self, args, default):
        limit = args.get('limit', default)
        if type(limit) is not int or not 1 <= limit <= self.config.limits.page_size:
            return None
        return limit

    # -- the metered entry point -------------------------------------------------------
    def call(self, name, arguments=None, from_sdk=False, operation_id=None):
        return self._call(name, arguments, from_sdk, operation_id)

    def seal_prediction(self, race_id, prediction, operation_id=None):
        """Seal a prediction from the trusted lifecycle, never an agent tool.

        The broker exposed to models and sandbox workers only accepts ``call``;
        its submit request remains forbidden, even if a checkpoint contains an
        obsolete ``engine_submission`` flag.
        """
        return self._call('submit', {'race_id': race_id, 'prediction': prediction}, operation_id=operation_id, engine_submission=True)

    def _seal_prediction(self, race_id, prediction):
        from ..scoring import validate

        state = self.state
        if state['phase'] != 'predict' or not 0 <= state['index'] < len(state['schedule']):
            raise Rejected('Submission not open')
        batch = state['schedule'][state['index']]
        if race_id not in batch['races'] or state['clock'] != cutoff(batch['day']):
            raise Rejected('Prediction must belong to the current race batch and decision clock')
        entrants = [runner['number'] for runner in self.source.card(race_id, state['clock'])['runners']]
        prediction = copy.deepcopy(validate(prediction, entrants))
        old = state['submissions'].get(race_id)
        if old is not None:
            if old['prediction'] != prediction:
                raise Rejected('Prediction already sealed')
            return {'sealed': race_id, 'already_sealed': True}
        state['submissions'][race_id] = {
            'prediction': prediction,
            'strategy': state.get('strategy_digest'),
            'clock': state['clock'],
            'format_fallback': race_id in state.get('format_fallback_races', []),
        }
        self.persist()
        return {'sealed': race_id}

    def _call(self, name, arguments=None, from_sdk=False, operation_id=None, *, engine_submission=False):
        raw = {} if arguments is None else arguments
        try:
            encoded = packed(raw)
        except (TypeError, ValueError, RecursionError):
            raw, encoded = {'invalid_json': True}, b''
        ops = self.state.setdefault('economic_operations', {})
        key = digest([name, raw, self.state['clock'], self.state.get('strategy_job')])
        previous = ops.get(operation_id) if operation_id else None
        if previous and ((from_sdk and name not in self.read_tools) or (name == 'submit' and not engine_submission)):
            raise Rejected('Operation replay is not authorized through this interface')
        if previous and (previous['status'] != 'pending' or name not in READ_TOOLS):
            return self._resume(name, raw, key, previous, operation_id)
        # An interrupted read was charged before its checkpoint; finish it without a second charge.
        resumed = previous is not None

        used, limits = self.state['usage'], self.config.limits
        for k in USAGE_KEYS:
            used.setdefault(k, 0)
        job = self.state.get('strategy_job_usage')
        usage_prefix = 'strategy_' if job is not None else ''
        if not resumed and job is not None and job['calls'] >= limits.strategy_calls:
            raise BudgetExceeded('Strategy tool-call safety limit reached')
        caps = self.phase_caps()
        calls_before = used['calls']
        if not resumed:
            used[usage_prefix + 'calls'] += 1
            if job is not None:
                job['calls'] += 1
        ident = operation_id or 'tool-' + str(self.state.setdefault('economic_sequence', 0))
        if not operation_id:
            self.state['economic_sequence'] += 1
        if operation_id:
            ops[operation_id] = {
                'key': key,
                'status': 'pending',
                'restore_digest': self.state.get('strategy_digest'),
                'candidate_uniform': self.state.get('candidate_uniform', False),
                'cpu_before': compute_cost(self.state),
            }
        self.persist()  # A crash cannot erase a consumed call.
        log = {
            'tool': name,
            'arguments': raw,
            'clock': self.state['clock'],
            'phase': self.state['phase'],
            'index': self.state['index'],
            'projection': self.config.projection,
            'operation': ident,
            'from_sdk': from_sdk,
            'usage_scope': 'strategy' if job is not None else 'session',
        }
        mutation_origin = self.state.get('strategy_digest') if name in {'compute', 'save_strategy'} else None
        prior_uniform = self.state.get('candidate_uniform', False)
        data_fee = call_fee = 0
        before_cpu = compute_cost(self.state)
        try:
            if name in DATA_TOOLS and job is None and not resumed and calls_before >= caps['tool_calls']:
                self.data_limit('tool_calls', calls_before, caps['tool_calls'], 1)
            if not isinstance(raw, dict) or not encoded or len(encoded) > limits.response_bytes:
                raise Rejected('Arguments must be bounded finite JSON')
            if name not in TOOL_ARGS or set(raw) - TOOL_ARGS[name] or (from_sdk and name not in self.read_tools):
                raise Rejected('Unknown tool or unauthorized arguments')
            if name == 'submit' and (not engine_submission or from_sdk):
                raise Rejected('The framework executes predict and seals predictions; LLM sessions cannot submit')
            if any(k not in raw for k in REQUIRED.get(name, []) if not (name == 'save_strategy' and raw.get('mode') == 'uniform')):
                raise Rejected('Missing required arguments')
            maximum = raw.get('max_spend')
            if maximum is not None and (type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum < 0):
                raise Rejected('max_spend must be finite nonnegative score-yen')
            args = {k: v for k, v in raw.items() if k != 'max_spend'}
            meter = []
            result = self._seal_prediction(args['race_id'], args['prediction']) if name == 'submit' else self.dispatch(name, args, meter)
            cells = sum(meter)

            # Price the response; a repeat of an identical read carries no evidence fee.
            repeated = False
            if name in DATA_TOOLS:
                read_key, data_digest = read_identity(name, args, result)
                repeated = data_digest in self.state.get('read_digests', {}).get(read_key, [])
            charged_cells = 0 if repeated else cells
            call_fee = self.charge('call_yen') if name in DATA_TOOLS else 0
            if maximum is not None and call_fee + self.charge('field_yen', charged_cells) > maximum * MICRO:
                book(self.state, ident + ':call', 'calls', call_fee, tool=name)
                raise Rejected('max_spend exceeded; no data delivered or data fee charged. Use a smaller limit/k.')
            if name in DATA_TOOLS and job is None and charged_cells and used['evidence_cells'] + charged_cells > caps['evidence_cells']:
                self.data_limit('evidence_cells', used['evidence_cells'], caps['evidence_cells'], charged_cells)
            if job is not None and job['evidence_cells'] + charged_cells > limits.strategy_evidence_cells:
                raise BudgetExceeded('Strategy evidence safety limit reached; reduce k/page size')

            # Refuse undeliverable responses before charging. No large discarded observation is paid for.
            nested = from_sdk or self.state.get('strategy_job')
            capacity = limits.response_bytes if nested else min(limits.response_bytes, self.observation_limit or limits.context_bytes // 3)
            size = len(packed(result))
            if size + ENVELOPE_RESERVE > capacity:
                call_fee = 0
                if name in DATA_TOOLS and capacity == limits.response_bytes:
                    self.data_limit('response_bytes', 0, capacity, size + ENVELOPE_RESERVE)
                raise Rejected(
                    'Undeliverable response: use a smaller limit/k, page with the SDK inside compute, or return a smaller compute '
                    'result. No data or call fee charged; a compute keeps its CPU and nested SDK fees.'
                )
            if name in DATA_TOOLS and job is None and used['bytes'] + size + ENVELOPE_RESERVE > caps['bytes']:
                self.data_limit('bytes', used['bytes'], caps['bytes'], size + ENVELOPE_RESERVE)

            data_fee = self.charge('field_yen', charged_cells)
            if call_fee:
                book(self.state, ident + ':call', 'calls', call_fee, tool=name)
            if data_fee:
                category = 'execution_data' if self.state['phase'] == 'predict' else 'exploration_data'
                book(self.state, ident + ':data', category, data_fee, tool=name, units=charged_cells)
            cpu = compute_cost(self.state) - before_cpu if name == 'compute' else 0
            receipt = {
                'currency': 'score_yen_not_money',
                'call_micro': call_fee,
                'data_micro': data_fee,
                'cpu_micro': cpu,
                'total_micro': call_fee + data_fee + cpu,
                'repeat': repeated,
                'evidence_cells': cells,
                'charged_cells': charged_cells,
            }
            used[usage_prefix + 'evidence_cells'] += charged_cells
            if job is not None:
                job['evidence_cells'] += charged_cells

            # The envelope describes the remaining allowance after its own bytes (a small fixed point).
            envelope = {'_cost': receipt, '_ledger': summary(self.state, self.config)}
            envelope['_budget'] = self.budget()
            result.update(envelope)
            for _ in range(8):
                remaining = self.budget(len(packed(result)))
                if remaining == result['_budget']:
                    break
                envelope['_budget'] = result['_budget'] = remaining
            if len(packed(result)) > capacity:
                raise RuntimeError('Economic envelope exceeded its fixed reserve')
            used[usage_prefix + 'bytes'] += len(packed(result))
            if name in DATA_TOOLS:
                seen = self.state.setdefault('read_digests', {}).setdefault(read_key, [])
                if data_digest not in seen:
                    seen.append(data_digest)
                log.update(read_key=read_key, data_digest=data_digest)
            if name == 'save_strategy':
                self.state['session_saves'] = self.state.get('session_saves', 0) + 1
            log.update(
                status='ok',
                response_digest=digest(result),
                evidence_cells=cells,
                charged_cells=charged_cells,
                response_bytes=len(packed(result)),
                _cost=receipt,
                envelope=envelope,
            )
            if name not in DATA_TOOLS:
                log['control_result'] = result
            if operation_id:
                ops[operation_id] = {
                    'key': key,
                    'status': 'complete',
                    'digest': digest(result),
                    'envelope': envelope,
                    'result': result if name not in DATA_TOOLS else None,
                }
            return result
        except (Rejected, Pending) as exc:
            if mutation_origin and self.executor and not isinstance(exc, Pending):
                self.executor.restore(mutation_origin)
                self.state['candidate_uniform'] = prior_uniform
            if isinstance(exc, BudgetExceeded):
                self.state.setdefault('limit_events', []).append(
                    {'phase': self.state['phase'], 'index': self.state['index'], 'reason': str(exc)}
                )
            elif (
                name in DATA_TOOLS
                and not isinstance(exc, (Pending, BudgetLimit))
                and not str(exc).startswith(('Undeliverable', 'max_spend'))
            ):
                # A rejected data request still pays its call fee.
                book(self.state, ident + ':call', 'calls', self.charge('call_yen'), tool=name)
            call_fee = self._booked(ident + ':call')
            cpu = compute_cost(self.state) - before_cpu if name == 'compute' else 0
            log.update(
                status='interrupted' if isinstance(exc, Pending) else 'rejected',
                error=str(exc),
                _cost={'call_micro': call_fee, 'data_micro': 0, 'cpu_micro': cpu, 'total_micro': call_fee + cpu},
            )
            if operation_id and not isinstance(exc, Pending):
                ops[operation_id] = {'key': key, 'status': 'rejected', 'error': str(exc), 'error_detail': safe_error(exc)}
            raise
        except BaseException as exc:
            cpu = compute_cost(self.state) - before_cpu if name == 'compute' else 0
            actual = {category: self._booked(ident + ':' + category) for category in ('call', 'data')}
            log.update(
                status='framework_error' if isinstance(exc, Exception) else 'interrupted',
                error=type(exc).__name__,
                _cost={
                    'call_micro': actual['call'],
                    'data_micro': actual['data'],
                    'cpu_micro': cpu,
                    'total_micro': actual['call'] + actual['data'] + cpu,
                },
            )
            if isinstance(exc, Exception):
                raise Rejected('Framework could not complete this request; incurred costs remain in the ledger') from exc
            raise
        finally:
            self.state['audit'].append(log)
            self.persist()

    def _booked(self, ident):
        offset = ledger(self.state)['ids'].get(ident)
        return ledger(self.state)['entries'][offset]['amount_micro'] if offset is not None else 0

    def _resume(self, name, raw, key, previous, operation_id):
        """Resolve a completed, rejected or interrupted-mutation call seen again after a crash."""
        if previous['key'] != key:
            raise Rejected('Operation identity conflict')
        if previous['status'] == 'complete':
            if name not in DATA_TOOLS:
                return previous['result']
            result = self.dispatch(name, {k: v for k, v in raw.items() if k != 'max_spend'})
            result.update(previous['envelope'])
            if digest(result) != previous['digest']:
                raise Rejected('Idempotent read changed')
            return result
        if previous['status'] == 'rejected':
            error = previous.get('error_detail', {})
            if error.get('code') == 'BudgetLimit':
                raise BudgetLimit(*(error[k] for k in ('cap', 'used', 'limit', 'requested')))
            if error.get('code') == 'JobFailed':
                raise JobFailed(previous['error'], error.get('details'))
            raise Rejected(previous['error'])
        origin = previous.get('restore_digest')
        if not origin or self.executor is None:
            raise Pending('Interrupted mutation lacks a recoverable strategy snapshot')
        self.executor.restore(origin)
        self.state['candidate_uniform'] = previous.get('candidate_uniform', False)
        recorded = sum(row.get('_cost', {}).get('cpu_micro', 0) for row in self.state['audit'] if row.get('operation') == operation_id)
        cpu = compute_cost(self.state) - previous.get('cpu_before', 0)
        error = (
            'Interrupted ' + name + ' was rolled back without re-execution; incurred and unknown costs remain. '
            'Make a new tool call to continue.'
        )
        self.state['audit'].append(
            {
                'tool': name,
                'arguments': raw,
                'clock': self.state['clock'],
                'phase': self.state['phase'],
                'index': self.state['index'],
                'projection': self.config.projection,
                'usage_scope': 'strategy' if self.state.get('strategy_job_usage') is not None else 'session',
                'operation': operation_id,
                'status': 'recovered_interruption',
                'error': error,
                '_cost': {'call_micro': 0, 'data_micro': 0, 'cpu_micro': cpu - recorded, 'total_micro': cpu - recorded},
            }
        )
        self.state['economic_operations'][operation_id] = {'key': key, 'status': 'rejected', 'error': error}
        self.state.setdefault('recovered_mutations', []).append({'operation': operation_id, 'tool': name})
        self.persist()
        raise Rejected(error)

    # -- tools -----------------------------------------------------------------------
    def dispatch(self, name, args, meter=None):
        """Serve one validated request. ``meter`` collects the evidence cells delivered."""

        def observed(value, cells):
            if meter is not None:
                meter.append(cells)
            return {**value, '_cost': {'evidence_cells': cells}}

        state, config, clock, start = self.state, self.config, self.state['clock'], self.config.start
        if name in {'ledger', 'status'}:
            from .specification import public_limits

            return {
                'ledger': summary(state, config),
                'usage': dict(state['usage']),
                'limits': public_limits(config),
                'strategy_digest': state.get('validated_strategy'),
            }
        if name == 'describe':
            from .specification import describe

            return describe(self)
        if name == 'batch':
            index = state['index']
            if index >= len(state['schedule']):
                value = {'races': [], 'phase': 'completed'}
            else:
                batch = state['schedule'][index]
                value = {
                    'day_index': (date.fromisoformat(batch['day']) - date.fromisoformat(start)).days,
                    'races': [{'race_id': r, 'sealed': r in state['submissions']} for r in batch['races']],
                }
            for race in value['races']:
                race['runner_numbers'] = [r['number'] for r in self.source.card(race['race_id'], clock)['runners']]
            value['total_days'] = len(state['schedule'])
            value['total_races'] = sum(len(b['races']) for b in state['schedule'])
            return value
        if name == 'submissions':
            limit = self._page_size(args, config.limits.page_size)
            if limit is None:
                raise Rejected('Invalid page size')
            sealed = state['submissions']
            if 'race_id' in args:
                if args['race_id'] not in sealed:
                    raise Rejected('No sealed prediction for this race')
                selected = [(args['race_id'], sealed[args['race_id']])]
            else:
                selected = list(sealed.items())
            offset = self._position(name, args, 0)
            return {
                'items': [
                    {
                        'race_id': rid,
                        'prediction': v['prediction'],
                        'strategy_digest': v['strategy'],
                        'format_fallback': v.get('format_fallback', False),
                    }
                    for rid, v in selected[offset : offset + limit]
                ],
                'next_cursor': self._cursor(name, args, offset + limit if offset + limit < len(selected) else None),
            }
        if name == 'save_strategy':
            if config.arm == 'S' and state['phase'] != 'warmup':
                raise Rejected('This strategy is frozen after initial exploration')
            if args.get('mode') == 'uniform':
                if args.get('files'):
                    raise Rejected('uniform mode does not accept files')
                state['candidate_uniform'] = True
                return {'candidate': 'uniform'}
            if args.get('mode', 'code') != 'code':
                raise Rejected('Unknown strategy mode')
            if self.executor is None:
                raise Rejected('Sandbox executor unavailable')
            value = self.executor.save_strategy(args['files'])
            state['candidate_uniform'] = False
            return value
        if name == 'compute':
            if self.executor is None:
                raise Rejected('Sandbox executor unavailable')
            return self.executor.execute(args['code'])
        if name == 'card':
            card = self.source.card(args['race_id'], clock, start)
            return observed(card, card_cells(card))
        if name == 'training':
            card = self.source.card(args['race_id'], clock, start)
            return observed(
                {
                    'race_id': card['race_id'],
                    'feature_asof': card['feature_asof'],
                    'records': [
                        {'number': r['number'], 'values': r['training'], 'missing': r['missing']['training']} for r in card['runners']
                    ],
                },
                training_cells(card),
            )
        if name == 'form':
            value = self.source.form(args['race_id'], clock, start, args.get('k', 5))
            return observed(value, form_cells(value))
        if name == 'connections':
            from ..data.connections import connections

            value = connections(self.source, args['race_id'], clock, start)
            return observed(value, connection_cells(value))
        if name == 'results':
            return self._results(args, observed)
        if name == 'history':
            k = args.get('include_form', 0)
            limit = self._page_size(args, config.limits.default_page_size)
            if type(k) is not int or not 0 <= k <= 5 or limit is None:
                raise Rejected('Invalid include_form (0..5) or page size')
            value, position = self.source.history_page(
                clock,
                start,
                self._position(name, args),
                limit,
                filters=args.get('filters'),
                order=args.get('order', 'recent'),
                scope=args.get('scope', 'all'),
                include_form=k,
            )
            value['next_cursor'] = self._cursor(name, args, position)
            return observed(value, sum(example_cells(e) + (form_cells(e['form']) if 'form' in e else 0) for e in value['examples']))
        if name in {'horse_history', 'statistics', 'coverage'}:
            return self._page_tool(name, args, observed)
        raise Rejected('Unknown tool')

    def _results(self, args, observed):
        clock, start = self.state['clock'], self.config.start
        if 'race_id' in args:
            if set(args) != {'race_id'}:
                raise Rejected('results takes race_id alone or page parameters')
            value = self.source.objective_result(args['race_id'], clock, start)
            if value is None:
                raise Rejected('Results have not been released')
            return observed(value, result_cells(value))
        limit = self._page_size(args, self.config.limits.default_page_size)
        if limit is None:
            raise Rejected('Invalid page size')
        page, position = self.source.history_page(
            clock, start, self._position('results', args), limit, filters=args.get('filters'), order=args.get('order', 'recent')
        )
        items = [
            result
            for sample in page['examples']
            if (result := self.source.objective_result(sample['card']['race_id'], clock, start)) is not None
        ]
        return observed({'items': items, 'next_cursor': self._cursor('results', args, position)}, sum(result_cells(v) for v in items))

    def _page_tool(self, name, args, observed):
        """``horse_history``, ``statistics`` and ``coverage``: views over one history page."""
        clock, start, page_size = self.state['clock'], self.config.start, self.config.limits.page_size
        limit = self._page_size(args, self.config.limits.default_page_size)
        if limit is None:
            raise Rejected('Page size must be an integer from 1 to ' + str(page_size))
        if name == 'coverage' and 'race_id' in args:
            if set(args) - {'race_id'}:
                raise Rejected('coverage accepts race_id alone, or history-page parameters')
            result = {'examples': [{'card': self.source.card(args['race_id'], clock, start), 'labels': None}]}
            position = None
        else:
            anchor = (args['race_id'], args['number']) if name == 'horse_history' else None
            result, position = self.source.history_page(
                clock,
                start,
                self._position(name, args),
                limit,
                anchor=anchor,
                filters=args.get('filters'),
                order=args.get('order', 'recent'),
                scope=args.get('scope', 'support' if name == 'statistics' else 'all'),
            )
        cells = sum(example_cells(e) for e in result['examples'])
        if name == 'coverage':
            cells = coverage_cells(result['examples'])
            groups = {}
            for example in result['examples']:
                card = example['card']
                parts = (
                    [('race_facts', card['facts'])]
                    + [('runner_facts', r['facts']) for r in card['runners']]
                    + [('training', r['training']) for r in card['runners']]
                )
                for group, values in parts:
                    for key, value in values.items():
                        cell = groups.setdefault(group + '.' + key, {'total': 0, 'present': 0, 'missing': 0})
                        cell['total'] += 1
                        cell['missing' if value is None else 'present'] += 1
            result = {
                'fields': groups,
                'races': len(result['examples']),
                'scope': 'requested card/page only; no imputation and no hidden labels',
            }
        if name == 'statistics':
            group = args.get('group_by', 'surface')
            if group not in GROUPS:
                raise Rejected('group_by must be surface, distance, venue, draw or sex')
            cells = statistics_cells(result['examples'], group)
            groups, withheld, missing_labels = {}, 0, 0
            for example in result['examples']:
                if example['labels'] is None:
                    withheld += 1
                    continue
                for runner in example['card']['runners']:
                    key = runner['facts'].get(group, example['card']['facts'].get(group))
                    label = example['labels'].get(runner['number'], {})
                    if not isinstance(label.get('finish'), (int, float)) or label['finish'] < 1:
                        missing_labels += 1
                        continue
                    cell = groups.setdefault(str(key), {'n': 0, 'wins': 0, 'top3': 0})
                    cell['n'] += 1
                    cell['wins'] += label['finish'] == 1
                    cell['top3'] += label['finish'] <= 3
            result = {
                'groups': groups,
                'withheld_races': withheld,
                'missing_label_runners': missing_labels,
                'scope': 'this page only; labels visible under the result-release clock',
            }
        result['next_cursor'] = self._cursor(name, args, position)
        return observed(result, cells)
