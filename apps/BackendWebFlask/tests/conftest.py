"""Pytest wrappers for the scenario suites in tests/scenarios/.

Each scenario is a standalone script that builds its own throwaway SQLite database and patches
module-level state (config, routes._current_user), so every scenario runs in a separate Python
process. A scenario passes when it exits with code 0 ("ALL PASSED"); its individual checks are
printed as "ok"/"FAIL" lines and shown by pytest on failure.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCENARIOS = Path(__file__).resolve().parent / 'scenarios'


def run(name):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PEF_TEST_WORKDIR=tempfile.mkdtemp(prefix=f'pef-{name}-'))
    result = subprocess.run([sys.executable, str(SCENARIOS / f'{name}.py')], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=env, cwd=str(SCENARIOS), timeout=900)
    output = result.stdout + result.stderr
    passed = sum(1 for line in output.splitlines() if line.startswith('  ok '))
    failed = [line for line in output.splitlines() if line.startswith('  FAIL')]
    print(f'{name}: {passed} checks passed, {len(failed)} failed')
    assert result.returncode == 0 and not failed, output[-6000:]
    return passed


@pytest.fixture
def run_scenario():
    return run
