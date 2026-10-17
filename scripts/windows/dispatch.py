"""WSL-side launcher bootstrap, executed by the distribution's system Python.

The Windows launcher sends this trusted source and one base64-encoded JSON
payload. User arguments become an argv list; they are never parsed by a shell.
This file intentionally imports neither JockeyBench nor third-party packages.
"""

import base64
import binascii
import json
import os
import re
import subprocess
import sys

PROTOCOL = 'jockeybench-wsl-launcher-v1'
BRIDGE_ERROR = 125


class LauncherError(Exception):
    pass


def decode_payload(encoded):
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise LauncherError('Invalid Windows launcher payload') from exc
    if not isinstance(payload, dict) or set(payload) != {'protocol', 'project', 'action', 'arguments'}:
        raise LauncherError('Invalid Windows launcher payload fields')
    if payload['protocol'] != PROTOCOL or not isinstance(payload['action'], str) or payload['action'] not in {'setup', 'check', 'run'}:
        raise LauncherError('Unsupported Windows launcher protocol or action')
    project, arguments = payload['project'], payload['arguments']
    if not isinstance(project, str) or not project.startswith('/home/') or '\0' in project:
        raise LauncherError('ProjectPath must be an absolute Linux path inside /home')
    if not isinstance(arguments, list) or any(not isinstance(arg, str) or '\0' in arg for arg in arguments):
        raise LauncherError('BenchmarkArgs must contain strings without NUL characters')
    return payload


def dispatch(payload):
    uname = os.uname()
    if uname.sysname != 'Linux' or not re.search(r'microsoft.*wsl2', uname.release, re.I):
        raise LauncherError('Use a WSL 2 distribution; WSL 1 and native Windows execution are unsupported')
    project = os.path.realpath(payload['project'])
    if not project.startswith('/home/') or not os.path.isdir(project):
        raise LauncherError('ProjectPath must resolve to an existing Linux project inside /home, not a Windows-mounted directory')
    try:
        filesystem = subprocess.run(
            ['/usr/bin/stat', '-f', '-c', '%T', '--', project], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError('Could not verify the Linux project filesystem') from exc
    if filesystem not in {'ext2/ext3', 'ext2', 'ext3', 'ext4'}:
        raise LauncherError('Keep the project on the WSL Linux ext4 filesystem under /home; Windows mounts are unsupported')
    if not os.path.isfile(os.path.join(project, 'pyproject.toml')):
        raise LauncherError('ProjectPath is not the JockeyBench repository root (pyproject.toml is missing)')
    action = payload['action']
    if action == 'run':
        executable = os.path.join(project, '.venv', 'bin', 'jockeybench')
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise LauncherError('The Linux environment is missing; run the launcher with -Action setup first')
        command = [executable, *payload['arguments']]
    else:
        setup = os.path.join(project, 'scripts', 'windows', 'setup.sh')
        if not os.path.isfile(setup):
            raise LauncherError('The Linux project is missing scripts/windows/setup.sh; synchronize the current checkout first')
        command = ['/bin/bash', setup, *(['--check'] if action == 'check' else []), *payload['arguments']]
    os.chdir(project)
    os.execv(command[0], command)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise LauncherError('Expected one encoded Windows launcher payload')
        dispatch(decode_payload(args[0]))
    except (LauncherError, OSError) as exc:
        print('JockeyBench Windows launcher: ' + str(exc), file=sys.stderr)
        return BRIDGE_ERROR
    return 0  # os.execv replaces this process during normal execution.


if __name__ == '__main__':
    sys.exit(main())
