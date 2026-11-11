"""MS-01: unified runner for the test suites of the eight services (AuthService + 7 microservices).

    docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait   # isolated stack only
    python scripts/run_ms_tests.py                    # every suite
    python scripts/run_ms_tests.py catalog session    # some suites
    python scripts/run_ms_tests.py --list             # names
    python scripts/run_ms_tests.py vision --build-vision-image   # (re)build pef-ms-test/vision first
    python scripts/run_ms_tests.py vision --weights <best.pt>    # + real-inference tests (MS-02)

Every suite runs against the ISOLATED test stack of docker-compose.test.yml (.env.test): PostgreSQL :55432
(database pef_test), Redis :56379, MongoDB :47017, Garage :59000. The runner refuses to start if the database
URL does not point to that test database, so it can never touch the main stack. Python suites use the Python
that runs this script (install apps/*/requirements-dev.txt in it). VisionWorker needs torch/ultralytics/cv2:
it runs inside the image pef-ms-test/vision built from apps/VisionWorker/Dockerfile (the code is mounted
read-only, pytest is installed in the throw-away container; the weights are not needed by its tests).

Results come from pytest's JUnit XML (never from the console text): passed, failed, errors and skipped per
suite. Failures/errors/skips are classified as INFRA (dependency not reachable or not installed) or CODE.
Skipped tests are never counted as passed. Exit code: 1 if any suite has a failure/error (or could not start),
2 if none failed but some tests were skipped because a dependency was missing (INCOMPLETE), 0 otherwise.
A JSON report is written to .e2e/ms_tests/summary.json (gitignored).
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402

OUT = ROOT / '.e2e' / 'ms_tests'
VISION_IMAGE = os.environ.get('PEF_VISION_TEST_IMAGE', 'pef-ms-test/vision:latest')
EVIDENCE_TEST_BUCKET = 'pef-evidence-test'

SUITES = {
    'auth': ('AuthService', 'BackendAuthService', 'AUTH'),
    'catalog': ('CatalogService', 'CatalogService', 'CATALOG'),
    'session': ('SessionService', 'SessionService', 'SESSION'),
    'evidence': ('EvidenceService', 'EvidenceService', 'EVIDENCE'),
    'audit': ('AuditService', 'AuditService', 'AUDIT'),
    'notifications': ('NotificationsService', 'NotificationsService', 'NOTIFICATIONS'),
    'monitoring': ('MonitoringService', 'MonitoringService', 'MONITORING'),
    'integration': ('IntegrationService', 'IntegrationService', 'INTEGRATION'),
    'vision': ('VisionWorker', 'VisionWorker', None),
}

# Messages that mean "the environment is missing something", not "the code is wrong".
INFRA_PATTERNS = re.compile(
    r'not reachable|not configured|not available|OperationalError|ConnectionError|ConnectionRefused|'
    r'RedisError|ServerSelectionTimeout|EndpointConnectionError|ModuleNotFoundError|ImportError|'
    r'No module named|could not connect|Connection refused|timed out|StorageUnavailable|not usable',
    re.IGNORECASE)


def test_env():
    env = load_test_env()
    url = urlparse(env['DATABASE_URL'].replace('postgresql+psycopg', 'postgresql'))
    configured = 55432
    for line in (ROOT / '.env.test').read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('TEST_POSTGRES_PORT='):
            configured = int(line.split('=', 1)[1].strip())
    if (not url.path.lstrip('/').endswith('test') or url.hostname not in ('127.0.0.1', 'localhost')
            or url.port != configured):
        sys.exit(f'Refusing to run: DATABASE_URL is not the isolated test database ({url.hostname}:{url.port}{url.path}).')
    pg, redis_url = env['DATABASE_URL'], env['REDIS_URL']
    for prefix in ('CATALOG', 'SESSION', 'EVIDENCE', 'AUDIT', 'NOTIFICATIONS', 'MONITORING'):
        env[f'{prefix}_TEST_DATABASE_URL'] = pg
        env[f'{prefix}_TEST_REDIS_URL'] = redis_url
    env['MONITORING_TEST_MONGO_URL'] = env['PEF_TEST_MONGO_URL']
    # EvidenceService Garage tests read EVIDENCE_TEST_S3_* (they override the repository .env).
    env['EVIDENCE_TEST_S3_ACCESS_KEY'] = env['PEF_TEST_S3_ACCESS_KEY']
    env['EVIDENCE_TEST_S3_SECRET_KEY'] = env['PEF_TEST_S3_SECRET_KEY']
    env['EVIDENCE_TEST_S3_API_PORT'] = str(urlparse(env['PEF_TEST_S3_ENDPOINT']).port)
    env['EVIDENCE_TEST_S3_REGION'] = 'garage'
    env['EVIDENCE_TEST_S3_BUCKET_EVIDENCE'] = EVIDENCE_TEST_BUCKET
    return env


def preflight(env):
    """Reachability of the isolated stack (a missing dependency is reported as INFRA, not as a code failure)."""
    targets = {'postgres': urlparse(env['DATABASE_URL'].replace('+psycopg', '')),
               'redis': urlparse(env['REDIS_URL']), 'mongo': urlparse(env['PEF_TEST_MONGO_URL']),
               'garage': urlparse(env['PEF_TEST_S3_ENDPOINT'])}
    status = {}
    for name, url in targets.items():
        try:
            with socket.create_connection((url.hostname, url.port), timeout=2):
                status[name] = 'up'
        except OSError as exc:
            status[name] = f'DOWN ({type(exc).__name__})'
    return status


def ensure_evidence_bucket(env):
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url=env['PEF_TEST_S3_ENDPOINT'], region_name='garage',
                              aws_access_key_id=env['PEF_TEST_S3_ACCESS_KEY'],
                              aws_secret_access_key=env['PEF_TEST_S3_SECRET_KEY'],
                              config=Config(s3={'addressing_style': 'path'}))
        names = {b['Name'] for b in client.list_buckets().get('Buckets', [])}
        if EVIDENCE_TEST_BUCKET not in names:
            client.create_bucket(Bucket=EVIDENCE_TEST_BUCKET)
        return 'ready'
    except Exception as exc:  # reported, the suite will skip its Garage tests
        return f'unavailable ({type(exc).__name__})'


def parse_junit(path):
    cases = []
    if not path.exists():
        return cases
    root = ET.parse(path).getroot()
    for case in root.iter('testcase'):
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome, message = 'passed', ''
        for tag in ('failure', 'error', 'skipped'):
            node = case.find(tag)
            if node is not None:
                outcome = {'failure': 'failed', 'error': 'error', 'skipped': 'skipped'}[tag]
                message = (node.get('message') or '') + ' ' + (node.text or '')[:2000]
                break
        cases.append({'name': name, 'outcome': outcome, 'message': message.strip()})
    return cases


def run_python_suite(key, env, extra):
    label, folder, _ = SUITES[key]
    junit = OUT / f'{key}.xml'
    junit.unlink(missing_ok=True)
    cmd = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}', *extra, 'tests']
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT / 'apps' / folder, env=env, capture_output=True, text=True, timeout=1800)
    (OUT / f'{key}.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
    return proc.returncode, parse_junit(junit), time.monotonic() - started, proc.stdout + proc.stderr


def docker_image_exists(image):
    return subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode == 0


def run_vision_suite(env, extra, build, weights=None):
    junit = OUT / 'vision.xml'
    junit.unlink(missing_ok=True)
    if shutil.which('docker') is None:
        return None, [], 0.0, 'docker not available'
    if build or not docker_image_exists(VISION_IMAGE):
        print(f'  building {VISION_IMAGE} from apps/VisionWorker/Dockerfile ...', flush=True)
        built = subprocess.run(['docker', 'build', '-q', '-t', VISION_IMAGE, str(ROOT / 'apps' / 'VisionWorker')],
                               capture_output=True, text=True, timeout=3600)
        if built.returncode != 0:
            return None, [], 0.0, 'image build failed: ' + built.stderr[-500:]
    mongo = urlparse(env['PEF_TEST_MONGO_URL'])
    mongo_url = env['PEF_TEST_MONGO_URL'].replace(f'@{mongo.hostname}:', '@host.docker.internal:')
    script = ('set -e; cp -r /src /tmp/vw; cd /tmp/vw; pip install -q --user pytest >/dev/null 2>&1; '
              f'python -m pytest -q -p no:cacheprovider --junitxml=/out/vision.xml {" ".join(extra)} tests')
    mounts = []
    if weights:   # MS-02: real-inference tests with the provisional best.pt (read-only, never copied to the repo)
        weights = Path(weights).resolve()
        mounts = ['-v', f'{weights.parent}:/real_weights:ro', '-e', f'PEF_REAL_WEIGHTS=/real_weights/{weights.name}']
    cmd = ['docker', 'run', '--rm', '--add-host', 'host.docker.internal:host-gateway', *mounts,
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/src:ro", '-v', f'{OUT}:/out',
           '-e', f'PEF_TEST_MONGO_URL={mongo_url}', '-e', 'YOLO_CONFIG_DIR=/tmp/ultralytics',
           '--user', 'root', '--entrypoint', 'sh', VISION_IMAGE, '-c', script]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                          env={**os.environ, 'MSYS_NO_PATHCONV': '1'})
    (OUT / 'vision.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
    return proc.returncode, parse_junit(junit), time.monotonic() - started, proc.stdout + proc.stderr


def summarize(key, code, cases, elapsed, output):
    counts = {k: sum(1 for c in cases if c['outcome'] == k) for k in ('passed', 'failed', 'error', 'skipped')}
    problems = [c for c in cases if c['outcome'] != 'passed']
    for item in problems:
        item['kind'] = 'INFRA' if INFRA_PATTERNS.search(item['message']) else 'CODE'
    # pytest could not even collect / start: no JUnit file or interruption
    collection = None
    if not cases or (code not in (0, 1, 5) and not problems):
        collection = 'INFRA' if INFRA_PATTERNS.search(output or '') else 'CODE'
    infra_skips = [p for p in problems if p['outcome'] == 'skipped' and p['kind'] == 'INFRA']
    status = 'OK'
    if counts['failed'] or counts['error'] or collection:
        status = 'FAIL'
    elif infra_skips:
        status = 'INCOMPLETE (INFRA)'
    elif counts['skipped']:
        status = 'OK (with skips)'
    return {'suite': SUITES[key][0], 'key': key, 'exit_code': code, 'seconds': round(elapsed, 1), **counts,
            'status': status, 'collection_problem': collection,
            'code_problems': [p['name'] for p in problems if p['kind'] == 'CODE' and p['outcome'] != 'skipped'],
            'infra_problems': [p['name'] for p in problems if p['kind'] == 'INFRA' and p['outcome'] != 'skipped'],
            'skips': sorted({re.sub(r'\s+', ' ', p['message'])[:110] for p in problems if p['outcome'] == 'skipped'}),
            'tail': (output or '')[-1500:] if collection else ''}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('suites', nargs='*', help=f"any of: {', '.join(SUITES)} (default: all)")
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--build-vision-image', action='store_true')
    parser.add_argument('--weights', help='real weights file for the VisionWorker real-inference tests (read only)')
    parser.add_argument('-k', dest='keyword', help='pytest -k expression for the selected suites')
    args = parser.parse_args()
    if args.list:
        print('\n'.join(f'{k:14} {v[0]} (apps/{v[1]})' for k, v in SUITES.items()))
        return 0
    selected = args.suites or list(SUITES)
    unknown = [s for s in selected if s not in SUITES]
    if unknown:
        sys.exit(f'Unknown suite(s): {unknown}. Use --list.')
    OUT.mkdir(parents=True, exist_ok=True)
    env = test_env()
    infra = preflight(env)
    print('Isolated test stack: ' + ', '.join(f'{k} {v}' for k, v in infra.items()))
    if 'evidence' in selected:
        print(f'Evidence Garage bucket {EVIDENCE_TEST_BUCKET}: {ensure_evidence_bucket(env)}')
    extra = ['-k', args.keyword] if args.keyword else []
    results = []
    for key in selected:
        print(f'\n=== {SUITES[key][0]}', flush=True)
        if key == 'vision':
            code, cases, elapsed, output = run_vision_suite(env, extra, args.build_vision_image, args.weights)
        else:
            code, cases, elapsed, output = run_python_suite(key, env, extra)
        result = summarize(key, code, cases, elapsed, output)
        results.append(result)
        print(f"  {result['status']}: {result['passed']} passed, {result['failed']} failed, {result['error']} errors, "
              f"{result['skipped']} skipped ({result['seconds']} s)")
        for name in result['code_problems']:
            print(f'    CODE  {name}')
        for name in result['infra_problems']:
            print(f'    INFRA {name}')
        for reason in result['skips']:
            print(f'    skip: {reason}')
        if result['collection_problem']:
            print(f"    {result['collection_problem']} (collection/start): {result['tail'][-400:]}")
    total = {k: sum(r[k] for r in results) for k in ('passed', 'failed', 'error', 'skipped')}
    print('\n=== Summary')
    print(f"{'suite':22}{'passed':>8}{'failed':>8}{'errors':>8}{'skipped':>9}  status")
    for r in results:
        print(f"{r['suite']:22}{r['passed']:>8}{r['failed']:>8}{r['error']:>8}{r['skipped']:>9}  {r['status']}")
    print(f"{'TOTAL':22}{total['passed']:>8}{total['failed']:>8}{total['error']:>8}{total['skipped']:>9}")
    (OUT / 'summary.json').write_text(json.dumps({'stack': infra, 'total': total, 'suites': results}, indent=2),
                                      encoding='utf-8')
    if any(r['status'] == 'FAIL' for r in results):
        return 1
    return 2 if any(r['status'].startswith('INCOMPLETE') for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
