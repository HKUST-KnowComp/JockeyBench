"""Standalone Windows bridge tests: stdlib only, no benchmark imports or data.

Run with: python scripts/windows/test_launcher.py
"""

import base64
import contextlib
import importlib.util
import io
import json
import posixpath
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location('jockeybench_wsl_dispatch', Path(__file__).with_name('dispatch.py'))
dispatcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dispatcher)
PROJECT = '/home/' + 'synthetic/Jockey Bench'


def encoded(**changes):
    value = {'protocol': dispatcher.PROTOCOL, 'project': PROJECT, 'action': 'run', 'arguments': []}
    value.update(changes)
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        # All OS interactions are mocked. No Linux process or archive is opened.
        names = {
            'uname': types.SimpleNamespace(sysname='Linux', release='6.6.87.2-microsoft-standard-WSL2'),
            'path.realpath': PROJECT,
            'path.isdir': True,
            'path.isfile': True,
            'access': True,
            'chdir': None,
            'execv': None,
        }
        self.os = {}
        for name, value in names.items():
            mocked = patch.object(
                dispatcher.os if '.' not in name else dispatcher.os.path, name.split('.')[-1], return_value=value, create=name == 'uname'
            )
            self.os[name] = mocked.start()
            self.addCleanup(mocked.stop)
        join = patch.object(dispatcher.os.path, 'join', side_effect=posixpath.join)
        join.start()
        self.addCleanup(join.stop)
        self.stat = patch.object(dispatcher.subprocess, 'run', return_value=types.SimpleNamespace(stdout='ext2/ext3\n')).start()
        self.addCleanup(patch.stopall)

    def call(self, **changes):
        return dispatcher.main([encoded(**changes)])

    def test_literal_argv_preserves_spaces_quotes_unicode_empty_and_shell_text(self):
        arguments = [
            'research',
            '',
            'two words',
            'quote"here',
            'trailing\\',
            'a\\\\"b',
            '中文',
            '$HOME',
            '$(touch invalid)',
            '; exit 7',
            'a\nb',
        ]
        self.assertEqual(self.call(arguments=arguments), 0)
        target = PROJECT + '/.venv/bin/jockeybench'
        self.os['execv'].assert_called_once_with(target, [target, *arguments])
        self.os['chdir'].assert_called_once_with(PROJECT)
        self.stat.assert_called_once_with(['/usr/bin/stat', '-f', '-c', '%T', '--', PROJECT], check=True, capture_output=True, text=True)

    def test_setup_and_check_pass_arguments_as_an_array(self):
        target = PROJECT + '/scripts/windows/setup.sh'
        for action, prefix in [('setup', []), ('check', ['--check'])]:
            with self.subTest(action=action):
                self.os['execv'].reset_mock()
                self.assertEqual(self.call(action=action, arguments=['--install-system', 'space value']), 0)
                self.os['execv'].assert_called_once_with('/bin/bash', ['/bin/bash', target, *prefix, '--install-system', 'space value'])

    def test_wsl1_and_non_wsl_are_rejected_before_access(self):
        for system, release in [('Linux', '4.4.0-Microsoft'), ('Linux', '6.8.0-generic'), ('Windows', 'microsoft-WSL2')]:
            with self.subTest(release=release), contextlib.redirect_stderr(io.StringIO()) as output:
                self.os['uname'].return_value = types.SimpleNamespace(sysname=system, release=release)
                self.assertEqual(self.call(), dispatcher.BRIDGE_ERROR)
                self.assertIn('WSL 2', output.getvalue())
        self.os['execv'].assert_not_called()
        self.stat.assert_not_called()

    def test_project_must_resolve_to_linux_home_ext4(self):
        for path, filesystem in [('/mnt/c/project', 'ext2/ext3'), (PROJECT, '9p'), (PROJECT, 'drvfs'), ('/home', 'ext2/ext3')]:
            with self.subTest(path=path, filesystem=filesystem), contextlib.redirect_stderr(io.StringIO()):
                self.os['path.realpath'].return_value = path
                self.stat.return_value.stdout = filesystem
                self.assertEqual(self.call(), dispatcher.BRIDGE_ERROR)
        self.os['execv'].assert_not_called()

    def test_missing_setup_and_environment_fail_cleanly(self):
        for action, expected in [('setup', 'setup.sh'), ('run', 'environment')]:
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()) as output:
                self.os['path.isfile'].side_effect = lambda path: path.endswith('pyproject.toml')
                self.assertEqual(self.call(action=action), dispatcher.BRIDGE_ERROR)
                self.assertIn(expected, output.getvalue())
        self.os['execv'].assert_not_called()

    def test_missing_project_is_rejected(self):
        self.os['path.isdir'].return_value = False
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(self.call(), dispatcher.BRIDGE_ERROR)
        self.assertIn('existing Linux project', output.getvalue())
        self.os['execv'].assert_not_called()

    def test_os_error_returns_actionable_bridge_exit(self):
        self.os['execv'].side_effect = OSError('synthetic exec failure')
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(self.call(), dispatcher.BRIDGE_ERROR)
        self.assertIn('synthetic exec failure', output.getvalue())

    def test_invalid_payloads_never_launch(self):
        invalid = [
            'bad base64',
            encoded(protocol='wrong'),
            encoded(project='C:\\project'),
            encoded(project='/mnt/c/project'),
            encoded(arguments=['bad\0arg']),
            encoded(arguments=[None]),
            encoded(action='remove'),
            encoded(action={}),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(dispatcher.main([payload]), dispatcher.BRIDGE_ERROR)
        self.os['execv'].assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
