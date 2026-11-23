"""Run, audit and report code-as-policy trajectories, and import licensed JRDB archives.

Exit codes: 0 success, 2 stopped (a protocol refusal), 3 pending (an
interrupted provider request; resume with the same command).
"""

import argparse
import gzip
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path

from .agents.reference import DEFAULT_KIND, DEFAULT_SAMPLES, KINDS
from .config import ARMS, Config
from .errors import Pending, Rejected

HOST_MARKER = '.experiment-host'
WINDOWS_GUIDANCE = (
    'On Windows, run JockeyBench inside WSL2 with Ubuntu 22.04. Native Windows Python cannot provide the required sandbox. '
    'macOS and Linux run directly. See docs/WINDOWS.md.'
)


def refuse_native_windows():
    """Stop before loading Unix-only execution modules; help remains portable."""
    if platform.system() != 'Windows':
        return False
    print(json.dumps({'status': 'stopped', 'reason': WINDOWS_GUIDANCE}))
    return True


def require_experiment_host():
    """Real data and model calls require a supported OS and a designated experiment checkout."""
    if platform.system() not in {'Darwin', 'Linux'}:
        raise Rejected('Full execution requires macOS sandbox-exec or Linux bubblewrap; no isolation fallback is provided')
    if not (Path.cwd() / HOST_MARKER).is_file():
        raise Rejected(f'Run from the designated experiment checkout containing {HOST_MARKER}')


def build_parser(prog='jockeybench'):
    summary, exit_codes = __doc__.split('\n\n', 1)
    parser = argparse.ArgumentParser(prog=prog, description=summary, epilog=' '.join(exit_codes.split()) + ' ' + WINDOWS_GUIDANCE)
    parser.add_argument('--config', help='JSON configuration (defaults: the paper protocol)')
    parser.add_argument('--arm', choices=ARMS, help='override the configured arm')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='read-only source preflight: manifest, code hash and schedule')
    p = sub.add_parser('run', help='run or resume a trajectory (checks sandbox isolation first)')
    p.add_argument('--run-id', required=True)
    p.add_argument('--reference', choices=KINDS, default=DEFAULT_KIND, help='reference program used when no provider is given')
    p.add_argument('--reference-samples', type=int, default=DEFAULT_SAMPLES, help='support races the reference fits on')
    p.add_argument('--provider-config', help='OpenAI-compatible provider settings; opt-in to external model calls')
    p.add_argument('--initial-from', help='start from the completed warmup of another run in the same root')
    p.add_argument('--stop-after', type=int, help='pause after this many sealed races')
    p.add_argument('--warmup-only', action='store_true')
    for name, text in [
        ('report', 'print a stored report'),
        ('replay-audit', 'replay responses and scores of a checkpoint'),
        ('reexecute-audit', 're-execute sealed strategies and compare predictions'),
    ]:
        sub.add_parser(name, help=text).add_argument('--run-id', required=True)
    p = sub.add_parser('compare', help='paired comparison of two report.json.gz files')
    p.add_argument('left')
    p.add_argument('right')
    p = sub.add_parser('data', help='import privately acquired JRDB archives')
    from .importer.cli import add_arguments

    add_arguments(p)
    return parser


def make_agent(args):
    if not args.provider_config:
        from .agents.reference import ProgramReference

        return ProgramReference(args.reference, args.reference_samples)
    from .agents import provider
    from .agents.compatible import CompatibleAgent

    settings = json.loads(Path(args.provider_config).read_text())
    provider.require_prices(settings)
    provider.require_credential(settings)
    return CompatibleAgent(settings)


def execute(args, config):
    if args.command == 'compare':
        from .reporting import compare

        reports = []
        for path in (args.left, args.right):
            with gzip.open(path, 'rt') as f:
                reports.append(json.load(f))
        return compare(*reports)
    if args.command == 'data':
        from .importer.cli import execute as import_data

        return import_data(args)
    if args.command == 'report':
        with gzip.open(Path(config.managed_root) / args.run_id / 'report.json.gz', 'rt') as f:
            return json.load(f)
    require_experiment_host()
    if args.command == 'doctor':
        from .canonical import code_hash
        from .data.source import Source

        source = Source(config.source, config.limits.cache_mib)
        try:
            return {
                'source': source.manifest(),
                'code': code_hash(),
                'schedule': source.schedule(config.start, config.end, config.smoke_days, config.races_per_day, config.schedule_mode),
            }
        finally:
            source.close()
    if args.command in {'replay-audit', 'reexecute-audit'}:
        from .storage import read_checkpoint

        checkpoint = read_checkpoint(Path(config.managed_root) / args.run_id / 'checkpoint.json.gz')
        if args.command == 'replay-audit':
            from .audit.replay import replay

            return replay(config, checkpoint)
        from .audit.reexecution import reexecute

        return reexecute(config, checkpoint)
    from .runner import Run

    run = Run(config, args.run_id, make_agent(args), args.initial_from)
    try:
        return run.run(stop_after=args.stop_after, warmup_only=args.warmup_only)
    finally:
        run.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (['--help'], ['-h']):
        parser = argparse.ArgumentParser(prog='jockeybench', description='Forecasting workflows in JockeyBench.')
        commands = parser.add_subparsers(dest='setting', metavar='{code-as-policy,llm-as-policy}')
        commands.add_parser('code-as-policy', aliases=['program'], help='Build and revise an executable predictor (A/S)')
        commands.add_parser(
            'llm-as-policy', aliases=['research'], help='Run an evidence plan and forecast each race with an LLM (Adaptive/Frozen)'
        )
        parser.epilog = (
            'Use jockeybench code-as-policy --help or jockeybench llm-as-policy --help. '
            'The aliases program and research remain available. Unprefixed commands select code-as-policy. ' + WINDOWS_GUIDANCE
        )
        parser.print_help()
        return 0
    workflow = argv[0]
    if workflow.lower() in ('code-as-policy', 'llm-as-policy'):
        workflow = workflow.lower()
    if workflow in ('llm-as-policy', 'research'):
        from .research.cli import main as research_main

        return research_main(argv[1:], prog=f'jockeybench {workflow}')
    prog = 'jockeybench'
    if workflow in ('code-as-policy', 'program'):
        prog = f'jockeybench {workflow}'
        argv = argv[1:]
    args = build_parser(prog).parse_args(argv)
    if refuse_native_windows():
        return 2
    from .storage import valid_run_id

    if getattr(args, 'run_id', None) is not None and not valid_run_id(args.run_id):
        print(json.dumps({'status': 'stopped', 'reason': 'Invalid run name'}))
        return 2
    try:
        config = Config.load(args.config)
        if args.arm:
            config = replace(config, arm=args.arm)
        result = execute(args, config)
    except Pending as exc:
        print(json.dumps({'status': 'pending', 'reason': str(exc)}))
        return 3
    except (Rejected, OSError, ValueError) as exc:
        print(json.dumps({'status': 'stopped', 'reason': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
