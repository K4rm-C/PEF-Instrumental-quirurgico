"""MS-09: production configuration preflight for the VM (reads /etc/pef/*.env; never prints a secret value).

    sudo python3 scripts/preflight_production.py --env-dir /etc/pef [--strict]

Checks (each reported as OK / WARN / FAIL with the variable NAME only):
- JWT_SECRET_KEY present in common.env, at least 64 characters, not a known placeholder, the same in every file that
  overrides it; JWT_ALGORITHM one of HS256/HS384/HS512;
- SESSION_SECRET_KEY (portal), VISION_WS_SECRET (portal = VisionWorker), SYNC_CURSOR_SECRET strong and distinct from
  the JWT secret;
- cookies Secure / SameSite, AUTH_TRUSTED_PROXIES set behind nginx, demo mode off;
- every *_URL between services uses 127.0.0.1 / localhost (internal traffic never leaves the VM); public URLs https;
- INTEGRATION_ALLOW_PLAIN_HTTP=false; INTEGRATION_ALLOWED_HOSTS only https-capable hosts (reported, not judged);
- S3 public endpoint https; Garage internal endpoint on 127.0.0.1;
- file permissions: env files not world-readable (0640 or stricter).
Exit code 1 when any FAIL (or WARN with --strict).
"""
import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

PLACEHOLDERS = ('change', 'secret', 'example', '<', 'changeme', 'password', 'test', 'dev')
results = []


def report(level, name, detail):
    results.append(level)
    print(f'{level:4}  {name}: {detail}')


def load(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def strong(value: str, minimum: int) -> bool:
    return len(value) >= minimum and not any(p in value.lower() for p in PLACEHOLDERS)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env-dir', type=Path, default=Path('/etc/pef'))
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    files = sorted(args.env_dir.glob('*.env'))
    if not files:
        sys.exit(f'no *.env files in {args.env_dir}')
    envs = {f.stem: load(f) for f in files}
    common = envs.get('common', {})

    for f in files:
        mode = stat.S_IMODE(os.stat(f).st_mode)
        if os.name != 'nt':
            report('OK' if not mode & 0o007 else 'FAIL', f'{f.name} permissions', oct(mode))

    jwt = common.get('JWT_SECRET_KEY', '')
    report('OK' if strong(jwt, 64) else 'FAIL', 'JWT_SECRET_KEY', f'length {len(jwt)}, fingerprint {fingerprint(jwt)}'
           if jwt else 'missing')
    overrides = {name: env['JWT_SECRET_KEY'] for name, env in envs.items() if 'JWT_SECRET_KEY' in env and name != 'common'}
    for name, value in overrides.items():
        report('OK' if value == jwt else 'FAIL', f'{name}.env JWT_SECRET_KEY', 'same as common' if value == jwt
               else 'DIFFERENT from common: tokens would be refused')
    algorithm = common.get('JWT_ALGORITHM', 'HS256')
    report('OK' if algorithm in ('HS256', 'HS384', 'HS512') else 'FAIL', 'JWT_ALGORITHM', algorithm)

    web, vision, integration, auth = (envs.get(k, {}) for k in ('web', 'vision', 'integration', 'auth'))
    for name, value, owner in (('SESSION_SECRET_KEY', web.get('SESSION_SECRET_KEY', ''), 'web'),
                               ('VISION_WS_SECRET', web.get('VISION_WS_SECRET', ''), 'web'),
                               ('SYNC_CURSOR_SECRET', integration.get('SYNC_CURSOR_SECRET', ''), 'integration')):
        if not value:
            report('WARN' if name == 'SYNC_CURSOR_SECRET' else 'FAIL', f'{owner}.env {name}',
                   'missing (derived from the JWT secret)' if name == 'SYNC_CURSOR_SECRET' else 'missing')
        else:
            report('OK' if strong(value, 32) and value != jwt else 'FAIL', f'{owner}.env {name}',
                   f'length {len(value)}' + (' (same as JWT secret!)' if value == jwt else ''))
    if web.get('VISION_WS_SECRET') and vision.get('VISION_WS_SECRET'):
        same = web['VISION_WS_SECRET'] == vision['VISION_WS_SECRET']
        report('OK' if same else 'FAIL', 'VISION_WS_SECRET portal = VisionWorker', 'same' if same else 'different')

    for name, env, key, expected in (('auth', auth, 'AUTH_COOKIE_SECURE', 'true'), ('web', web, 'PORTAL_COOKIE_SECURE', 'true'),
                                     ('web', web, 'FRONTEND_DEMO_MODE', 'false'),
                                     ('integration', integration, 'INTEGRATION_ALLOW_PLAIN_HTTP', 'false')):
        value = env.get(key, '').lower()
        report('OK' if value == expected else 'FAIL', f'{name}.env {key}', value or 'missing')
    proxies = auth.get('AUTH_TRUSTED_PROXIES', '0')
    report('OK' if proxies not in ('', '0') else 'WARN', 'auth.env AUTH_TRUSTED_PROXIES',
           f'{proxies} (behind nginx it must be the number of proxies, usually 1)')

    for name, env in envs.items():
        for key, value in env.items():
            if not key.endswith('_URL') or not value or value.startswith('<'):
                continue
            parts = urlsplit(value)
            host = (parts.hostname or '').lower()
            if key in ('AUTH_BROWSER_URL', 'S3_PUBLIC_ENDPOINT', 'NOTIFICATIONS_PORTAL_URL') or key.endswith('_WS_URL'):
                ok = parts.scheme in ('https', 'wss')
                report('OK' if ok else 'FAIL', f'{name}.env {key}', f'{parts.scheme}://{host} (public: must be TLS)')
            elif key in ('DATABASE_URL', 'REDIS_URL', 'MONGO_URL') or key.endswith('_SERVICE_URL') or key.startswith(
                    ('INTEGRATION_', 'MONITORING_TARGET_', 'VISION_WORKER_URL', 'AUTH_SERVICE_URL')):
                ok = host in ('127.0.0.1', 'localhost', '::1')
                report('OK' if ok else 'WARN', f'{name}.env {key}', f'{host} (internal: expected 127.0.0.1)')
    s3_internal = web.get('S3_INTERNAL_ENDPOINT') or envs.get('evidence', {}).get('S3_INTERNAL_ENDPOINT', '')
    if s3_internal:
        host = (urlsplit(s3_internal).hostname or '').lower()
        report('OK' if host in ('127.0.0.1', 'localhost') else 'WARN', 'S3_INTERNAL_ENDPOINT', host)
    # MS-10: with ADMIN_STORAGE_BACKEND=s3 the portal stores Administration files in Garage (photos, notices, weights)
    if web.get('ADMIN_STORAGE_BACKEND', 'local').lower() == 's3':
        for key in ('S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY', 'ADMIN_S3_BUCKET'):
            value = web.get(key, '')
            report('OK' if value and not value.startswith('<') else 'FAIL', f'web.env {key}',
                   'set' if value and not value.startswith('<') else 'missing (Administration uploads would fail)')
    elif web:
        report('WARN', 'web.env ADMIN_STORAGE_BACKEND', 'local: Administration files stay on this VM disk (back them up)')
    allowed = integration.get('INTEGRATION_ALLOWED_HOSTS', '')
    report('OK', 'integration.env INTEGRATION_ALLOWED_HOSTS', allowed or 'empty (no FHIR delivery can leave the VM)')

    fails, warns = results.count('FAIL'), results.count('WARN')
    print(f'\n{len(results)} checks: {fails} FAIL, {warns} WARN')
    sys.exit(1 if fails or (args.strict and warns) else 0)


if __name__ == '__main__':
    main()
