"""Installer refusal and check-only tests. No package downloads or sudo calls."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('setup.sh')


@unittest.skipIf(os.name == 'nt', 'The Linux installer is exercised inside WSL2, not native Windows.')
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jockeybench-setup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts/windows').mkdir(parents=True)
        shutil.copyfile(SCRIPT, self.root / 'scripts/windows/setup.sh')
        (self.root / 'pyproject.toml').touch()
        (self.root / 'requirements-lock.txt').touch()
        self.bin = self.root / 'fake-bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        self.fake('uname', 'if [ "$1" = -s ]; then echo Linux; else echo 6.6.87-microsoft-standard-WSL2; fi')
        self.fake('id', 'echo 1000')
        self.fake('stat', 'echo ext2/ext3')
        for name in ('python3', 'bwrap', 'bsdtar', 'lsof', 'cc', 'sudo'):
            self.fake(name, 'echo "Unexpected installation or system call" >&2; exit 91')

    def fake(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def invoke(self, *args):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/windows/setup.sh'), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def refused(self, needle, *args):
        result = self.invoke(*args)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(needle, result.stderr)
        self.assertNotIn('Unexpected installation', result.stderr)
        self.assertFalse((self.root / '.experiment-host').exists())

    def test_unknown_and_conflicting_options(self):
        self.refused('Unknown option', '--unsafe')
        self.refused('cannot be combined', '--check', '--install-system')

    def test_os_and_wsl1_are_rejected_before_install(self):
        self.fake('uname', 'echo Darwin')
        self.refused('inside Linux or WSL2')
        self.fake('uname', 'if [ "$1" = -s ]; then echo Linux; else echo 4.4.0-Microsoft; fi')
        self.refused('WSL1 is unsupported')

    def test_shared_filesystems_are_rejected(self):
        for filesystem in ('9p', 'drvfs', 'ntfs3', 'cifs'):
            with self.subTest(filesystem=filesystem):
                self.fake('stat', 'echo ' + filesystem)
                self.refused('Linux filesystem')

    def test_root_is_rejected(self):
        self.fake('id', 'echo 0')
        self.refused('normal Linux user')

    def test_existing_environment_is_not_changed(self):
        (self.root / '.venv').mkdir()
        sentinel = self.root / '.venv/preserved'
        sentinel.write_text('preserved')
        self.refused('environment already exists')
        self.assertEqual(sentinel.read_text(), 'preserved')

    def test_check_requires_an_installed_environment(self):
        self.refused('No Linux .venv was found', '--check')

    def test_check_runs_synthetic_native_tests_and_preserves_failure(self):
        venv_bin = self.root / '.venv/bin'
        venv_bin.mkdir(parents=True)
        python = venv_bin / 'python'
        python.write_text(
            '#!/bin/sh\nif [ "$1" = -m ]; then '
            '[ "$JRDB_NATIVE" = 1 ] && [ -z "$PYTEST_ADDOPTS" ] && [ -z "$PYTEST_PLUGINS" ] '
            '&& [ "$PYTEST_DISABLE_PLUGIN_AUTOLOAD" = 1 ] || exit 92; exit 73; fi\n'
        )
        python.chmod(0o755)
        cli = venv_bin / 'jockeybench'
        cli.write_text('#!/bin/sh\nexit 0\n')
        cli.chmod(0o755)
        self.env.update(PYTEST_ADDOPTS='--collect-only', PYTEST_PLUGINS='unexpected_plugin')
        result = self.invoke('--check')
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertNotIn('passed', result.stdout)
        self.assertFalse((self.root / '.experiment-host').exists())


if __name__ == '__main__':
    unittest.main()
