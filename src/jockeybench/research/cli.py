"""Run the portable Active Research setting from the common installation."""

import argparse
import json
from dataclasses import replace

from jockeybench.config import Config
from jockeybench.errors import Pending, Rejected

from .profile import ARMS, provider_settings, validate_config


def build_parser():
    from jockeybench.cli import WINDOWS_GUIDANCE

    parser = argparse.ArgumentParser(prog='jockeybench research', description=__doc__, epilog=WINDOWS_GUIDANCE)
    parser.add_argument('--config', help='Shared engine configuration; research requires hit_bonus_yen=0')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Check the source and planned schedule on the designated experiment host')
    run = sub.add_parser('run', help='Start a fresh Active or matched Frozen trajectory; never resumes')
    run.add_argument('--run-id', required=True)
    run.add_argument('--provider-config', required=True, help='Non-secret provider settings with explicit prices and limits')
    run.add_argument('--arm', choices=tuple(ARMS), default='adaptive')
    run.add_argument('--initial-from', help='Completed Adaptive run in the same managed root; required for Frozen')
    for name in ('report', 'replay-audit'):
        sub.add_parser(name).add_argument('--run-id', required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    from jockeybench.cli import refuse_native_windows

    if refuse_native_windows():
        return 2
    try:
        config = Config.load(args.config)
        if args.config is None:
            config = replace(config, tariff=replace(config.tariff, hit_bonus_yen=0))
        validate_config(config)
        from .public import audit, execute, run_path

        if getattr(args, 'run_id', None):
            run_path(config, args.run_id)
        if args.command == 'report':
            result = json.loads((run_path(config, args.run_id) / 'report.json').read_text())
        else:
            from jockeybench.cli import require_experiment_host

            require_experiment_host()
            if args.command == 'doctor':
                from jockeybench.cli import execute as program_execute

                result = program_execute(args, config)
                result.update(setting='active_research', profile='jockeybench-research-v1')
            elif args.command == 'replay-audit':
                result = audit(config, args.run_id)
            else:
                from pathlib import Path

                from jockeybench.agents.provider import require_credential

                settings = provider_settings(json.loads(Path(args.provider_config).read_text()))
                require_credential(settings)
                result = execute(config, args.run_id, settings, arm=args.arm, initial_from=args.initial_from)
    except (Pending, Rejected, OSError, ValueError) as exc:
        print(json.dumps({'status': 'stopped', 'reason': str(exc), 'restart_allowed': False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
