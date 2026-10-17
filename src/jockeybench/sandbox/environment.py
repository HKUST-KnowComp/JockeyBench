"""The interpreter environment offered to sandboxed programs, recorded in every trajectory."""

import json
import subprocess
from functools import lru_cache

THREAD_VARIABLES = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')


def execution_env(limits):
    """Deterministic hashing and a fixed number of numerical threads."""
    return {'PYTHONHASHSEED': '0', **{k: str(limits.numerical_threads) for k in THREAD_VARIABLES}}


@lru_cache(maxsize=16)
def probe(python, threads=4):
    code = '''import importlib, json, platform
available = {}
for name in ('numpy', 'scipy', 'sklearn', 'pandas', 'statsmodels'):
    try:
        importlib.import_module(name)
        available[name] = True
    except Exception:
        available[name] = False
print(json.dumps({'python_version': platform.python_version(), 'imports': available, 'machine': platform.machine(),
                  'system': platform.system(), 'system_release': platform.release()}))
'''
    env = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONHASHSEED': '0', **{k: str(threads) for k in THREAD_VARIABLES}}
    result = subprocess.run([python, '-B', '-s', '-c', code], env=env, capture_output=True, text=True, check=True, timeout=60)
    return {
        **json.loads(result.stdout),
        'numerical_threads': threads,
        'hash_seed': 0,
        'isolation': 'no network, no subprocess, workspace-only file access',
        'runtime_exception': 'Read-only interpreter and installed libraries are available.',
    }
