"""MS-10: static verification of the PEF architecture against the code and the configuration (no service started).

    python scripts/verify_architecture.py            # report on stdout, exit 1 on any FAIL

Checks (each one cites the file it read):
- roles: Administration and Operator business code runs in BackendWebFlask (in-process admin_local / rf_session);
  the Supervisor screens use the microservice clients (session_remote, trays_remote, monitoring_remote...);
- AuthService is the only issuer of tokens; every service validates the JWT itself;
- VisionWorker loads the weights pinned to the session (sha256 checked); EvidenceService stores evidence;
  IntegrationService owns sync / FHIR;
- ports agree between docker-compose.yml, the systemd units (infra/vm) and the nginx upstreams (VM env);
- migrations 001..016 exist and are mounted in BOTH compose files, in order, plus the seeds;
- each service's migrations shipped inside its folder are byte-identical to data/migrations;
- the nine runner suites map to the nine services.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / 'apps'
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'}  {name}{'  -- ' + str(detail) if detail else ''}")


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def main():
    # --- roles ------------------------------------------------------------------------------------------
    admin_api = read('apps/BackendWebFlask/services/admin_local/api.py')
    check('Administration business runs in-process (admin_local, local router)', 'LocalAdminRouter' in admin_api,
          'apps/BackendWebFlask/services/admin_local/api.py')
    routes = read('apps/BackendWebFlask/controllers/routes.py')
    check('Operator counting flow runs in the portal (rf_session: initial/final/validation/contingency/escalation)',
          all(f in read('apps/BackendWebFlask/services/rf_session.py') for f in
              ('def confirm_final_count', 'def submit_manual_close', 'def escalate_final_mismatch')),
          'apps/BackendWebFlask/services/rf_session.py')
    sup = read('apps/BackendWebFlask/controllers/supervisor_sessions.py')
    check('Supervisor sessions go to SessionService when configured (session_remote)', 'session_remote.close_session' in sup
          and 'def remote()' in sup, 'controllers/supervisor_sessions.py')
    for module, url in (('trays_remote', 'CATALOG_SERVICE_URL'), ('monitoring_remote', 'MONITORING_SERVICE_URL'),
                        ('notifications_remote', 'NOTIFICATIONS_SERVICE_URL'), ('session_remote', 'SESSION_SERVICE_URL')):
        check(f'portal client {module} uses {url}', url in read(f'apps/BackendWebFlask/services/{module}.py'))
    check('portal authenticates through AuthService (/verify, /login)', "_auth_request" in routes and "'/verify'" in routes)

    # --- tokens --------------------------------------------------------------------------------------------
    issuers = [p for p in APPS.rglob('*.py') if 'jwt.encode(' in p.read_text(encoding='utf-8', errors='ignore')
               and '/tests/' not in p.as_posix() and 'Legacy' not in p.as_posix()]
    check('only AuthService signs tokens (jwt.encode outside tests)',
          all('BackendAuthService' in p.as_posix() for p in issuers), [p.relative_to(ROOT).as_posix() for p in issuers])
    for service in ('CatalogService', 'SessionService', 'EvidenceService', 'AuditService', 'NotificationsService',
                    'MonitoringService', 'IntegrationService'):
        text = (APPS / service / 'auth.py').read_text(encoding='utf-8') if (APPS / service / 'auth.py').exists() else ''
        check(f'{service} validates the JWT itself (algorithm + Redis revocation)',
              'jwt.decode' in text and 'auth:jwt:' in text, f'apps/{service}/auth.py')

    # --- vision / evidence / integration -----------------------------------------------------------------
    vision = read('apps/VisionWorker/model_store.py') if (APPS / 'VisionWorker/model_store.py').exists() else ''
    check('VisionWorker loads pinned weights verified by SHA-256', 'sha256' in vision.lower(), 'apps/VisionWorker/model_store.py')
    check('EvidenceService stores objects in S3/Garage', 'boto3' in read('apps/EvidenceService/requirements.txt'))
    check('IntegrationService owns sync + FHIR', all(b in read('apps/IntegrationService/main.py')
                                                    for b in ('sync_bp', 'fhir_bp', 'integration_bp')))

    # --- ports -------------------------------------------------------------------------------------------
    compose = yaml.safe_load(read('docker-compose.yml'))['services']
    expected = {'web': 5000, 'auth': 5001, 'vision': 5002, 'catalog': 5003, 'session': 5004, 'evidence': 5005,
                'audit': 5006, 'notifications': 5007, 'monitoring': 5008, 'integration': 5009}
    for name, port in expected.items():
        unit = read(f'infra/vm/systemd/pef-{name}.service')
        bound = re.search(r'--bind 127\.0\.0\.1:(\d+)', unit)
        check(f'{name}: systemd binds 127.0.0.1:{port}', bound and int(bound.group(1)) == port, f'pef-{name}.service')
        service = compose.get(name, {})
        ports = ' '.join(str(p) for p in service.get('ports', []))
        env = service.get('environment') or {}
        in_compose = str(port) in ports or any(str(port) == str(v) for v in env.values()) if service else False
        check(f'{name}: compose uses port {port}', in_compose, ports or 'no published port (internal)')
    gw_env = read('infra/vm/nginx/gateway.env.example')
    for key, port in (('AUTH', 5001), ('CATALOG', 5003), ('SESSION', 5004), ('EVIDENCE', 5005), ('NOTIFICATIONS', 5007),
                      ('MONITORING', 5008), ('INTEGRATION', 5009), ('VISION', 5002), ('PORTAL', 5000)):
        check(f'nginx VM upstream {key} = 127.0.0.1:{port}', f'PEF_GW_{key}_UPSTREAM=127.0.0.1:{port}' in gw_env)

    # --- migrations -----------------------------------------------------------------------------------------
    numbers = sorted(int(p.name[:3]) for p in (ROOT / 'data/migrations').glob('0*.sql'))
    check('migrations 001 and 004..019 present (002/003 are seeds)', numbers == [1] + list(range(4, 20)), numbers)
    for compose_file in ('docker-compose.yml', 'docker-compose.test.yml'):
        mounts = re.findall(r'\./data/(migrations|seeds)/(\d{3})_[^:]+:/docker-entrypoint-initdb\.d/(\d+)_', read(compose_file))
        order = [int(m[1]) for m in mounts]
        check(f'{compose_file}: 001..019 mounted in order (seeds 002/003 included)', order == list(range(1, 20)), order)
    for service in ('MonitoringService', 'NotificationsService', 'IntegrationService'):
        for shipped in (APPS / service / 'migrations').glob('*.sql'):
            same = shipped.read_bytes().replace(b'\r\n', b'\n') == (ROOT / 'data/migrations' / shipped.name).read_bytes().replace(b'\r\n', b'\n')
            check(f'{service}/migrations/{shipped.name} identical to data/migrations', same)

    # --- runner ---------------------------------------------------------------------------------------------
    runner = read('scripts/run_ms_tests.py')
    suites = re.findall(r"'(\w+)': \('(\w+)', '(\w+)'", runner)
    check('runner has the nine service suites', len(suites) == 9, [s[0] for s in suites])

    print(f'\n{sum(results)}/{len(results)} checks OK')
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
