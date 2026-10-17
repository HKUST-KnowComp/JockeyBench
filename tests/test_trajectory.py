"""End-to-end trajectories of the reference programs in the real sandbox, with both audits."""

import copy

import pytest

from conftest import native
from jockeybench.agents.reference import ProgramReference
from jockeybench.audit.reexecution import reexecute
from jockeybench.audit.replay import replay
from jockeybench.canonical import digest
from jockeybench.reporting import compare

pytestmark = [native, pytest.mark.native]
SMALL = {'smoke_days': 2, 'races_per_day': 2, 'schedule_mode': 'smoke'}  # three races on two days


@pytest.mark.parametrize('kind', ['R1', 'R2', 'R3'])
def test_reference_fits_once_predicts_resumes_and_audits(make_run, kind):
    agent = ProgramReference(kind, samples=2)
    run = make_run(kind, agent, native=True, **SMALL)
    run.run(stop_after=1)
    assert run.state['validated_strategy'] and run.state['isolation']['network_denied']
    sealed, jobs = copy.deepcopy(run.state['submissions']), len(run.state['compute_jobs'])
    run.close()
    run = make_run(kind, agent, native=True, **SMALL)
    report = run.run()
    assert report['completed'] and report['delivery_totals']['predictor'] == 3 and not report['strategy_failures']
    assert all(run.state['submissions'][k] == v for k, v in sealed.items())
    assert len(run.state['compute_jobs']) == jobs + 2  # the two remaining races, nothing re-executed
    assert report['ledger_reconciliation']['reconciled'] and report['prediction_model_requests'] == 0
    audit = replay(run.config, run.state, run.source)
    assert audit['verified_score_revenues'] == 3 and not audit['uncertain_compute']
    again = reexecute(run.config, run.state, run.source)
    assert again['matches'] == 3 and again['mismatches'] == 0
    run.close()


def test_a_crash_after_compute_never_reexecutes_the_race(make_run, monkeypatch):
    run = make_run('crash', ProgramReference(), native=True, **SMALL)
    version = run.executor.save_strategy(
        {
            'strategy.py': "def predict(sdk, context):\n    n = context['runner_numbers']\n"
            "    return {'ranking': n, 'probabilities': {k: 1 / len(n) for k in n}}\n"
        }
    )['strategy_digest']
    run.state.update(phase='predict', initial_strategy=version, validated_strategy=version, valid_strategies=[version])
    original = run.executor.predict

    def crash(ctx):
        original(ctx)
        raise KeyboardInterrupt()

    monkeypatch.setattr(run.executor, 'predict', crash)
    with pytest.raises(KeyboardInterrupt):
        run.run()
    assert not run.state['submissions'] and len(run.state['compute_jobs']) == 1
    run.close()
    run = make_run('crash', ProgramReference(), native=True, **SMALL)
    report = run.run()
    assert report['completed'] and report['uniform_fallback_races'] == 1 and len(run.state['compute_jobs']) == 3
    assert report['strategy_failures'][0]['error'].startswith('Interrupted')
    assert replay(run.config, run.state, run.source)['ledger']['reconciled']
    run.close()


def test_arms_started_from_a_shared_warmup_replay_its_receipts(make_run):
    class ReferenceWithResults(ProgramReference):
        def warmup(self, service):
            super().warmup(service)
            service.call('results', {'limit': 1})

    agent = ReferenceWithResults('R2', samples=2)
    common = make_run('shared', agent, native=True, **SMALL)
    common.run(warmup_only=True)
    common.close()
    reports = {}
    for arm in ['S', 'A']:
        run = make_run('shared-' + arm, agent, initial_from='shared', native=True, arm=arm, **SMALL)
        reports[arm] = run.run()
        assert replay(run.config, run.state, run.source)['ledger']['reconciled']
        assert reexecute(run.config, run.state, run.source)['mismatches'] == 0
        run.close()
    paired = compare(reports['A'], reports['S'])
    assert paired['common_scored_races'] == 3 and paired['common_initial_strategy']


class SavesOneProgram:
    """A scripted agent: its warmup saves the given strategy files; it never reflects."""

    def __init__(self, files):
        self.files = files
        self.identity = {'kind': 'test-saves-one-program', 'files': digest(files)}

    def warmup(self, service):
        service.call('save_strategy', {'files': self.files}, operation_id='test-save')

    def reflect(self, service, feedback):
        pass


def test_reexecution_quotes_fees_so_max_spend_binds_as_in_the_run(make_run):
    strategy = (
        'def predict(sdk, context):\n'
        "    n = list(context['runner_numbers'])\n"
        '    try:\n'
        "        sdk.call('card', race_id=context['race_id'], max_spend=0.0001)  # always over budget\n"
        '    except Exception:\n'
        '        n.reverse()\n'
        '    total = sum(range(1, len(n) + 1))\n'
        "    return {'ranking': n, 'probabilities': {k: (len(n) - i) / total for i, k in enumerate(n)}}\n"
    )
    run = make_run('max-spend', SavesOneProgram({'strategy.py': strategy}), native=True, **SMALL)
    report = run.run()
    assert report['completed'] and report['delivery_totals']['predictor'] == 3
    for race, sealed in run.state['submissions'].items():
        numbers = sorted(sealed['prediction']['ranking'], key=int)
        assert sealed['prediction']['ranking'] == numbers[::-1], race  # the refusal happened in the run
    audit = reexecute(run.config, run.state, run.source)
    assert audit['matches'] == 3 and audit['mismatches'] == 0
    run.close()


def test_a_strategy_error_mentioning_storage_rejects_the_candidate_instead_of_stopping(make_run):
    strategy = 'class StorageError(Exception):\n    pass\n\n\ndef predict(sdk, context):\n    raise StorageError(\'storage of my own\')\n'
    run = make_run('storage-name', SavesOneProgram({'strategy.py': strategy}), native=True, **SMALL)
    report = run.run()
    assert report['completed'] and not run.state['validated_strategy']
    assert report['delivery_totals']['no_strategy'] == 3
    assert any('StorageError' in f['error'] for f in report['strategy_failures'])
    run.close()


def predicting(probabilities):
    return (
        'def predict(sdk, context):\n'
        "    n = list(context['runner_numbers'])\n"
        f'    p = {probabilities}\n'
        "    return {'ranking': n, 'probabilities': {k: p[i] / sum(p) for i, k in enumerate(n)}}\n"
    )


def test_equal_probabilities_are_sealed_silent_uniform_and_earn_no_hit_bonus(make_run):
    strategy = predicting('[1] * len(n)')
    run = make_run('equal', SavesOneProgram({'strategy.py': strategy}), native=True, **SMALL)
    report = run.run()
    assert run.state['validated_strategy'] and run.state['validation_notice']['classification'] == 'silent_uniform'
    assert report['delivery_totals']['silent_uniform'] == 3
    assert all(score['revenue_hit_yen'] == 0 for score in report['race_scores'].values())
    run.close()


def test_choosing_uniform_seals_explicit_uniform(make_run):
    class ChoosesUniform(SavesOneProgram):
        def warmup(self, service):
            service.call('save_strategy', {'mode': 'uniform'}, operation_id='test-uniform')

    run = make_run('uniform', ChoosesUniform({}), native=True, **SMALL)
    report = run.run()
    assert run.state['explicit_uniform'] and report['delivery_totals']['explicit_uniform'] == 3
    run.close()


def test_nondeterministic_output_is_flagged_but_keeps_the_program(make_run):
    strategy = 'import random\n\n' + predicting('[random.random() + 0.1 for _ in n]')
    run = make_run('random', SavesOneProgram({'strategy.py': strategy}), native=True, **SMALL)
    report = run.run()
    assert any(o['nondeterministic_output'] for o in run.state['last_validation'])
    assert run.state['validated_strategy'] and report['delivery_totals']['predictor'] == 3
    run.close()
