"""MS-10: reproducible schema check on a FRESH database + concurrent start-up of the services that apply migrations.

Isolated test PostgreSQL only (127.0.0.1:55632). Creates `pef_ms10_fresh` (dropped and recreated each run; it is a
throw-away database of this check, never the shared test database `pef_test` and never a volume):

1. applies 001, seeds 002/003, 004..019 in order (like docker-entrypoint-initdb.d), then 002 and 004..019 a
   SECOND time (idempotency; 001 and the demo seed 003 run once, as in initdb), and checks the key objects (tables, triggers, the sync backfill marker);
2. starts at the same time, ROUNDS times, MonitoringService API + collector, NotificationsService API + worker and
   IntegrationService API + worker against that database (each applies its migrations at start-up under the shared
   advisory lock `pef:schema:migration`) and requires every process to become healthy / stay alive and no
   "deadlock detected" in any log.

    python scripts/check_migrations_concurrency.py [--rounds 5]
"""
import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402

FRESH = 'pef_ms10_fresh'
ORDER = (['data/migrations/001_init.sql', 'data/seeds/002_seed_catalogs.sql', 'data/seeds/003_seed_demo.sql']
         + [f'data/migrations/{p.name}' for p in sorted((ROOT / 'data/migrations').glob('0*.sql')) if p.name >= '004'])
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:300] if detail and not ok else ''}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    env = load_test_env()
    base = env['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
    assert '127.0.0.1:55632' in base, 'isolated test database only'
    admin_url = base.rsplit('/', 1)[0] + '/postgres'
    fresh_url = base.rsplit('/', 1)[0] + f'/{FRESH}'
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {FRESH} WITH (FORCE)')
        conn.execute(f'CREATE DATABASE {FRESH}')
    for attempt in (1, 2):
        try:
            with psycopg.connect(fresh_url) as conn:
                for rel in ORDER:
                    if attempt == 2 and ('seeds/003' in rel or '001_init' in rel):
                        continue          # base schema and demo seed run once (initdb); 002 and 004..016 are idempotent
                    conn.execute((ROOT / rel).read_text(encoding='utf-8'))
                conn.commit()
            applied = len(ORDER) if attempt == 1 else len(ORDER) - 2
            check(f'fresh database: {applied} files applied (pass {attempt})', True)
            if attempt == 1:
                # MS-11 regression: rows of NEWER types (017/018) must not break re-applying older migrations
                # (013 redefines the notification type constraint; NotificationsService re-applies it at every start).
                with psycopg.connect(fresh_url, autocommit=True) as conn:
                    conn.execute("INSERT INTO service_event (component, kind, status) VALUES ('catalog', 'down', 'down')")
                    user, inst = conn.execute('SELECT id, institution_id FROM "user" LIMIT 1').fetchone()
                    conn.execute("INSERT INTO notification (source_table, source_id, recipient_user_id, institution_id, type, data) "
                                 "VALUES ('access_audit', gen_random_uuid(), %s, %s, 'final_count_escalated', '{}'::jsonb)", (user, inst))
                    station = conn.execute('SELECT s.id, r.institution_id FROM capture_station s '
                                           'JOIN operating_room r ON r.id = s.room_id LIMIT 1').fetchone()
                    conn.execute("INSERT INTO capture_camera (institution_id, station_id, name, device_type) "
                                 "VALUES (%s, %s, 'MS-11 simulated', 'simulated')", (station[1], station[0]))
                check('rows of the MS-11 types written between passes (service_event, notification, simulated camera)', True)
        except psycopg.Error as exc:
            check(f'fresh database: files applied (pass {attempt})', False, f'{type(exc).__name__}: {exc}')
            return finish()
    with psycopg.connect(fresh_url) as conn:
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        triggers = {r[0] for r in conn.execute('SELECT tgname FROM pg_trigger WHERE NOT tgisinternal')}
        seeded = conn.execute('SELECT count(*) FROM sync_seed').fetchone()[0]
    needed = {'work_session', 'notification_outbox', 'monitoring_check', 'capture_camera', 'station_event',
              'notification_delivery', 'sync_change', 'sync_operation', 'integration_delivery', 'surgical_tray',
              'surgical_tray_history', 'service_event', 'yolo_model_evaluation',
              # MS-12 (019)
              'kit_item_equivalence', 'capture_camera_calibration', 'tracking_event', 'yolo_model_regression_run',
              'integration_export_batch', 'integration_export_item'}
    check('fresh schema has every MS-01..MS-12 table', needed <= tables, sorted(needed - tables))
    check('fresh schema has the MS-05..MS-12 triggers', {'trg_sync_kit', 'trg_sync_surgical_tray',
                                                        'trg_surgical_tray_history', 'trg_work_session_tray_same_institution',
                                                        'trg_notification_station_event', 'trg_notification_service_event',
                                                        'trg_yolo_model_evaluation_immutable',
                                                        'trg_capture_camera_calibration_immutable',
                                                        'trg_yolo_model_regression_immutable',
                                                        'trg_integration_export_batch_immutable',
                                                        'trg_tracking_event_immutable'} <= triggers,
          sorted(t for t in triggers if t.startswith('trg_')))
    check('sync backfill ran once per resource type', seeded >= 8, seeded)

    sqlalchemy_url = fresh_url.replace('postgresql://', 'postgresql+psycopg://')
    jwt_secret = secrets.token_hex(32)
    logs = ROOT / '.e2e'
    for round_no in range(1, args.rounds + 1):
        common = {**os.environ, **env, 'DATABASE_URL': sqlalchemy_url, 'JWT_SECRET_KEY': jwt_secret,
                  'PYTHONIOENCODING': 'utf-8'}
        specs = [
            ('monitoring', 'MonitoringService', 'main.py', {'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': '15108',
                                                            'MONITORING_MONGO_URL': ''}, 15108),
            ('collector', 'MonitoringService', 'collector.py', {'MONITORING_MONGO_URL': '', 'MONITORING_INTERVAL_SECONDS': '5'}, None),
            ('notifications', 'NotificationsService', 'main.py', {'NOTIFICATIONS_HOST': '127.0.0.1',
                                                                  'NOTIFICATIONS_PORT': '15107'}, 15107),
            ('notif_worker', 'NotificationsService', 'worker.py', {}, None),
            ('integration', 'IntegrationService', 'main.py', {'INTEGRATION_HOST': '127.0.0.1', 'INTEGRATION_PORT': '15109'}, 15109),
            ('int_worker', 'IntegrationService', 'worker.py', {}, None),
        ]
        procs, files = {}, {}
        for name, folder, script, extra, port in specs:      # all at once
            files[name] = logs / f'ms10_concurrency_{name}.log'
            procs[name] = subprocess.Popen([sys.executable, script], cwd=ROOT / 'apps' / folder, env={**common, **extra},
                                           stdout=open(files[name], 'w'), stderr=subprocess.STDOUT)
        healthy = {}
        deadline = time.time() + 90
        for name, folder, script, extra, port in specs:
            if port is None:
                continue
            while time.time() < deadline:
                try:
                    if requests.get(f'http://127.0.0.1:{port}/health/live', timeout=2).ok:
                        healthy[name] = True
                        break
                except requests.RequestException:
                    pass
                if procs[name].poll() is not None:
                    break
                time.sleep(0.5)
        time.sleep(3)
        alive = {name: procs[name].poll() is None for name in procs}
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        deadlocks = [name for name, path in files.items() if 'deadlock detected' in path.read_text(encoding='utf-8', errors='ignore')]
        check(f'round {round_no}: 6 processes started together, 3 APIs healthy, workers alive, no deadlock',
              len(healthy) == 3 and all(alive.values()) and not deadlocks, (healthy, alive, deadlocks))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {FRESH} WITH (FORCE)')
    return finish()


def finish():
    print(f'\n{sum(results)}/{len(results)} checks passed')
    sys.exit(0 if results and all(results) else 1)


if __name__ == '__main__':
    main()
