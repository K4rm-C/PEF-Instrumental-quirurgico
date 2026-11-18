"""MS-11 (Phase 2): compares the READ-ONLY inventory of the VM (scripts/vm/inspect_vm.sh output) with what this
repository needs, and prints the table: installed / missing / to update / keep / conflict / needs a service stop.

    python scripts/vm/compare_inventory.py pef_inventory_<host>_<date>.txt [--md out.md]

It only reads the inventory file and the repository. It NEVER guesses: a component whose section is absent or failed
in the inventory is reported as "no inspeccionado" (not as missing, not as installed).
"""
import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPS = ('BackendAuthService', 'BackendWebFlask', 'VisionWorker', 'CatalogService', 'SessionService', 'EvidenceService',
        'AuditService', 'NotificationsService', 'MonitoringService', 'IntegrationService')
PORTS = {5000: 'pef-web', 5001: 'pef-auth', 5002: 'pef-vision', 5003: 'pef-catalog', 5004: 'pef-session',
         5005: 'pef-evidence', 5006: 'pef-audit', 5007: 'pef-notifications', 5008: 'pef-monitoring',
         5009: 'pef-integration', 5432: 'postgres', 6379: 'redis', 27017: 'mongod', 3900: 'garage', 3901: 'garage',
         3903: 'garage'}
INTERNAL = set(PORTS)                       # must listen on 127.0.0.1 only
MIGRATIONS = ['m001', 'm004', 'm005', 'm006', 'm007', 'm008', 'm010', 'm011', 'm012', 'm013', 'm014', 'm015', 'm016']
KEY_PACKAGES = ('flask', 'gunicorn', 'sqlalchemy', 'psycopg', 'redis', 'pyjwt', 'boto3', 'pymongo', 'ultralytics',
                'torch', 'numpy', 'requests', 'flask-sock')
rows = []


def add(component, observed, expected, category, action='', stop=False):
    rows.append((component, observed, expected, category, action, 'sí' if stop else 'no'))


def parse(text):
    """{section title: {subsection description: output}}"""
    sections, current, sub = {}, None, None
    for line in text.splitlines():
        m = re.match(r'^=+ (.+?) =+$', line)
        if m:
            current, sub = m.group(1), None
            sections[current] = {}
            continue
        m = re.match(r'^--- (.+)$', line)
        if m and current:
            sub = m.group(1)
            sections[current][sub] = []
            continue
        if current and sub is not None and not line.startswith('$ '):
            sections[current][sub].append(line)
    return {s: {k: '\n'.join(v).strip() for k, v in subs.items()} for s, subs in sections.items()}


def find(sections, section_prefix, sub_prefix=None):
    for name, subs in sections.items():
        if name.split('. ', 1)[-1].startswith(section_prefix) or name.startswith(section_prefix):
            if sub_prefix is None:
                return subs
            for key, value in subs.items():
                if key.startswith(sub_prefix):
                    return value
    return None


def requirement_pins():
    pins = {}
    for app in APPS:
        for line in (ROOT / 'apps' / app / 'requirements.txt').read_text(encoding='utf-8').splitlines():
            m = re.match(r'^([A-Za-z0-9_.-]+)\s*(==|>=|~=)\s*([^\s;#]+)', line)
            if m:
                pins.setdefault(app, {})[m.group(1).lower()] = (m.group(2), m.group(3))
    return pins


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('inventory', type=Path)
    parser.add_argument('--md', type=Path)
    args = parser.parse_args()
    text = args.inventory.read_text(encoding='utf-8', errors='replace')
    if 'inventory_version=ms11-1' not in text:
        sys.exit('not an inspect_vm.sh (ms11-1) inventory')
    s = parse(text)

    # --- OS and resources --------------------------------------------------------------------------------------
    osr = find(s, 'OS and resources', 'os-release') or ''
    if not osr:
        add('Sistema operativo', 'no inspeccionado', 'CentOS Stream 10', 'no inspeccionado')
    else:
        ok = 'CentOS Stream' in osr and re.search(r'VERSION_ID="?10', osr)
        add('Sistema operativo', re.search(r'PRETTY_NAME="([^"]+)"', osr).group(1) if 'PRETTY_NAME' in osr else '?',
            'CentOS Stream 10', 'ya instalado' if ok else 'conflicto', '' if ok else 'la guía y los paquetes asumen CentOS Stream 10')
    cpu = find(s, 'OS and resources', 'CPU') or ''
    mem = find(s, 'OS and resources', 'memory') or ''
    ncpu = re.search(r'^CPU\(s\):\s+(\d+)', cpu, re.M)
    total = re.search(r'^Mem:\s+(\S+)', mem, re.M)
    if ncpu or total:
        add('CPU / RAM', f"{ncpu.group(1) if ncpu else '?'} CPU · {total.group(1) if total else '?'} RAM",
            '≥ 4 CPU · ≥ 8 GiB (YOLO en CPU + 10 procesos)', 'revisar' if (ncpu and int(ncpu.group(1)) < 4) else 'conservar',
            'medir la inferencia con el modelo definitivo')
    gpu = find(s, 'OS and resources', 'GPU') or ''
    add('GPU', 'ninguna' if (not gpu or gpu.strip() == 'none') else gpu.splitlines()[0], 'opcional (CPU soportado)', 'conservar')
    disks = find(s, 'OS and resources', 'disks') or ''
    for line in disks.splitlines():
        parts = line.split()
        if len(parts) >= 7 and parts[-1] in ('/', '/var', '/opt', '/var/lib'):
            add(f'Disco {parts[-1]}', f'{parts[4]} libres de {parts[2]} ({parts[5]} usado)',
                '≥ 20 GiB libres para venvs, pesos YOLO y evidencias', 'conservar' if not parts[5].rstrip('%').isdigit()
                or int(parts[5].rstrip('%')) < 80 else 'conflicto', 'ampliar el disco antes de desplegar'
                if parts[5].rstrip('%').isdigit() and int(parts[5].rstrip('%')) >= 80 else '')

    # --- ports -------------------------------------------------------------------------------------------------
    listening = find(s, 'Listening ports', 'listening TCP/UDP')
    if listening is None:
        add('Puertos', 'no inspeccionado', '', 'no inspeccionado')
    else:
        seen = {}
        for line in listening.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] not in ('tcp', 'udp') or 'LISTEN' not in line and parts[0] == 'tcp':
                continue
            m = re.search(r'(\S+):(\d+)$', parts[4])
            if not m:
                continue
            port = int(m.group(2))
            proc = re.search(r'users:\(\("([^"]+)"', line)
            seen.setdefault(port, []).append((m.group(1), proc.group(1) if proc else '?'))
        for port, owner in sorted(PORTS.items()):
            if port not in seen:
                add(f'Puerto {port}', 'libre', owner, 'falta' if port in (5432, 6379, 27017, 3900) else 'libre (se usará)')
                continue
            addrs = {a for a, _ in seen[port]}
            procs = {p for _, p in seen[port]}
            public = [a for a in addrs if a not in ('127.0.0.1', '[::1]', '::1')]
            expected_proc = {'postgres': 'postgres', 'redis': 'redis', 'mongod': 'mongod', 'garage': 'garage'}.get(owner)
            accepted = {'redis': ('redis', 'valkey')}.get(expected_proc, (expected_proc,)) if expected_proc else ()
            if expected_proc and not any(a in p for p in procs for a in accepted):
                add(f'Puerto {port}', f'{",".join(procs)} en {",".join(addrs)}', owner, 'conflicto',
                    'otro proceso ocupa el puerto: cambiar el puerto PEF o retirar el proceso (decisión del responsable)', True)
            elif owner.startswith('pef-') and not any('gunicorn' in p or 'python' in p for p in procs):
                add(f'Puerto {port}', f'{",".join(procs)} en {",".join(addrs)}', owner, 'conflicto',
                    'proceso ajeno al PEF en un puerto PEF: cambiar el puerto en unidad, entorno y gateway.env', True)
            elif public:
                add(f'Puerto {port}', f'{",".join(procs)} en {",".join(addrs)}', f'{owner} solo en 127.0.0.1', 'conflicto',
                    'escucha fuera de 127.0.0.1: restringir bind y firewall', True)
            else:
                add(f'Puerto {port}', f'{",".join(procs)} en 127.0.0.1', owner, 'ya instalado')
        for port in (80, 443):
            holders = {p for _, p in seen.get(port, [])}
            add(f'Puerto {port}', ','.join(holders) or 'libre', 'nginx', 'ya instalado' if 'nginx' in holders else
                ('conflicto' if holders else 'falta'), 'otro servidor web ocupa el puerto' if holders and 'nginx' not in holders else '',
                bool(holders and 'nginx' not in holders))

    # --- systemd units -------------------------------------------------------------------------------------------
    unit_files = find(s, 'systemd', 'PEF and data units')
    repo_units = sorted(p.name for p in (ROOT / 'infra/vm/systemd').glob('pef*'))
    if unit_files is None:
        add('Unidades systemd PEF', 'no inspeccionado', f'{len(repo_units)} unidades', 'no inspeccionado')
    else:
        present = set(re.findall(r'^(pef[\w.-]+\.(?:service|target))', unit_files, re.M))
        for unit in repo_units:
            body = find(s, 'systemd', f'unit {unit}')
            if unit not in present:
                add(f'systemd {unit}', 'no existe', 'infra/vm/systemd', 'falta', 'copiar (cp -n) y daemon-reload')
                continue
            repo_text = (ROOT / 'infra/vm/systemd' / unit).read_text(encoding='utf-8')
            same = body is not None and all(line in body for line in repo_text.splitlines() if line.startswith(('ExecStart', 'User=', 'EnvironmentFile')))
            add(f'systemd {unit}', 'existe' + ('' if same else ' (difiere)'), 'infra/vm/systemd',
                'conservar' if same else 'actualizar', '' if same else 'revisar diff y reiniciar la unidad', not same)
        for unit in sorted(present - set(repo_units)):
            add(f'systemd {unit}', 'existe en la VM', 'no está en el repositorio', 'conflicto', 'unidad ajena al repositorio: decidir si se conserva')

    # --- PostgreSQL -----------------------------------------------------------------------------------------------
    pg = find(s, 'PostgreSQL')
    server = (pg or {}).get('server', '')
    if not pg or not server or 'not installed' in server or '[exit' in server:
        add('PostgreSQL', 'no instalado o no inspeccionado', '16, 127.0.0.1', 'falta' if pg else 'no inspeccionado')
    else:
        major = re.search(r'PostgreSQL (\d+)', server)
        add('PostgreSQL', server.splitlines()[0][:60], '16', 'ya instalado' if major and major.group(1) == '16' else 'revisar',
            '' if major and major.group(1) == '16' else 'versión distinta de la probada (16)')
        settings = pg.get('settings', '')
        if 'listen_addresses=' in settings and not re.search(r'listen_addresses=(localhost|127\.0\.0\.1)', settings):
            add('PostgreSQL listen_addresses', re.search(r'listen_addresses=(\S*)', settings).group(1), 'localhost', 'conflicto',
                'restringir a localhost (requiere reinicio de PostgreSQL)', True)
        found_db = False
        for key, value in pg.items():
            m = re.match(r'\[(.+)\] PEF schema markers', key)
            if not m:
                continue
            marks = dict(re.findall(r'^(m\d{3}) (t|f|true|false)$', value, re.M))
            if not marks or marks.get('m001') not in ('t', 'true'):
                continue
            found_db = True
            missing = [k for k in MIGRATIONS if marks.get(k) not in ('t', 'true')]
            add(f'Base {m.group(1)}: migraciones', f'aplicadas hasta {[k for k in MIGRATIONS if k not in missing][-1]}',
                '001, 004–016', 'conservar' if not missing else 'actualizar',
                ('aplicar en orden: ' + ', '.join(x.replace('m', '') for x in missing)) if missing else '', bool(missing))
            seeds = pg.get(f'[{m.group(1)}] seeds and volumes (counts only, no personal data)', '')
            demo = re.search(r'seed003_demo_users\(@instrumed.com\) (\d+)', seeds)
            if demo and int(demo.group(1)) > 0:
                add(f'Base {m.group(1)}: seed 003', f'{demo.group(1)} usuarios demo', 'ninguno en producción', 'conflicto',
                    'la base contiene datos de demostración: decidir si es la base de demo o de producción')
            status = re.search(r'seed002_session_statuses (\d+)', seeds)
            add(f'Base {m.group(1)}: seed 002', f'{status.group(1) if status else "?"} estados de sesión', '> 0',
                'ya instalado' if status and int(status.group(1)) > 0 else 'falta')
        if not found_db:
            add('Base de datos PEF', 'no encontrada', 'base pef con 001 + 002 + 004–016', 'falta', 'instalación nueva (§4.3 de la guía)')

    # --- Redis / Mongo / Garage ---------------------------------------------------------------------------------
    redis = find(s, 'Redis')
    ping = (redis or {}).get('ping', '') + (redis or {}).get('ping (valkey)', '')
    if not redis:
        add('Redis', 'no inspeccionado', '', 'no inspeccionado')
    elif 'NOAUTH' in ping:
        add('Redis', 'responde, exige contraseña', 'contraseña + 127.0.0.1', 'ya instalado')
    elif 'PONG' in ping:
        add('Redis', 'responde SIN contraseña', 'requirepass', 'conflicto', 'activar requirepass (reinicio de Redis)', True)
    else:
        add('Redis', 'no responde / no instalado', 'Redis 127.0.0.1:6379', 'falta')
    mongo = find(s, 'MongoDB')
    if not mongo:
        add('MongoDB', 'no inspeccionado', '', 'no inspeccionado')
    else:
        version = mongo.get('version', '')
        installed = 'db version' in version
        conf = mongo.get('config (masked)', '')
        auth = re.search(r'authorization:\s*enabled', conf)
        add('MongoDB', version.splitlines()[0][:50] if installed else 'no instalado', 'autenticación + 127.0.0.1',
            ('ya instalado' if auth else 'conflicto') if installed else 'falta',
            '' if (auth or not installed) else 'activar security.authorization (reinicio de MongoDB)', bool(installed and not auth))
    garage = find(s, 'Garage')
    if not garage:
        add('Garage', 'no inspeccionado', '', 'no inspeccionado')
    else:
        version = garage.get('version', '')
        if not version or 'garage' not in version.lower():
            add('Garage', 'no instalado', 'Garage 127.0.0.1:3900, buckets pef-evidence y pef-admin', 'falta')
        else:
            buckets = garage.get('buckets', '')
            for bucket in ('pef-evidence', 'pef-admin'):
                add(f'Garage bucket {bucket}', 'existe' if bucket in buckets else 'no existe', bucket,
                    'ya instalado' if bucket in buckets else 'falta', '' if bucket in buckets else 'crear bucket y permisos de la clave PEF')

    # --- Python, venvs, env -----------------------------------------------------------------------------------------
    pyth = find(s, 'Python services')
    pins = requirement_pins()
    if not pyth:
        add('Python / venvs', 'no inspeccionado', '', 'no inspeccionado')
    else:
        for app in APPS:
            out = next((v for k, v in pyth.items() if k.startswith('venv ') and k.rstrip('/').endswith('/' + app)), None)
            if out is None:
                add(f'venv {app}', 'no existe', '/opt/pef/venvs/' + app, 'falta', 'crear venv e instalar requirements')
                continue
            installed = dict((n.lower(), v) for n, v in re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)$', out, re.M))
            diffs = [f'{n} {installed.get(n, "falta")}≠{op}{v}' for n, (op, v) in pins.get(app, {}).items()
                     if n in KEY_PACKAGES and op == '==' and installed.get(n) != v]
            add(f'venv {app}', re.search(r'Python (\S+)', out).group(1) if 'Python' in out else '?',
                'Python 3.12 + requirements.txt', 'actualizar' if diffs else 'conservar', '; '.join(diffs[:4]), bool(diffs))
        commit = pyth.get('deployed commit', '')
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        m = re.search(r'^([0-9a-f]{40})$', commit, re.M)
        add('Código desplegado', m.group(1)[:7] if m else 'no existe /opt/pef/current', head[:7],
            'conservar' if m and m.group(1) == head else ('actualizar' if m else 'falta'), '', bool(m and m.group(1) != head))
        for example in sorted((ROOT / 'infra/vm/env').glob('*.env.example')):
            name = example.name.replace('.example', '')
            needed = {l.split('=', 1)[0] for l in example.read_text(encoding='utf-8').splitlines() if '=' in l and not l.startswith('#')}
            block = re.search(rf'--- env keys /etc/pef/{re.escape(name)} \(values hidden\)\n(.*?)(?:\n---|\n=+ |\Z)', text, re.S)
            if not block:
                add(f'/etc/pef/{name}', 'no existe', 'infra/vm/env/' + example.name, 'falta', 'copiar con cp -n y completar desde Secret Manager')
                continue
            have = dict(re.findall(r'^([A-Z0-9_]+)=<(set|empty|placeholder)>$', block.group(1), re.M))
            missing = sorted(needed - set(have))
            holders = sorted(k for k, v in have.items() if v == 'placeholder')
            category = 'actualizar' if missing or holders else 'conservar'
            add(f'/etc/pef/{name}', f'{len(have)} claves', f'{len(needed)} claves', category,
                ('faltan: ' + ', '.join(missing)) if missing else ('marcadores: ' + ', '.join(holders)) if holders else '',
                bool(missing))

    # --- nginx, TLS, SELinux, firewall ---------------------------------------------------------------------------------
    web = find(s, 'Web front')
    conf = (web or {}).get('nginx full config (masked)', '')
    if not web:
        add('nginx', 'no inspeccionado', '', 'no inspeccionado')
    elif not conf or 'nginx: not installed' in (web.get('nginx version', '') + conf):
        add('nginx', 'no instalado', 'nginx + plantillas del Gateway (TLS)', 'falta')
    else:
        names = sorted(set(re.findall(r'server_name\s+([^;]+);', conf)))
        add('nginx', 'instalado; server_name: ' + ', '.join(names)[:80], 'Gateway PEF (pef-servers.conf)',
            'actualizar' if 'pef_gateway' not in conf else 'conservar',
            'añadir conf.d/pef* sin tocar los demás server' if 'pef_gateway' not in conf else '')
        add('WSS /ws', 'presente' if 'location = /ws' in conf else 'ausente', 'location = /ws con Upgrade',
            'ya instalado' if 'location = /ws' in conf else 'falta')
        if (web.get('httpd version') or '').startswith('Server version'):
            add('Apache httpd', 'instalado', 'no lo usa el PEF', 'conflicto', 'verificar que no ocupe 80/443')
    tls = find(s, 'TLS certificates') or {}
    for key, value in tls.items():
        if key.startswith('certificate '):
            end = re.search(r'notAfter=(.+)', value)
            when = datetime.strptime(end.group(1).strip(), '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc) if end else None
            days = (when - datetime.now(timezone.utc)).days if when else None
            san = re.search(r'DNS:[^\n]+', value)
            add(f'Certificado {key.split(" ", 1)[1]}', f'vence en {days} días; {san.group(0)[:60] if san else "sin SAN"}',
                'vigente > 30 días y SAN de portal/API', 'conservar' if days is not None and days > 30 else 'actualizar')
    se = find(s, 'SELinux') or {}
    if se and 'failed to run' in se.get('mode', ''):
        add('SELinux', 'no inspeccionado (sestatus no disponible)', 'enforcing', 'no inspeccionado')
    elif se:
        mode = re.search(r'Current mode:\s+(\w+)', se.get('mode', ''))
        add('SELinux', mode.group(1) if mode else 'desconocido', 'enforcing', 'conservar' if mode and mode.group(1) == 'enforcing' else 'conflicto',
            '' if mode and mode.group(1) == 'enforcing' else 'no desactivar; ajustar contextos')
        boolean = se.get('booleans', '')
        add('SELinux httpd_can_network_connect', 'on' if 'httpd_can_network_connect --> on' in boolean else 'off', 'on',
            'ya instalado' if 'httpd_can_network_connect --> on' in boolean else 'falta', 'setsebool -P (sin reinicio)')
    fw = find(s, 'Local firewall') or {}
    if fw and ('not found' in fw.get('firewalld', '') or 'failed to run' in fw.get('firewalld', '') or 'not running' in fw.get('firewalld', '') or not fw.get('firewalld')):
        add('firewalld', 'no activo o no inspeccionado', 'solo http/https (+ ssh por IAP)', 'no inspeccionado',
            'si firewalld no está activo, la única barrera es el firewall de VPC')
    elif fw:
        rules = fw.get('firewalld', '')
        services = re.search(r'services:\s*(.*)', rules)
        ports = re.search(r'ports:\s*(.*)', rules)
        extra = [p for p in (ports.group(1).split() if ports else []) if p.split('/')[0] not in ('80', '443')]
        add('firewalld', f"servicios: {services.group(1) if services else '?'}; puertos: {ports.group(1) if ports else '-'}",
            'solo http/https (+ ssh por IAP)', 'conflicto' if extra else 'conservar',
            ('puertos abiertos de más: ' + ' '.join(extra)) if extra else '')
    print_table(args.md)


def print_table(md_path):
    header = '| Componente | Observado en la VM | Requerido por el repositorio | Categoría | Acción | ¿Detener servicio? |'
    lines = [header, '| :--- | :--- | :--- | :--- | :--- | :--- |']
    lines += ['| ' + ' | '.join(str(c).replace('|', '/') for c in row) + ' |' for row in rows]
    summary = {}
    for row in rows:
        summary[row[3]] = summary.get(row[3], 0) + 1
    out = '\n'.join(lines) + '\n\nResumen: ' + ', '.join(f'{k}: {v}' for k, v in sorted(summary.items())) + '\n'
    print(out)
    if md_path:
        md_path.write_text(out, encoding='utf-8')


if __name__ == '__main__':
    main()
