"""MS-09: render the API Gateway / portal nginx configuration for the VM (no Docker) from infra/gateway.

The same repository templates the gateway container uses (MS-04 allow-list, limits, TLS, WebSocket) are rendered
with the VM values: upstreams on 127.0.0.1, listeners 80/443, the operator's certificate paths, file logs. The
Garage S3 TLS front (infra/vm/nginx/garage-s3.conf.template) is rendered too when --garage-host is given.

    python scripts/render_gateway_vm.py --env infra/vm/nginx/gateway.env.example --out <dir> \
        --cert /etc/pki/tls/certs/pef-fullchain.pem --key /etc/pki/tls/private/pef-privkey.pem [--garage-host s3.example.org]

Output layout = what goes under /etc/nginx/conf.d/ (00-pef-http.conf, pef-servers.conf, pef/*.conf). It refuses to
write a file that still contains an unresolved ${...}. Validate on the VM with `nginx -t` before reloading.
"""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GW = ROOT / 'infra' / 'gateway'
VAR = re.compile(r'\$\{(PEF_GW_[A-Z0-9_]+)\}')


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def render(text: str, values: dict[str, str], name: str) -> str:
    missing = sorted({m for m in VAR.findall(text) if m not in values})
    if missing:
        raise SystemExit(f'{name}: missing values for {", ".join(missing)}')
    return VAR.sub(lambda m: values[m.group(1)], text)


def vm_adjust(text: str, cert: str, key: str, hosts: str = '') -> str:
    text = re.sub(r'\blisten 8080;', 'listen 80;', text)
    if hosts:
        # MS-11: on the VM the stock nginx.conf already has a default "server_name _" on port 80 (CentOS Stream 10);
        # a second "_" is IGNORED by nginx ("conflicting server name"), so the HTTPS redirect would never apply.
        # The PEF redirect block names its own hosts instead and leaves the existing default server untouched.
        text = re.sub(r'(listen 80;\s*\n\s*)server_name _;', lambda m: f'{m.group(1)}server_name {hosts};', text)
    text = re.sub(r'\blisten 8443 ssl;', 'listen 443 ssl;', text)
    text = text.replace('/etc/nginx/certs/fullchain.pem', cert).replace('/etc/nginx/certs/privkey.pem', key)
    return text.replace('access_log /dev/stdout', 'access_log /var/log/nginx/pef_gateway.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--cert', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--garage-host', default='')
    args = parser.parse_args()
    values = load_env(args.env)
    hosts = ' '.join(h for h in (values.get('PEF_GW_PORTAL_HOST'), values.get('PEF_GW_API_HOST'), args.garage_host) if h)
    (args.out / 'pef').mkdir(parents=True, exist_ok=True)
    files = {
        args.out / '00-pef-http.conf': GW / 'templates' / '00-pef-http.conf.template',
        args.out / 'pef-servers.conf': GW / 'servers' / 'tls.conf.template',
        **{args.out / 'pef' / p.name.replace('.template', ''): p for p in (GW / 'templates' / 'pef').glob('*.template')},
    }
    for target, source in files.items():
        text = vm_adjust(render(source.read_text(encoding='utf-8'), values, source.name), args.cert, args.key, hosts)
        assert not VAR.search(text), target   # (a literal "${PEF_GW_*}" in a comment is fine)
        target.write_text(text, encoding='utf-8', newline='\n')
    if args.garage_host:
        source = ROOT / 'infra' / 'vm' / 'nginx' / 'garage-s3.conf.template'
        text = render(source.read_text(encoding='utf-8'), {**values, 'PEF_GW_GARAGE_HOST': args.garage_host}, source.name)
        (args.out / 'pef-garage-s3.conf').write_text(vm_adjust(text, args.cert, args.key), encoding='utf-8', newline='\n')
    print('rendered', len(files) + bool(args.garage_host), 'files into', args.out)


if __name__ == '__main__':
    main()
