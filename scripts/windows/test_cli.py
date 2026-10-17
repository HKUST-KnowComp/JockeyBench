"""Native Windows CLI checks, runnable without the POSIX-only fixture suite.

Run with ``python scripts/windows/test_cli.py``. Each child forbids Unix-only
imports so the same regression checks also run on macOS and Linux.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = """
import importlib.abc
import platform
import runpy
import sys

class NoUnixRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'fcntl', 'resource', 'jockeybench.storage', 'jockeybench.sandbox.executor'}:
            raise AssertionError('Unix-only runtime imported: ' + fullname)

sys.meta_path.insert(0, NoUnixRuntime())
platform.system = lambda: 'Windows'
runpy.run_module('jockeybench', run_name='__main__')
"""


class WindowsCliTests(unittest.TestCase):
    def invoke(self, *arguments):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run(
                [sys.executable, '-B', '-c', BOOTSTRAP, *arguments],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

    def test_help_is_available_without_unix_runtime(self):
        for arguments in ((), ('--help',), ('program', '--help'), ('research', '--help')):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage: jockeybench', result.stdout)
                self.assertIn('WSL2', result.stdout)
                self.assertEqual(result.stderr, '')

    def test_execution_stops_before_config_files_or_unix_runtime(self):
        for arguments in (
            ('doctor',),
            ('program', 'doctor'),
            ('program', '--config', 'missing-config.json', 'run', '--run-id', 'fixture'),
            ('program', 'report', '--run-id', 'fixture'),
            ('program', 'data', 'layouts'),
            ('research', 'doctor'),
            ('research', '--config', 'missing-config.json', 'run', '--run-id', 'fixture', '--provider-config', 'missing.json'),
            ('research', 'replay-audit', '--run-id', 'fixture'),
            ('research', 'report', '--run-id', 'fixture'),
        ):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                message = json.loads(result.stdout)
                self.assertEqual(message['status'], 'stopped')
                self.assertIn('WSL2', message['reason'])
                self.assertIn('Ubuntu 22.04', message['reason'])
                self.assertNotIn('missing-config', message['reason'])
                self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
