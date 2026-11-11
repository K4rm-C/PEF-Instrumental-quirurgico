"""MS-11: post-deployment smoke test of PEF, ONLY through the public front (nginx: API Gateway + portal + /ws).

It never connects to a database, never uses SSH and never changes configuration. It DOES create test data
(sessions, a tray, captures) and therefore refuses to run without --confirm-test-institution: run it only with the
users of a TEST institution, the demo/test kit and the MS-03.1 test videos. NO clinical data: no patient is set.

    export PEF_SMOKE_ADMIN_EMAIL=... PEF_SMOKE_ADMIN_PASSWORD=...           # test institution users
    export PEF_SMOKE_SUPERVISOR_EMAIL=... PEF_SMOKE_SUPERVISOR_PASSWORD=...
    export PEF_SMOKE_OPERATOR_EMAIL=... PEF_SMOKE_OPERATOR_PASSWORD=...
    export PEF_SMOKE_CLIENT_ID=... PEF_SMOKE_CLIENT_SECRET=...             # optional: test machine client (sync/FHIR)
    python scripts/vm/smoke_post_deploy.py --base https://portal.example.org [--api https://api.example.org] \
        --fixture smoke_fixture.json --confirm-test-institution [--local-ports] [--out smoke_result.json]

Fixture (IDs of the TEST institution; docs/MS11_PREPARACION_DESPLIEGUE.md §6 has a read-only query that prints it):
    {"operator_user_id", "procedure_type_id", "room_id", "station_id", "camera_id" (optional), "kit_id", "family_id",
     "physician_id", "privacy_notice_version_id", "institution_id",
     "videos": {"one_removed": "<path>", "full": "<path>"}}   # .e2e/videos/ms031_one_removed.avi / ms031_masked.avi

Credentials are read from the environment and NEVER printed; tokens are never printed. Exit code 0 only when every
executed check passed. A check that could not run (missing optional input) is reported as SKIP, never as PASS.
"""
import argparse
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import portal_csrf  # noqa: E402

LOCAL_PORTS = {'web': (5000, '/health/live'), 'auth': (5001, '/health/live'), 'vision': (5002, '/health'),
               'catalog': (5003, '/health/live'), 'session': (5004, '/health/live'), 'evidence': (5005, '/health/live'),
               'audit': (5006, '/health/live'), 'notifications': (5007, '/health/live'),
               'monitoring': (5008, '/health/live'), 'integration': (5009, '/health/live')}
# The nine runner services (+ the portal) as MonitoringService names them on the Supervisor's health screen.
MONITORED = ('web', 'auth', 'catalog', 'session', 'evidence', 'audit', 'notifications', 'vision', 'postgres',
             'redis', 'mongo', 'garage')
results = []
OUT = {"path": None}


def report(item, name, status, detail=''):
    results.append({'item': item, 'check': name, 'status': status, 'detail': str(detail)[:300]})
    print(f'{status:<5} [{item:>2}] {name}{"  -- " + str(detail)[:300] if detail and status != "PASS" else ""}', flush=True)


def check(item, name, ok, detail=''):
    report(item, name, 'PASS' if ok else 'FAIL', detail)
    return ok


def creds(role):
    email, password = os.environ.get(f'PEF_SMOKE_{role}_EMAIL'), os.environ.get(f'PEF_SMOKE_{role}_PASSWORD')
    if not email or not password:
        sys.exit(f'PEF_SMOKE_{role}_EMAIL / PEF_SMOKE_{role}_PASSWORD are not set')
    return email, password


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--base', required=True, help='public URL of the portal (nginx)')
    parser.add_argument('--api', help='public URL of the API Gateway if it is a different host (default: --base)')
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--confirm-test-institution', action='store_true',
                        help='required: confirms that the users and IDs belong to a TEST institution')
    parser.add_argument('--local-ports', action='store_true', help='also check 127.0.0.1:5000-5009 (only ON the VM)')
    parser.add_argument('--not-monitored', default='', help='comma list of components this environment cannot monitor '
                        '(reported as SKIP with the reason, never as up)')
    parser.add_argument('--resolve', action='append', default=[], metavar='HOST:IP',
                        help='like curl --resolve: send HOST to IP without DNS (test front / before DNS exists)')
    parser.add_argument('--http-url', help='plain-HTTP URL of the portal for the redirect check (default: http://<host>)')
    parser.add_argument('--insecure', action='store_true', help='accept a self-signed certificate (test front only)')
    parser.add_argument('--timeout-video', type=int, default=900)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    OUT["path"] = args.out
    if not args.confirm_test_institution:
        sys.exit('refused: pass --confirm-test-institution (this test creates sessions and captures)')
    if args.resolve:
        import socket
        hosts_map = dict(item.split(':', 1) for item in args.resolve)
        native = socket.getaddrinfo

        debug = os.environ.get('PEF_SMOKE_DEBUG_RESOLVE') == '1'

        def resolve(host, port, family=0, type=0, proto=0, flags=0, _hosts=hosts_map):
            key = host.decode() if isinstance(host, bytes) else host
            if debug:
                print(f'DEBUG resolve {key!r}:{port} pinned={key in _hosts}', file=sys.stderr, flush=True)
            if key in _hosts:   # pinned IPv4 literal: answer directly, no system resolver involved
                return [(socket.AF_INET, type or socket.SOCK_STREAM, proto or socket.IPPROTO_TCP, '', (_hosts[key], int(port)))]
            return native(host, port, family, type, proto, flags)
        socket.getaddrinfo = resolve
        OUT['resolve'] = resolve
    base, api = args.base.rstrip('/'), (args.api or args.base).rstrip('/')
    fx = json.loads(args.fixture.read_text(encoding='utf-8'))
    verify = not args.insecure
    if args.insecure:
        import urllib3
        urllib3.disable_warnings()
    portal_csrf.enable(base)
    https = urlsplit(base).scheme == 'https'
    tag = f'SMOKE-MS11-{datetime.now(timezone.utc):%Y%m%d%H%M}-{secrets.token_hex(2)}'
    print(f'INFO  base {base} · api {api} · tag {tag} (no credential or token is printed)', flush=True)

    def api_login(role):
        email, password = creds(role)
        return requests.post(api + '/api/v1/auth/login', json={'email': email, 'password': password}, timeout=20,
                             verify=verify)

    def portal_session(token):
        http = requests.Session()
        http.verify = verify
        http.cookies.set('access_token', token, domain=urlsplit(base).hostname, path='/')
        return http

    def bearer(token):
        return {'Authorization': f'Bearer {token}'}

    # ===== 3. nginx / Gateway =======================================================================================
    health = requests.get(api + '/healthz', timeout=10, verify=verify)
    check(3, 'Gateway /healthz answers', health.status_code == 200, health.status_code)
    internal = requests.get(api + '/api/v1/audit/events', timeout=10, verify=verify)
    check(3, 'internal route /api/v1/audit/events is NOT published (404)', internal.status_code == 404, internal.status_code)
    if https:
        check(3, 'HSTS header on the portal', 'strict-transport-security' in {k.lower() for k in
              requests.get(base + '/sign-in', timeout=10, verify=verify).headers})
        plain = requests.get((args.http_url or 'http://' + urlsplit(base).hostname).rstrip('/') + '/sign-in', timeout=10,
                             allow_redirects=False)
        check(3, 'plain HTTP redirects to HTTPS', plain.status_code in (301, 308)
              and plain.headers.get('Location', '').startswith('https://'), plain.status_code)
    else:
        report(3, 'HSTS / HTTP->HTTPS redirect', 'SKIP', 'base is not https (test front)')

    # ===== 1. Login of the three roles ==============================================================================
    tokens, users = {}, {}
    for role, landing in (('ADMIN', '/admin/dashboard'), ('SUPERVISOR', '/supervisor/dashboard'), ('OPERATOR', '/operator/sessions')):
        response = api_login(role)
        body = response.json() if response.headers.get('Content-Type', '').startswith('application/json') else {}
        tokens[role] = body.get('access_token')
        users[role] = body.get('user') or {}
        page = portal_session(tokens[role]).get(base + landing, timeout=30, allow_redirects=False) if tokens[role] else None
        check(1, f'{role.lower()}: login through the Gateway and landing page {landing}',
              response.status_code == 200 and page is not None and page.status_code == 200,
              (response.status_code, page.status_code if page is not None else None))
    if not all(tokens.values()):
        return finish(args.out)
    spd, op = portal_session(tokens['SUPERVISOR']), portal_session(tokens['OPERATOR'])

    # ===== 2. The services (as the Supervisor sees them, through MonitoringService) ==================================
    page = spd.get(base + '/supervisor/services-health', timeout=30).text
    status = dict(re.findall(r'data-component="([a-z-]+)" data-status="([a-z]+)"', page))
    skipped = {c.strip() for c in args.not_monitored.split(',') if c.strip()}
    down = {name: status.get(name, 'missing') for name in MONITORED if status.get(name) != 'up' and name not in skipped}
    check(2, 'Services health (Supervisor): portal, 6 services, VisionWorker, PostgreSQL, Redis, MongoDB, Garage "up"',
          not down, down)
    for name in sorted(skipped):
        report(2, f'{name} in MonitoringService', 'SKIP', f'declared not monitored in this environment (status: {status.get(name)})')
    if args.local_ports:
        bad = {}
        for name, (port, path) in LOCAL_PORTS.items():
            try:
                code = requests.get(f'http://127.0.0.1:{port}{path}', timeout=5).status_code
            except requests.RequestException as exc:
                code = type(exc).__name__
            if code != 200:
                bad[name] = code
        check(2, 'the 9 services + portal answer on 127.0.0.1:5000-5009 (MonitoringService and IntegrationService included)',
              not bad, bad)
    else:
        report(2, 'local ports 5000-5009 (MonitoringService and IntegrationService health)', 'SKIP', 'run with --local-ports on the VM')

    # ===== 5. Token revocation ======================================================================================
    fresh = api_login('SUPERVISOR').json()
    me_before = requests.get(api + '/api/v1/auth/me', headers=bearer(fresh['access_token']), timeout=10, verify=verify)
    out = requests.post(api + '/api/v1/auth/logout', json={'refresh_token': fresh['refresh_token']},
                        headers=bearer(fresh['access_token']), timeout=10, verify=verify)
    me_after = requests.get(api + '/api/v1/auth/me', headers=bearer(fresh['access_token']), timeout=10, verify=verify)
    catalog_after = requests.get(api + '/api/v1/catalog/kits', headers=bearer(fresh['access_token']), timeout=10, verify=verify)
    check(5, 'logout revokes the token in AuthService AND in a microservice (Redis revocation)',
          me_before.status_code == 200 and out.status_code in (200, 204) and me_after.status_code == 401
          and catalog_after.status_code == 401, (me_before.status_code, out.status_code, me_after.status_code,
                                                 catalog_after.status_code))

    # ===== 14. Trays (CatalogService, PostgreSQL persistence) =======================================================
    tray_code = tag[-16:].replace('-', '')
    created = requests.post(api + '/api/v1/catalog/trays', headers=bearer(tokens['SUPERVISOR']), timeout=20, verify=verify,
                            json={'code': tray_code, 'name': f'{tag} tray', 'kit_id': fx['kit_id'], 'notes': tag})
    tray = created.json() if created.status_code in (200, 201) else {}
    tray = tray.get('tray', tray)
    read = requests.get(f"{api}/api/v1/catalog/trays/{tray.get('id')}", headers=bearer(tokens['SUPERVISOR']), timeout=20,
                        verify=verify) if tray.get('id') else None
    check(14, 'tray created and read back (CatalogService -> PostgreSQL)',
          read is not None and read.status_code == 200 and tray_code in read.text, (created.status_code, created.text[:150]))
    check(4, 'PostgreSQL persistence: written row read back through another request', read is not None and read.status_code == 200)

    # ===== 8. Capture and recapture, 7. pinned model, 6. WSS, 12. camera ==============================================
    videos = fx.get('videos') or {}
    sid = None
    if not (videos.get('one_removed') and videos.get('full') and Path(videos['one_removed']).exists()
            and Path(videos['full']).exists()):
        for item, name in ((8, 'capture and recapture'), (7, 'pinned model'), (6, 'WSS'), (9, 'evidence'), (10, 'notifications')):
            report(item, name, 'SKIP', 'fixture videos not available')
    else:
        body = {'operator_user_id': fx['operator_user_id'], 'procedure_type_id': fx['procedure_type_id'],
                'room_id': fx['room_id'], 'station_id': fx['station_id'], 'kit_id': fx['kit_id'],
                'scheduled_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'phase_code': 'start',
                'physicians': [{'physician_id': fx['physician_id']}],
                'privacy_notice_version_id': fx['privacy_notice_version_id'],
                'expected_lines': [{'family_id': fx['family_id'], 'expected_quantity': 1}]}
        scheduled = requests.post(api + '/api/v1/sessions/schedule', json=body, headers=bearer(tokens['SUPERVISOR']),
                                  timeout=30, verify=verify)
        sid = (scheduled.json() or {}).get('id') if scheduled.status_code in (200, 201) else None
        check(8, 'Supervisor schedules a test session through the Gateway (no patient)', sid, (scheduled.status_code, scheduled.text[:200]))
    if sid:
        cap = f'{base}/operator/sessions/{sid}/capture'
        begun = op.post(f'{base}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=60)
        op.get(cap, timeout=60)
        check(8, 'Operator starts the session', begun.status_code in (302, 303), begun.status_code)
        if fx.get('camera_id'):
            cam = op.post(cap + '/camera', json={'camera_id': fx['camera_id']}, timeout=30)   # the page sends JSON
            check(12, 'Operator selects the station camera', cam.status_code == 200, (cam.status_code, cam.text[:150]))
        else:
            report(12, 'camera selection', 'SKIP', 'no camera_id in the fixture')

        class Abort(Exception):
            pass

        def state():
            response = op.get(cap + '/state', timeout=30)
            if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('application/json'):
                raise Abort(f'capture/state {response.status_code}: {response.text[:200]}')
            return response.json()

        def wait_state(pred, seconds):
            deadline = time.time() + seconds
            last = {}
            while time.time() < deadline:
                last = state()
                if pred(last.get('session') or {}):
                    return last
                time.sleep(2)
            return last

        def upload(path):
            with open(path, 'rb') as fh:
                sent = op.post(cap + '/upload', files={'video': (Path(path).name, fh, 'video/x-msvideo')}, timeout=600)
            if sent.status_code not in (200, 202):
                raise Abort(f'upload {sent.status_code}: {sent.text[:200]}')
            return wait_state(lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None,
                              args.timeout_video)

        def play():
            op.post(cap + '/play', timeout=60)
            return wait_state(lambda s: s.get('status') in ('ready', 'error') and (s.get('final_frame') or {}).get('ok')
                              is not None, args.timeout_video)

        try:
            first = upload(videos['one_removed'])
            initial_total = len(((first.get('session') or {}).get('initial_frame') or {}).get('boxes') or [])
            registered = op.post(cap + '/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
            check(8, f'initial count from the first frame (YOLO): {initial_total}', registered.status_code == 200 and initial_total > 0,
                  (registered.status_code, registered.text[:150]))

            # 6. WSS: ticket from the portal, upgrade through nginx, VisionWorker checks the ticket (no forged ticket accepted)
            try:
                import websocket
                ticket = op.get(cap + '/ws-ticket', timeout=30).json().get('ticket')
                ws_url = ('wss://' if https else 'ws://') + urlsplit(base).netloc + '/ws'

                def bind(tk):
                    ws = websocket.create_connection(ws_url, origin=base, timeout=15,
                                                     sslopt=None if verify else {'cert_reqs': 0, 'check_hostname': False})
                    try:
                        ws.send(json.dumps({'type': 'session.bind', 'session_id': sid, 'auth_token': tk}))
                        msgs = []
                        for _ in range(3):
                            try:
                                msgs.append(json.loads(ws.recv()))
                            except Exception:  # noqa: BLE001
                                break
                        return ' '.join(str(m.get('message', '')) for m in msgs if isinstance(m, dict)).lower()
                    finally:
                        ws.close()
                good, forged = bind(ticket), bind('forged.ticket.0.0.0')
                check(6, f'{"WSS" if https else "WS"} /ws through nginx: valid ticket accepted, forged ticket refused',
                      'ticket' not in good and 'origin' not in good and 'ticket' in forged, (good[:120], forged[:120]))
            except Exception as exc:  # noqa: BLE001
                check(6, 'WebSocket through nginx', False, f'{type(exc).__name__}: {exc}')

            played = play()
            final_rejected = op.post(cap + '/final', timeout=60)
            final_total = len(((played.get('session') or {}).get('final_frame') or {}).get('boxes') or [])
            check(8, f'final count {final_total} != reference {initial_total} is rejected (409, capture again)',
                  final_total != initial_total and final_rejected.status_code == 409, (final_total, final_rejected.status_code))
            upload(videos['full'])
            replayed = play()
            recaptured = len(((replayed.get('session') or {}).get('final_frame') or {}).get('boxes') or [])
            accepted = op.post(cap + '/final', timeout=60)
            check(8, f'recapture: final {recaptured} = reference {initial_total} accepted', recaptured == initial_total
                  and accepted.status_code == 200, (recaptured, accepted.status_code, accepted.text[:150]))

            detail = requests.get(f'{api}/api/v1/sessions/{sid}?expand=detail', headers=bearer(tokens['SUPERVISOR']), timeout=30, verify=verify)
            snap = detail.json() if detail.ok else {}
            snap = snap.get('session', snap) if isinstance(snap, dict) else {}
            pinned_model = snap.get('vision_model') or {}
            payloads = [e.get('payload') or {} for e in snap.get('events', [])]
            shas = {p['model_sha256'] for p in payloads if p.get('model_sha256')}
            versions = {p['model_version'] for p in payloads if p.get('model_version')}
            check(7, f"session pinned to {pinned_model.get('version_tag')} (sha256 recorded); every count used that model",
                  bool(pinned_model.get('sha256')) and shas <= {pinned_model.get('sha256')} and versions <= {pinned_model.get('version_tag')}
                  and bool(versions), (detail.status_code, pinned_model.get('version_tag'), shas, versions, list(snap)[:8]))

            # 9. Evidence of the Supervisor (EvidenceService -> Garage through the signed URL)
            listed = requests.get(f'{api}/api/v1/sessions/{sid}/evidence', headers=bearer(tokens['SUPERVISOR']), timeout=30, verify=verify)
            items = listed.json() if listed.ok else []
            items = items.get('items', items) if isinstance(items, dict) else items
            images = [i for i in items if str(i.get('content_type', '')).startswith('image/')]
            signed = requests.get(f"{api}/api/v1/evidence/{images[0]['id']}", headers=bearer(tokens['SUPERVISOR']), timeout=30,
                                  verify=verify).json() if images else {}
            blob = requests.get(signed['signed_url'], timeout=30, verify=verify) if signed.get('signed_url') else None
            check(9, f'Supervisor evidence: {len(images)} images; one downloaded from Garage through its signed URL',
                  len(images) >= 2 and blob is not None and blob.status_code == 200 and blob.content[:2] == b'\xff\xd8',
                  (listed.status_code, len(images), blob.status_code if blob is not None else None))
            check(4, 'Garage persistence: the evidence object is served back', blob is not None and blob.status_code == 200)

            # 10. Notifications: escalate a second rejected run would be long; validation sends the session to review
            html = op.get(f'{base}/operator/sessions/{sid}/vision-validation', timeout=30).text
            values = {k: int(v) for k, v in re.findall(r'name="(validated_[0-9a-f-]{36})"[^>]*value="(\d+)"', html)}
            form = {k: str(v) for k, v in values.items()}
            for key, value in values.items():
                fid = key.split('_', 1)[1]
                expected = 1 if fid == fx['family_id'] else 0
                form[f'reason_{fid}'] = 'surplus' if value > expected else ('shortage' if value < expected else '')
                form[f'notes_{fid}'] = tag
            form.update({'extra_instruments': str(max(0, recaptured - sum(values.values()))), 'extra_notes': tag})
            validated = op.post(f'{base}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False, timeout=60)
            check(8, 'validation with the same total accepted (session to the Supervisor)', validated.status_code in (302, 303),
                  validated.status_code)
            found = False
            for _ in range(20):
                listing = requests.get(api + '/api/v1/notifications?per_page=50', headers=bearer(tokens['SUPERVISOR']), timeout=20,
                                       verify=verify)
                if listing.ok and sid in listing.text:
                    found = True
                    break
                time.sleep(3)
            check(10, 'the Supervisor receives a notification about the test session', found, listing.status_code)
        except Abort as exc:
            check(8, 'capture flow through the Gateway (upload, YOLO, final, recapture)', False, exc)
            for item, name in ((7, 'pinned model'), (6, 'WSS'), (9, 'evidence'), (10, 'notifications')):
                report(item, name, 'SKIP', 'not reached: the capture flow stopped (see item 8)')

    # ===== 11. Reports and exports ==================================================================================
    for fmt, magic in (('csv', b'\xef\xbb\xbf'), ('pdf', b'%PDF')):
        # MS-06: exports are a download of the Supervisor's portal screen; the API route is deliberately NOT published
        exported = spd.get(f'{base}/supervisor/reports/export.{fmt}?date_range=7d', timeout=90)
        check(11, f'report export {fmt.upper()} from the Supervisor screen', exported.status_code == 200
              and exported.content.startswith(magic), (exported.status_code, exported.content[:8]))
    hidden = requests.get(f'{api}/api/v1/sessions/reports/export?format=csv', headers=bearer(tokens['SUPERVISOR']), timeout=20,
                          verify=verify)
    check(11, 'the export API route is not published by the Gateway (404)', hidden.status_code == 404, hidden.status_code)

    # ===== 12. Stations (MonitoringService, as shown to the Supervisor) =============================================
    stations = dict(re.findall(r'data-station="([^"]+)" data-status="([a-z]+)"', page))
    check(12, f'stations table of the Supervisor lists {len(stations)} station(s)', len(stations) >= 1, stations)

    # ===== 13. Sync and 15. FHIR (test machine client only) =========================================================
    meta = requests.get(api + '/fhir/r4/metadata', timeout=20, verify=verify)
    check(15, 'FHIR CapabilityStatement (public metadata)', meta.status_code == 200 and 'CapabilityStatement' in meta.text, meta.status_code)
    cid, csecret = os.environ.get('PEF_SMOKE_CLIENT_ID'), os.environ.get('PEF_SMOKE_CLIENT_SECRET')
    if cid and csecret:
        issued = requests.post(api + '/api/v1/auth/token', data={'grant_type': 'client_credentials', 'client_id': cid,
                                                                'client_secret': csecret}, timeout=20, verify=verify)
        client_token = issued.json().get('access_token') if issued.ok else None
        changes = requests.get(api + '/api/v1/sync/changes?limit=5', headers=bearer(client_token or 'x'), timeout=20, verify=verify)
        check(13, 'sync: machine client token + /api/v1/sync/changes', issued.ok and changes.status_code == 200,
              (issued.status_code, changes.status_code))
        location = requests.get(api + '/fhir/r4/Location?_count=1', headers=bearer(client_token or 'x'), timeout=20, verify=verify)
        check(15, 'FHIR read with the machine client (Location search -> Bundle)',
              location.status_code == 200 and '"Bundle"' in location.text, location.status_code)
    else:
        report(13, 'sync with a machine client', 'SKIP', 'PEF_SMOKE_CLIENT_ID / PEF_SMOKE_CLIENT_SECRET not set')
        report(15, 'FHIR read with a machine client', 'SKIP', 'no machine client')
    report(15, 'FHIR deliveries to an external server', 'SKIP', 'never from this smoke test (authorized test endpoint only, manual)')
    report(4, 'MongoDB persistence', 'SKIP' if 'mongo' not in status else ('PASS' if status.get('mongo') == 'up' else 'FAIL'),
           'checked as "up" in MonitoringService; persistence across a restart is a manual step')
    print(f'INFO  test data left in the TEST institution: tag {tag}, session {sid}, tray {tray_code}', flush=True)
    return finish(args.out)


def finish(out):
    counts = {s: sum(r['status'] == s for r in results) for s in ('PASS', 'FAIL', 'SKIP')}
    print(f"\n{counts['PASS']} PASS · {counts['FAIL']} FAIL · {counts['SKIP']} SKIP")
    if out:
        out.write_text(json.dumps({'results': results, 'counts': counts}, indent=2, ensure_ascii=False), encoding='utf-8')
    sys.exit(0 if counts['PASS'] and not counts['FAIL'] else 1)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - an unexpected error must still leave a report, never a silent crash
        import socket
        if OUT.get('resolve') is not None:
            print(f"DEBUG getaddrinfo still pinned: {socket.getaddrinfo is OUT['resolve']}", file=sys.stderr)
        report('-', 'smoke test aborted by an unexpected error (later checks not run)', 'FAIL', f'{type(exc).__name__}: {exc}')
        finish(OUT['path'])
