"""PEF load tests (Locust). See GUIA_LOCUST.md.

    python -m locust --config tests/load/profiles/smoke.conf      # from the repository root

User mix (weights): Operator 4, SPD 3, App (or_charge) 3, Admin 1, plus PEF_FLOW_USERS fixed session flows.
Exit code 1 if a threshold is exceeded (read p95, write p95, error ratio).
"""
from __future__ import annotations

import logging
import os

from locust import LoadTestShape, events

from users.admin import AdminUser            # noqa: F401  (Locust discovers the classes by import)
from users.app import AppUser                # noqa: F401
from users.flow import SessionFlowUser       # noqa: F401
from users.operator import OperatorUser      # noqa: F401
from users.supervisor import SupervisorUser  # noqa: F401

MAX_READ_P95_MS = float(os.environ.get('PEF_MAX_READ_P95_MS', '800'))
MAX_WRITE_P95_MS = float(os.environ.get('PEF_MAX_WRITE_P95_MS', '2000'))
MAX_FLOW_P95_MS = float(os.environ.get('PEF_MAX_FLOW_P95_MS', '20000'))
MAX_ERROR_RATIO = float(os.environ.get('PEF_MAX_ERROR_RATIO', '0.01'))

log = logging.getLogger('pef.thresholds')


@events.quitting.add_listener
def check_thresholds(environment, **_):
    stats = environment.stats
    problems = []
    if stats.total.num_requests and stats.total.fail_ratio > MAX_ERROR_RATIO:
        problems.append(f'error ratio {stats.total.fail_ratio:.2%} > {MAX_ERROR_RATIO:.2%}')
    for entry in stats.entries.values():
        if not entry.num_requests:
            continue
        limit = (MAX_FLOW_P95_MS if entry.method == 'FLOW'
                 else MAX_READ_P95_MS if entry.method == 'GET' else MAX_WRITE_P95_MS)
        p95 = entry.get_response_time_percentile(0.95)
        if p95 > limit:
            problems.append(f'p95 {entry.method} {entry.name} = {p95:.0f} ms > {limit:.0f} ms')
    if problems:
        log.error('THRESHOLDS FAILED:\n  ' + '\n  '.join(problems))
        environment.process_exit_code = 1
    else:
        log.info('Thresholds OK (read p95 <= %.0f ms, write p95 <= %.0f ms, errors <= %.2f%%)',
                 MAX_READ_P95_MS, MAX_WRITE_P95_MS, MAX_ERROR_RATIO * 100)


if os.environ.get('PEF_LOAD_SHAPE') == 'stress':
    class StressShape(LoadTestShape):
        """Steps of 50 users up to PEF_STRESS_MAX_USERS (default 200), STEP seconds each, then stop."""
        step_users = 50
        step_seconds = int(os.environ.get('PEF_STRESS_STEP_SECONDS', '180'))
        max_users = int(os.environ.get('PEF_STRESS_MAX_USERS', '200'))

        def tick(self):
            elapsed = self.get_run_time()
            step = int(elapsed // self.step_seconds) + 1
            users = step * self.step_users
            if users > self.max_users:
                return None
            return users, 5
