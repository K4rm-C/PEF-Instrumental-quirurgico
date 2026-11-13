"""Ejecuta las suites contra el entorno AISLADO de docker-compose.test.yml (ADMIN-01.0).

Uso (desde la raíz, con el intérprete que tenga instalados los requirements de Web y Auth + pytest):

    python scripts/run_test_env.py prepare  # once: writes .e2e/garage.test.toml (generated secrets)
    docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
    python scripts/run_test_env.py web      # apps/BackendWebFlask/tests
    python scripts/run_test_env.py admin    # solo pruebas administrativas de BackendWebFlask
    python scripts/run_test_env.py auth     # apps/BackendAuthService/tests
    python scripts/run_test_env.py smoke    # scripts/smoke_admin_monolith_sqlite.py (SQLite temporal)
    python scripts/run_test_env.py all

Lee .env.test y apunta DATABASE_URL / AUTH_TEST_* / PEF_TEST_MONGO_URL a los puertos de prueba
(127.0.0.1:55432/56379/47017). Nunca usa la BD de desarrollo ni el .env principal: si .env.test
no existe, se detiene. Argumentos extra se pasan a pytest (p. ej. `-k roles -x`).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_TESTS = sorted(
    {f'tests/{path.name}' for path in (ROOT / 'apps' / 'BackendWebFlask' / 'tests').glob('test_admin_*.py')}
    | {'tests/test_identity_remote.py', 'tests/test_catalog_remote.py', 'tests/test_audit_remote.py',
       'tests/test_multitenant.py'})


def load_test_env():
    path = ROOT / '.env.test'
    if not path.exists():
        sys.exit('Falta .env.test: copie .env.test.example y defina contraseñas (ver PLAN_TRABAJO.md, ADMIN-01.0).')
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    pg = (f"postgresql+psycopg://{values['TEST_POSTGRES_USER']}:{values['TEST_POSTGRES_PASSWORD']}"
          f"@127.0.0.1:{values.get('TEST_POSTGRES_PORT', '55432')}/{values['TEST_POSTGRES_DB']}")
    redis = f"redis://:{values['TEST_REDIS_PASSWORD']}@127.0.0.1:{values.get('TEST_REDIS_PORT', '56379')}/0"
    mongo = (f"mongodb://{values['TEST_MONGO_USER']}:{values['TEST_MONGO_PASSWORD']}"
             f"@127.0.0.1:{values.get('TEST_MONGO_PORT', '47017')}/?authSource=admin")
    env = dict(os.environ)
    env.update({
        'DATABASE_URL': pg, 'AUTH_TEST_DATABASE_URL': pg, 'AUTH_TEST_REDIS_URL': redis, 'REDIS_URL': redis,
        'PEF_TEST_MONGO_URL': mongo, 'PYTHONDONTWRITEBYTECODE': '1',
        # Garage of docker-compose.test.yml for the S3 backend tests of admin_local/storage.py.
        'PEF_TEST_S3_ENDPOINT': f"http://127.0.0.1:{values.get('TEST_S3_PORT', '59000')}",
        'PEF_TEST_S3_ACCESS_KEY': values.get('TEST_S3_ACCESS_KEY', ''),
        'PEF_TEST_S3_SECRET_KEY': values.get('TEST_S3_SECRET_KEY', ''),
        'PEF_TEST_S3_BUCKET': values.get('TEST_S3_BUCKET', 'pef-admin-test'),
    })
    return env


def prepare():
    """Write .e2e/garage.test.toml (Garage single-node config with generated secrets) if missing."""
    import secrets
    target = ROOT / '.e2e' / 'garage.test.toml'
    if target.exists():
        print(f'{target} already exists')
        return 0
    target.parent.mkdir(exist_ok=True)
    template = (ROOT / 'infra' / 'garage.toml.example').read_text(encoding='utf-8')
    config = (template.replace('REEMPLAZAR_CON_64_CARACTERES_HEXADECIMALES', secrets.token_hex(32))
              .replace('REEMPLAZAR_TOKEN_ADMIN', secrets.token_hex(24))
              .replace('REEMPLAZAR_TOKEN_METRICAS', secrets.token_hex(24)))
    target.write_text(config, encoding='utf-8')
    print(f'wrote {target}')
    return 0


def run(label, args, cwd, env):
    print(f'\n===== {label}: {" ".join(args)} (cwd={cwd.relative_to(ROOT) or "."})', flush=True)
    return subprocess.call([sys.executable, *args], cwd=cwd, env=env)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else 'all'
    extra = sys.argv[2:]
    if target == 'prepare':
        sys.exit(prepare())
    env = load_test_env()
    pytest = ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-rfEs', *extra]
    web, auth = ROOT / 'apps' / 'BackendWebFlask', ROOT / 'apps' / 'BackendAuthService'
    jobs = {
        'web': ('BackendWebFlask', [*pytest, 'tests'], web),
        'admin': ('Admin (BackendWebFlask)', [*pytest, *ADMIN_TESTS], web),
        'auth': ('BackendAuthService', [*pytest, 'tests'], auth),
        'smoke': ('Smoke Admin SQLite', ['scripts/smoke_admin_monolith_sqlite.py'], ROOT),
    }
    selected = ['web', 'auth', 'smoke'] if target == 'all' else [target]
    if any(name not in jobs for name in selected):
        sys.exit(f'Objetivo desconocido: {target} (use web, admin, auth, smoke o all)')
    results = {name: run(*jobs[name], env) for name in selected}
    print('\n===== Resumen (código de salida): ' + ', '.join(f'{k}={v}' for k, v in results.items()))
    sys.exit(max(results.values()))


if __name__ == '__main__':
    main()
