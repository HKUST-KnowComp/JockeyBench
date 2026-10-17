#!/usr/bin/env bash
# Install in a fresh Linux checkout (including WSL2); never designate an experiment host.
set -euo pipefail

fail() { printf '%s\n' "JockeyBench setup: $*" >&2; exit 2; }
check_only=false
install_system=false
for argument in "$@"; do
    case "$argument" in
        --check) check_only=true ;;
        --install-system) install_system=true ;;
        --help|-h)
            printf '%s\n' 'Usage: bash scripts/windows/setup.sh [--install-system | --check]' \
                'Default: install Python 3.13.2 and locked dependencies into a fresh .venv.' \
                '--install-system: also install Ubuntu system packages with sudo.' \
                '--check: run synthetic sandbox tests in the existing environment; no installation.'
            exit 0 ;;
        *) fail "Unknown option: $argument" ;;
    esac
done
if "$check_only" && "$install_system"; then
    fail '--check and --install-system cannot be combined'
fi
[[ $(uname -s) == Linux ]] || fail 'Use this installer inside Linux or WSL2, not Windows Python or macOS.'
kernel=$(uname -r | tr '[:upper:]' '[:lower:]')
if [[ $kernel == *microsoft* && $kernel != *wsl2* ]]; then
    fail 'WSL1 is unsupported; convert this distribution to WSL2 first.'
fi
[[ $(id -u) != 0 ]] || fail 'Run as your normal Linux user; use --install-system for the sudo package step.'
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$project"
case "$(stat -f -c %T .)" in
    9p|drvfs|ntfs|ntfs3|fuseblk|cifs|smb*) fail 'Keep the project and its environment on the Linux filesystem, not a Windows/shared drive.' ;;
esac
[[ -f pyproject.toml && -f requirements-lock.txt ]] || fail 'Run from a complete JockeyBench source checkout.'

if ! "$check_only"; then
    [[ ! -e .experiment-host ]] || fail 'This checkout is designated for experiments. Install a fresh checkout instead of changing its environment.'
    [[ ! -e .venv && ! -L .venv ]] || fail 'An environment already exists. Use --check, or install in a fresh checkout.'
fi
if "$install_system"; then
    command -v apt-get >/dev/null || fail '--install-system requires Ubuntu/Debian apt-get.'
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv git ca-certificates bubblewrap libseccomp2 libarchive-tools lsof build-essential
fi
for required in python3 bwrap bsdtar lsof cc; do
    command -v "$required" >/dev/null || fail "Missing $required; install the documented system dependencies or use --install-system."
done

if ! "$check_only"; then
    bootstrap=$(mktemp -d -t jockeybench-bootstrap.XXXXXXXX)
    trap 'rm -rf -- "$bootstrap"' EXIT
    python3 -m venv "$bootstrap/venv"
    "$bootstrap/venv/bin/python" -m pip install 'uv==0.13.1'
    uv_command="$bootstrap/venv/bin/uv"
    "$uv_command" python install 3.13.2
    "$uv_command" venv --python 3.13.2 --managed-python .venv
    "$uv_command" pip install --python .venv/bin/python -r requirements-lock.txt
    "$uv_command" pip install --python .venv/bin/python --no-deps --no-build-isolation -e .
    "$uv_command" pip check --python .venv/bin/python
fi

[[ -x .venv/bin/python ]] || fail 'No Linux .venv was found. Run setup first.'
.venv/bin/python -I -c 'import ctypes, sys; assert sys.platform == "linux" and sys.version_info[:2] == (3, 13), "Python 3.13 on Linux is required"; ctypes.CDLL("libseccomp.so.2")'
.venv/bin/jockeybench program --help
.venv/bin/jockeybench research --help
# Existing tests exercise the actual file/network/process boundary, NumPy threads,
# timeout cleanup, and both experiment lifecycles using fabricated in-memory data.
# No provider credentials, source database, or .experiment-host marker are needed.
PYTEST_ADDOPTS='' PYTEST_PLUGINS='' PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$project/src" JRDB_NATIVE=1 .venv/bin/python -m pytest -q -m native \
    tests/test_sandbox.py tests/test_linux_sandbox.py tests/test_trajectory.py \
    tests/test_research_warmup.py tests/test_research_lifecycle.py
printf '%s\n' 'JockeyBench setup/check passed using synthetic data only. No experiment host was designated.'
