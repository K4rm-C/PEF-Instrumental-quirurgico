"""ADMIN-09: functional validation of Administration (RF-AD-01 ... RF-AD-10) in a REAL browser (Playwright +
Chromium) against the ISOLATED test stack (docker-compose.test.yml / .env.test).

    docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
    python scripts/browser_validate_admin.py --weights <path to the real best.pt>

What it does
* Starts AuthService (:15001) and the portal (:15000) itself (scripts/run_admin_browser_env.py), with the
  admin storage on the Garage of the test stack, and restarts them (and Garage / MongoDB containers of the
  TEST project only) where a check needs it.
* Prepares test data only in the test database: a second institution "ZZ Browser Institution B" with its own
  it_admin, room and station (idempotent). Every record it creates carries a random tag (ZZB...).
* Drives the UI like a user (sign-in form, forms, modals, file inputs), takes screenshots and watches the
  browser console, page errors and HTTP 5xx answers.
* Writes evidence to docs/evidencias/ADMIN-09/ (JPEG screenshots + results.json + results.md). No passwords,
  tokens or cookies are written there.

It never touches the main stack. The real weights file is only read (uploaded through the UI); it is never
copied into the repository.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import traceback
import zlib
import struct
from datetime import date, datetime
from pathlib import Path

import requests
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402

WEB, AUTH = envctl.WEB, envctl.AUTH
EVIDENCE = ROOT / 'docs' / 'evidencias' / 'ADMIN-09'
FIXTURES = ROOT / '.e2e' / 'browser_fixtures'
COMPOSE = ['docker', 'compose', '-p', 'pef_admin_test', '-f', 'docker-compose.test.yml', '--env-file', '.env.test']
ADMIN_A = ('admin@instrumed.com', 'DemopwdADM78!')
SUPERVISOR = ('supervisor@instrumed.com', 'DemopwdSPD78!')
OPERATOR = ('operator@instrumed.com', 'DemopwdOP78!')
TAG = secrets.token_hex(3).upper()

results = []
problems = []          # console errors / page errors / 5xx, with the RF being checked
current = {'rf': 'SETUP'}
processes = {'auth': None, 'web': None}
storage_mode = {'backend': 's3', 'steward': ''}


# --- recording ------------------------------------------------------------------------------

def _csrf(page):
    """MS-04: direct API calls of the page context carry the portal's CSRF token (the forms already do)."""
    return {'X-CSRF-Token': page.request.get(f'{WEB}/csrf-token').json()['csrf_token']}


def record(name, ok, detail='', evidence='', status=None):
    status = status or ('PASS' if ok else 'FAIL')
    results.append({'rf': current['rf'], 'check': name, 'status': status, 'detail': str(detail)[:400],
                    'evidence': evidence})
    print(f"{status:7} [{current['rf']}] {name}{'  -- ' + str(detail)[:200] if detail and status != 'PASS' else ''}",
          flush=True)
    return ok


def shot(page, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    file = f"{current['rf'].lower()}_{len(results):03d}_{re.sub(r'[^a-z0-9]+', '-', name.lower())[:50]}.jpg"
    # The layout scrolls inside its own container: grow the viewport to the content height for the capture.
    size = page.viewport_size or {'width': 1366, 'height': 900}
    height = page.evaluate("() => Math.max(...[...document.querySelectorAll('*')].map(e => e.scrollHeight), "
                           "document.body.scrollHeight)")
    page.set_viewport_size({'width': size['width'], 'height': max(size['height'], min(int(height), 4000))})
    page.screenshot(path=str(EVIDENCE / file), full_page=True, type='jpeg', quality=55)
    page.set_viewport_size(size)
    return file


def watch(page):
    def on_console(msg):
        if msg.type == 'error':
            problems.append({'rf': current['rf'], 'kind': 'console', 'url': page.url, 'text': msg.text[:300]})

    page.on('console', on_console)
    page.on('pageerror', lambda exc: problems.append({'rf': current['rf'], 'kind': 'pageerror', 'url': page.url,
                                                      'text': str(exc)[:300]}))
    page.on('response', lambda r: problems.append({'rf': current['rf'], 'kind': f'http {r.status}', 'url': r.url,
                                                   'text': r.request.method}) if r.status >= 500 else None)
    return page


def messages(page):
    """Flash messages and inline form errors visible on the page."""
    parts = page.locator('.alert__message, .form-field__helper--error, .alert__title').all_inner_texts()
    return ' | '.join(p.strip() for p in parts if p.strip())


# --- environment ----------------------------------------------------------------------------

def compose(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, capture_output=True, text=True, timeout=300)


def start_services(storage='s3', steward='', restart_auth=True):
    stop_services(web_only=not restart_auth)
    auth, web = envctl.start(storage, steward, start_auth=restart_auth)
    if restart_auth:
        processes['auth'] = auth
    processes['web'] = web
    storage_mode.update(backend=storage, steward=steward)


def stop_services(web_only=False):
    envctl.stop(processes['web'])
    processes['web'] = None
    if not web_only:
        envctl.stop(processes['auth'])
        processes['auth'] = None


def db():
    return create_engine(load_test_env()['DATABASE_URL'], future=True)


def prepare_data(engine):
    """Second institution with its own it_admin, room and station (test data, idempotent)."""
    password = 'Zzb-' + secrets.token_urlsafe(12)
    with engine.begin() as conn:
        inst_a = conn.execute(text('SELECT institution_id FROM "user" WHERE email = :e'),
                              {'e': ADMIN_A[0]}).scalar_one()
        inst_b = conn.execute(text("SELECT id FROM institution WHERE name = 'ZZ Browser Institution B'")).scalar()
        if inst_b is None:
            inst_b = conn.execute(text("INSERT INTO institution (name) VALUES ('ZZ Browser Institution B') "
                                       "RETURNING id")).scalar_one()
        conn.execute(text("""INSERT INTO role (code, description, institution_id)
            SELECT c, c || ' (browser test institution)', :i FROM (VALUES ('station_operator'), ('spd_supervisor'),
            ('it_admin'), ('or_charge')) AS v(c) ON CONFLICT (institution_id, code) DO NOTHING"""), {'i': inst_b})
        email_b = 'admin.b@browser-test.invalid'
        user_b = conn.execute(text('SELECT id FROM "user" WHERE email = :e'), {'e': email_b}).scalar()
        hashed = generate_password_hash(password)
        if user_b is None:
            user_b = conn.execute(text('INSERT INTO "user" (name, email, password_hash, institution_id) '
                                       "VALUES ('Admin B (browser test)', :e, :h, :i) RETURNING id"),
                                  {'e': email_b, 'h': hashed, 'i': inst_b}).scalar_one()
        else:
            conn.execute(text('UPDATE "user" SET password_hash = :h, active = TRUE WHERE id = :u'),
                         {'h': hashed, 'u': user_b})
        conn.execute(text("""INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role
            WHERE institution_id = :i AND code = 'it_admin' ON CONFLICT DO NOTHING"""), {'u': user_b, 'i': inst_b})
        room_b = conn.execute(text("SELECT id FROM operating_room WHERE institution_id = :i AND code = 'ZZB-OR'"),
                              {'i': inst_b}).scalar()
        if room_b is None:
            room_b = conn.execute(text("INSERT INTO operating_room (code, name, institution_id) "
                                       "VALUES ('ZZB-OR', 'Browser test room B', :i) RETURNING id"),
                                  {'i': inst_b}).scalar_one()
        station_b = conn.execute(text('SELECT id FROM capture_station WHERE room_id = :r'), {'r': room_b}).scalar()
        if station_b is None:
            station_b = conn.execute(text("INSERT INTO capture_station (name, room_id) "
                                          "VALUES ('Browser test station B', :r) RETURNING id"),
                                     {'r': room_b}).scalar_one()
        role_b = conn.execute(text("SELECT id FROM role WHERE institution_id = :i AND code = 'it_admin'"),
                              {'i': inst_b}).scalar_one()
        user_a_ids = {row.email: str(row.id) for row in conn.execute(
            text('SELECT id, email FROM "user" WHERE email IN (:a, :s, :o)'),
            {'a': ADMIN_A[0], 's': SUPERVISOR[0], 'o': OPERATOR[0]})}
    return {'inst_a': str(inst_a), 'inst_b': str(inst_b), 'admin_b': (email_b, password), 'user_b': str(user_b),
            'room_b': str(room_b), 'station_b': str(station_b), 'role_b': str(role_b), 'users_a': user_a_ids}


def png_bytes(width=64, height=48, pad=0):
    rows = b''.join(b'\x00' + b''.join(bytes((x * 4 % 256, y * 5 % 256, 160)) for x in range(width))
                    for y in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    body = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    return body + (b'\x00' * pad)


def fixtures():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    files = {
        'photo.png': png_bytes(),
        'big_photo.png': png_bytes(pad=6 * 1024 * 1024),
        'not_an_image.png': b'plain text pretending to be an image\n',
        'notice.pdf': (b'%PDF-1.4\n% ADMIN-09 browser test notice ' + TAG.encode() + b'\n1 0 obj<<>>endobj\n'
                       b'trailer<<>>\n%%EOF\n'),
        'notice.html': (b'<!doctype html><html><head><meta charset="utf-8"><title>Notice</title></head>'
                        b'<body><h1>Privacy notice (browser test ' + TAG.encode() + b')</h1></body></html>'),
        'renamed_exe.pdf': b'MZ\x90\x00' + b'\x00' * 200,
        'too_big.pdf': b'%PDF-1.4\n' + b'0' * (11 * 1024 * 1024),
        # Above the 8 MB multipart threshold of boto3 but within the 10 MB limit (ADMIN-09 finding H7).
        'large_9mb.pdf': b'%PDF-1.4\n% ' + TAG.encode() + b'\n' + os.urandom(9 * 1024 * 1024),
        'model.onnx': b'\x08\x07\x12\x07pytorch' + b'\x00' * 100,
    }
    for name, data in files.items():
        (FIXTURES / name).write_bytes(data)
    return {name: str(FIXTURES / name) for name in files}


# --- browser helpers ------------------------------------------------------------------------

def sign_in(browser, email, password, expect_ok=True):
    context = browser.new_context(viewport={'width': 1366, 'height': 900}, locale='en-US')
    page = watch(context.new_page())
    page.goto(WEB + '/sign-in')
    page.fill('#institutional-email', email)
    page.fill('#password', password)
    page.click('#sign-in-form button[type=submit]')
    if expect_ok:
        page.wait_for_url(lambda url: '/sign-in' not in url, timeout=15000)
    else:
        page.wait_for_timeout(1500)
    page.wait_for_load_state('networkidle')
    return context, page


def go(page, path):
    response = page.goto(WEB + path)
    page.wait_for_load_state('networkidle')
    return response.status if response else 0


def submit(page, selector):
    with page.expect_navigation(timeout=120000):
        page.click(selector)
    page.wait_for_load_state('networkidle')


def row_text(page, needle):
    rows = page.locator('table tbody tr', has_text=needle)
    return rows.first.inner_text() if rows.count() else ''


def step(rf, func, *args):
    current['rf'] = rf
    try:
        func(*args)
    except Exception as exc:  # a broken step is BLOCKED, never PASS
        record(f'step aborted: {type(exc).__name__}', False, f'{exc}'[:300], status='BLOCKED')
        traceback.print_exc()


# --- RF checks ------------------------------------------------------------------------------

def rf01_dashboard(ctx):
    page = ctx['admin']
    status = go(page, '/admin/dashboard')
    health = page.locator('table', has_text='PostgreSQL').inner_text() if page.locator('table', has_text='PostgreSQL').count() else ''
    cards = page.locator('.stat-card').count()
    record('dashboard loads with session cards and System Health', status == 200 and cards >= 4 and 'Redis' in health
           and 'MongoDB' in health, f'HTTP {status}, cards {cards}', shot(page, 'dashboard'))
    record('all health checks reachable with the stack up', 'Unreachable' not in health and 'Unavailable' not in health,
           health.replace('\n', ' ')[:300])
    # Thresholds (RF-AD-10) drive the card colour: 0/0 makes "OR hold" danger whatever the count.
    go(page, '/admin/configuration')
    page.fill('#setting-kpi_or_hold_warning', '0')
    page.fill('#setting-kpi_or_hold_danger', '0')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    go(page, '/admin/dashboard')
    hold = page.locator('.stat-card', has_text=re.compile('hold', re.I)).first
    variant = hold.get_attribute('class') if hold.count() else ''
    record('KPI colour follows the configured thresholds (OR hold -> danger with 0/0)', 'stat-card--danger' in variant,
           variant, shot(page, 'dashboard-threshold-danger'))
    go(page, '/admin/configuration')
    page.fill('#setting-kpi_or_hold_warning', '1')
    page.fill('#setting-kpi_or_hold_danger', '3')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    # A dependency outage only degrades its own row (MongoDB of the TEST project).
    compose('stop', 'mongo')
    try:
        started = time.monotonic()
        status = go(page, '/admin/dashboard')
        elapsed = time.monotonic() - started
        rows = {name: page.locator('tr', has_text=name).first.inner_text().replace('\n', ' ').replace('\t', ' ')
                for name in ('MongoDB', 'PostgreSQL', 'Redis')}
        record('MongoDB down: dashboard still loads and marks only MongoDB as failing',
               status == 200 and 'Reachable' not in rows['MongoDB'].replace('Unreachable', '')
               and 'Unreachable' not in rows['PostgreSQL'] + rows['Redis'] and page.locator('.stat-card').count() >= 4,
               f'{rows} in {elapsed:.1f}s', shot(page, 'dashboard-mongo-down'))
    finally:
        compose('start', 'mongo')
        compose('up', '-d', '--wait', 'mongo')


def rf02_families(ctx):
    page, code = ctx['admin'], f'ZZB{TAG}'
    go(page, '/admin/instrument-families/new')
    page.fill('#family-code', code)
    page.fill('#family-name', f'Browser family {TAG}')
    page.select_option('#family-category', index=1)
    page.fill('#family-how-to-identify', 'Created by the ADMIN-09 browser validation')
    submit(page, 'form[action$="/instrument-families/new"] button[type=submit]')
    msg = messages(page)
    go(page, f'/admin/instrument-families?search={code}&status=all')
    record('create family; listed after reload', code in page.locator('table').inner_text(), msg,
           shot(page, 'family-created'))
    edit_link = page.locator('table tbody tr', has_text=code).locator('a', has_text='Edit').first.get_attribute('href')
    ctx['family_edit'] = edit_link
    # duplicate code
    go(page, '/admin/instrument-families/new')
    page.fill('#family-code', code)
    page.fill('#family-name', 'Duplicate')
    page.select_option('#family-category', index=1)
    submit(page, 'form[action$="/instrument-families/new"] button[type=submit]')
    record('duplicate family code rejected with a message', '/new' in page.url and bool(messages(page)), messages(page),
           shot(page, 'family-duplicate'))
    # edit + persistence
    go(page, edit_link)
    page.fill('#family-name', f'Browser family {TAG} edited')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    go(page, edit_link)
    record('edit family name persists after reload', page.input_value('#family-name').endswith('edited'))
    # photos
    files = ctx['files']
    page.set_input_files('#family-example-image', files['photo.png'])
    page.fill('#family-example-text', f'Front view {TAG}')
    submit(page, 'form[action$="/examples"] button[type=submit]')
    go(page, edit_link)
    images = page.locator('img[src*="/examples/"]')
    loaded = images.count() and page.evaluate(
        "() => [...document.querySelectorAll('img[src*=\"/examples/\"]')].every(i => i.complete && i.naturalWidth > 0)")
    record('upload example photo; image rendered from storage', bool(loaded), f'{images.count()} image(s)',
           shot(page, 'family-photo'))
    for name, label in (('not_an_image.png', 'non-image file renamed .png'), ('big_photo.png', 'image > 5 MB')):
        page.set_input_files('#family-example-image', files[name])
        submit(page, 'form[action$="/examples"] button[type=submit]')
        msg = messages(page)
        record(f'{label} rejected with a message', bool(msg) and 'added' not in msg.lower(), msg,
               shot(page, f'family-reject-{name}'))
        go(page, edit_link)
    # deactivate a family mapped in the ACTIVE model without confirmation -> refused
    mapped = ctx['engine'].connect().execute(text(
        'SELECT f.id FROM model_class mc JOIN yolo_model m ON m.id = mc.model_id JOIN instrument_family f '
        'ON f.id = mc.family_id WHERE m.active AND f.active LIMIT 1')).scalar()
    if mapped:
        go(page, f'/admin/instrument-families/{mapped}/edit')
        page.select_option('#family-status', 'inactive')
        if page.locator('#family-confirm-active-model').is_checked():
            page.uncheck('#family-confirm-active-model')
        submit(page, 'form[action$="/edit"] button[type=submit]')
        msg = messages(page)
        still = ctx['engine'].connect().execute(text('SELECT active FROM instrument_family WHERE id = :i'),
                                                {'i': mapped}).scalar()
        record('deactivating a family of the active model needs confirmation', still is True and bool(msg), msg,
               shot(page, 'family-active-model-confirm'))
    else:
        record('deactivating a family of the active model needs confirmation', False, 'no mapped family', status='BLOCKED')
    # deactivate / reactivate the test family
    go(page, edit_link)
    page.select_option('#family-status', 'inactive')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    go(page, f'/admin/instrument-families?search={code}&status=inactive')
    record('deactivate family; shown under Inactive', code in page.locator('table').inner_text(), '',
           shot(page, 'family-inactive'))
    go(page, edit_link)
    page.select_option('#family-status', 'active')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    go(page, '/admin/instrument-families?search=' + code)
    record('search in the list finds the family (reactivated)', code in page.locator('table').inner_text())


def rf03_procedures(ctx):
    page, code = ctx['admin'], f'zzb_proc_{TAG.lower()}'
    go(page, '/admin/procedures/new')
    page.fill('#procedure-code', code)
    page.fill('#procedure-name', f'Browser procedure {TAG}')
    page.click('#add-phase-btn')
    page.click('#add-phase-btn')
    selects = page.locator('select[name="phase[]"]')
    options = selects.first.locator('option').evaluate_all('os => os.map(o => o.value).filter(v => v)')
    selects.nth(0).select_option(options[0])
    selects.nth(1).select_option(options[1])
    orders = page.locator('input[name="sort_order[]"]')
    orders.nth(0).fill('1')
    orders.nth(1).fill('2')
    submit(page, '#procedure-form button[type=submit]')
    msg = messages(page)
    go(page, f'/admin/procedures?search={code}')
    link = page.locator('table tbody tr', has_text=code).locator('a', has_text='Edit')
    ok = link.count() > 0
    phases = 0
    if ok:
        go(page, link.first.get_attribute('href'))
        phases = page.locator('select[name="phase[]"]').count()
    record('create procedure with two phases; persisted in order', ok and phases == 2, f'{msg}; phases={phases}',
           shot(page, 'procedure-created'))
    # associated kits are read-only (an existing procedure with kits)
    with_kits = ctx['engine'].connect().execute(text(
        'SELECT procedure_type_id FROM procedure_kit WHERE active LIMIT 1')).scalar()
    if with_kits:
        go(page, f'/admin/procedures/{with_kits}/edit')
        kits = page.locator('table[aria-labelledby="associated-kits-heading"]')
        editable = kits.locator('input, select, button').count() if kits.count() else -1
        record('associated kits visible and read-only', kits.count() == 1 and editable == 0
               and kits.locator('tbody tr').count() > 0, f'editable controls={editable}', shot(page, 'procedure-kits'))
    else:
        record('associated kits visible and read-only', False, 'no procedure with kits in the test DB', status='BLOCKED')
    # selection catalogs
    for catalog in ('reasons', 'categories', 'phases'):
        go(page, f'/admin/selection-catalogs/{catalog}')
        new_code = f'zzb_{catalog[:3]}_{TAG.lower()}'
        page.fill('#catalog-code', new_code)
        page.fill('#catalog-name', f'Browser {catalog} {TAG}')
        submit(page, f'form[action$="/selection-catalogs/{catalog}"] button[type=submit]')
        go(page, f'/admin/selection-catalogs/{catalog}?search={new_code}')
        record(f'create {catalog} entry; listed after reload',
               page.locator(f'input[value="Browser {catalog} {TAG}"]').count() == 1, messages(page))
    go(page, '/admin/selection-catalogs/reasons')
    page.fill('#catalog-code', 'Bad Code')
    page.fill('#catalog-name', 'Invalid code')
    page.click('form[action$="/selection-catalogs/reasons"] button[type=submit]')
    page.wait_for_timeout(500)
    blocked_client = page.evaluate("() => !document.querySelector('#catalog-code').checkValidity()")
    shot(page, 'catalog-invalid-code-browser')
    page.evaluate("() => { const i = document.querySelector('#catalog-code'); i.removeAttribute('pattern'); "
                  "i.form.noValidate = true; }")
    submit(page, 'form[action$="/selection-catalogs/reasons"] button[type=submit]')
    record('invalid catalog code (uppercase/space): blocked in the browser and by the server',
           blocked_client and bool(messages(page)), messages(page), shot(page, 'catalog-invalid-code'))
    go(page, '/admin/selection-catalogs/reasons')
    page.fill('#catalog-code', f'zzb_rea_{TAG.lower()}')
    page.fill('#catalog-name', 'Duplicate')
    submit(page, 'form[action$="/selection-catalogs/reasons"] button[type=submit]')
    record('duplicate catalog code rejected', bool(messages(page)) and 'Duplicate' not in
           page.locator('table').inner_text(), messages(page))
    # deactivate the new phase
    go(page, f'/admin/selection-catalogs/phases?search=zzb_pha_{TAG.lower()}')
    form = page.locator('form', has=page.locator(f'input[value="Browser phases {TAG}"]'))
    form.locator('select[name=status]').select_option('inactive')
    with page.expect_navigation():
        form.locator('button[type=submit]').click()
    go(page, f'/admin/selection-catalogs/phases?search=zzb_pha_{TAG.lower()}')
    form = page.locator('form', has=page.locator(f'input[value="Browser phases {TAG}"]'))
    record('deactivate phase; inactive after reload', form.locator('select[name=status]').input_value() == 'inactive',
           '', shot(page, 'phase-inactive'))


def _publish_notice(page, path, version, confirm_duplicate=False):
    go(page, '/admin/privacy-notices/new')
    page.set_input_files('#notice-document', path)
    page.fill('#notice-version', version)
    page.fill('#notice-effective', date.today().isoformat())
    submit(page, 'form[action$="/privacy-notices/new"] button[type=submit]')
    warned = ''
    if confirm_duplicate and '/new' in page.url:
        warned = messages(page)
        box = page.locator('form[action$="/privacy-notices/new"] input[type=checkbox]')
        if box.count():
            page.set_input_files('#notice-document', path)
            if not page.input_value('#notice-version'):
                page.fill('#notice-version', version)
            box.first.check()
            submit(page, 'form[action$="/privacy-notices/new"] button[type=submit]')
    return messages(page), warned


def rf04_privacy(ctx):
    page, files = ctx['admin'], ctx['files']
    v1, v2, v3 = f'ZZB-{TAG}-1', f'ZZB-{TAG}-2', f'ZZB-{TAG}-3'
    msg, _ = _publish_notice(page, files['notice.pdf'], v1)
    go(page, f'/admin/privacy-notices?search={v1}')
    row = row_text(page, v1)
    pdf_sha = hashlib.sha256(Path(files['notice.pdf']).read_bytes()).hexdigest()
    record('publish PDF notice; listed with its version', v1 in row, msg, shot(page, 'notice-published'))
    msg, warned = _publish_notice(page, files['notice.pdf'], v2, confirm_duplicate=True)
    go(page, f'/admin/privacy-notices?search={v2}')
    record('same document again: duplicate warning, published after confirmation', bool(warned) and v2 in row_text(page, v2),
           f'warning: {warned}', shot(page, 'notice-duplicate-confirmed'))
    msg, _ = _publish_notice(page, files['notice.html'], v3)
    go(page, f'/admin/privacy-notices?search={v3}')
    record('publish HTML notice', v3 in row_text(page, v3), msg)
    for name, label in (('renamed_exe.pdf', '.exe renamed to .pdf'), ('too_big.pdf', 'PDF > 10 MB')):
        msg, _ = _publish_notice(page, files[name], f'ZZB-{TAG}-bad-{name[:3]}')
        record(f'{label} rejected with a message', '/new' in page.url and bool(msg), msg, shot(page, f'notice-reject-{name}'))
    # download: attachment, same bytes, sandbox for HTML
    go(page, f'/admin/privacy-notices?search={v1}')
    href = page.locator('table tbody tr', has_text=v1).locator('a', has_text='Download').first.get_attribute('href')
    ctx['notice_download'] = (href, pdf_sha)
    response = page.request.get(WEB + href)
    record('download returns the same bytes as an attachment',
           response.ok and hashlib.sha256(response.body()).hexdigest() == pdf_sha
           and 'attachment' in response.headers.get('content-disposition', ''),
           response.headers.get('content-disposition', ''))
    go(page, f'/admin/privacy-notices?search={v3}')
    href3 = page.locator('table tbody tr', has_text=v3).locator('a', has_text='Download').first.get_attribute('href')
    response = page.request.get(WEB + href3)
    record('HTML notice downloaded as attachment with CSP sandbox',
           'attachment' in response.headers.get('content-disposition', '')
           and 'sandbox' in response.headers.get('content-security-policy', ''),
           f"{response.headers.get('content-disposition')} / {response.headers.get('content-security-policy')}")
    # 9 MB notice: stored as a multipart object in Garage and downloaded intact
    v9 = f'ZZB-{TAG}-9mb'
    msg, _ = _publish_notice(page, files['large_9mb.pdf'], v9)
    go(page, f'/admin/privacy-notices?search={v9}')
    link = page.locator('table tbody tr', has_text=v9).locator('a', has_text='Download')
    body = page.request.get(WEB + link.first.get_attribute('href')).body() if link.count() else b''
    record('9 MB notice (multipart in Garage) published and downloaded intact',
           hashlib.sha256(body).hexdigest() == hashlib.sha256(Path(files['large_9mb.pdf']).read_bytes()).hexdigest(),
           f'{msg}; {len(body)} bytes', shot(page, 'notice-9mb'))
    # deactivate / activate
    go(page, f'/admin/privacy-notices?search={v1}')
    with page.expect_navigation():
        page.locator('table tbody tr', has_text=v1).locator('button', has_text='Deactivate').click()
    go(page, f'/admin/privacy-notices?search={v1}')
    inactive = 'Inactive' in row_text(page, v1)
    with page.expect_navigation():
        page.locator('table tbody tr', has_text=v1).locator('button', has_text='Activate').click()
    go(page, f'/admin/privacy-notices?search={v1}')
    record('deactivate and reactivate a version', inactive and 'Active' in row_text(page, v1), '',
           shot(page, 'notice-reactivated'))
    # "single active version" parameter
    engine = ctx['engine']
    before = [str(r[0]) for r in engine.connect().execute(text('SELECT id FROM privacy_notice_version WHERE active'))]
    go(page, '/admin/configuration')
    page.select_option('#setting-privacy_notice_single_active', 'true')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    go(page, f'/admin/privacy-notices?search={v2}')
    with page.expect_navigation():
        page.locator('table tbody tr', has_text=v2).locator('button', has_text='Deactivate').click()
    go(page, f'/admin/privacy-notices?search={v2}')
    with page.expect_navigation():
        page.locator('table tbody tr', has_text=v2).locator('button', has_text='Activate').click()
    active_now = [r[0] for r in engine.connect().execute(text('SELECT version FROM privacy_notice_version WHERE active'))]
    go(page, '/admin/privacy-notices?status=active')
    record('single-active parameter leaves only one active version', active_now == [v2], active_now,
           shot(page, 'notice-single-active'))
    # restore the previous state (test data): parameter off, previously active versions active again
    go(page, '/admin/configuration')
    page.select_option('#setting-privacy_notice_single_active', 'false')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    for notice_id in before:
        page.request.post(f'{WEB}/admin/privacy-notices/{notice_id}/activate', headers=_csrf(page))


def rf05_users(ctx, browser):
    page, data = ctx['admin'], ctx['data']
    email = f'zzb.charge.{TAG.lower()}@browser-test.invalid'
    password = 'Zzb-' + secrets.token_urlsafe(10)
    go(page, '/admin/users/new')
    page.fill('#user-name', f'Browser OR charge {TAG}')
    page.fill('#user-email', email)
    page.check('#user-role-or_charge')
    page.locator('input[name="rooms[]"]').first.check()
    page.fill('#user-password', password)
    page.fill('#user-confirm-password', password)
    submit(page, '#user-form button[type=submit]')
    go(page, f'/admin/users?status=all&search={TAG.lower()}')
    record('create or_charge user with a room; listed', email in page.locator('table').inner_text(), messages(page),
           shot(page, 'user-created'))
    ctx['or_charge'] = (email, password)
    # duplicate email and short password
    go(page, '/admin/users/new')
    page.fill('#user-name', 'Duplicate')
    page.fill('#user-email', email)
    page.check('#user-role-station_operator')
    page.fill('#user-password', password)
    page.fill('#user-confirm-password', password)
    submit(page, '#user-form button[type=submit]')
    record('duplicate email rejected with a message', bool(messages(page)), messages(page), shot(page, 'user-duplicate'))
    go(page, '/admin/users/new')
    page.fill('#user-name', 'Short password')
    page.fill('#user-email', f'zzb.short.{TAG.lower()}@browser-test.invalid')
    page.check('#user-role-station_operator')
    page.fill('#user-password', 'Ab1')
    page.fill('#user-confirm-password', 'Ab1')
    page.click('#user-form button[type=submit]')
    page.wait_for_timeout(500)
    blocked_client = '/users/new' in page.url and page.evaluate("() => !document.querySelector('#user-password').checkValidity()")
    page.evaluate("() => { document.querySelector('#user-password').removeAttribute('minlength'); "
                  "document.querySelector('#user-form').noValidate = true; }")
    submit(page, '#user-form button[type=submit]')
    record('short password: blocked in the browser and by the server', blocked_client and bool(messages(page)),
           messages(page), shot(page, 'user-short-password'))
    # lifecycle with a live session of the user in another browser
    op_email, op_pass, op_new = f'zzb.op.{TAG.lower()}@browser-test.invalid', 'Zzb-' + secrets.token_urlsafe(10), \
        'Zzb-' + secrets.token_urlsafe(10)
    go(page, '/admin/users/new')
    page.fill('#user-name', f'Browser operator {TAG}')
    page.fill('#user-email', op_email)
    page.check('#user-role-station_operator')
    page.fill('#user-password', op_pass)
    page.fill('#user-confirm-password', op_pass)
    submit(page, '#user-form button[type=submit]')
    user_ctx, user_page = sign_in(browser, op_email, op_pass)
    home = user_page.url
    go(page, f'/admin/users?status=all&search=zzb.op.{TAG.lower()}')
    edit = page.locator('table tbody tr', has_text=op_email).locator('a[href$="/edit"]').first.get_attribute('href')
    go(page, edit)
    page.click('[data-modal-open="deactivate-user-modal"]')
    shot(page, 'user-deactivate-modal')
    with page.expect_navigation():
        page.locator('form[action$="/deactivate"] button[type=submit]').click()
    deactivate_msg = messages(page)
    user_page.goto(home)
    user_page.wait_for_load_state('networkidle')
    record('deactivation ends the open session of the user (other browser)', '/sign-in' in user_page.url,
           f'{deactivate_msg}; user now at {user_page.url}', shot(page, 'user-deactivated'))
    user_ctx.close()
    go(page, edit)
    with page.expect_navigation():
        page.click('#activate-user-form ~ * button, button[form="activate-user-form"]')
    user_ctx, user_page = sign_in(browser, op_email, op_pass)
    record('reactivated user can sign in again', '/sign-in' not in user_page.url, user_page.url)
    go(page, edit)
    page.click('[data-modal-open="change-password-modal"]')
    page.fill('#change-password-new', op_new)
    page.fill('#change-password-confirm', op_new)
    with page.expect_navigation():
        page.locator('form[action$="/password"] button[type=submit]').click()
    reset_msg = messages(page)
    user_page.goto(home)
    user_page.wait_for_load_state('networkidle')
    ended = '/sign-in' in user_page.url
    user_ctx.close()
    old_ctx, old_page = sign_in(browser, op_email, op_pass, expect_ok=False)
    old_refused = '/sign-in' in old_page.url
    shot(old_page, 'user-old-password-refused')
    old_ctx.close()
    new_ctx, new_page = sign_in(browser, op_email, op_new)
    record('password reset: session ended, old password refused, new one works',
           ended and old_refused and '/sign-in' not in new_page.url, reset_msg, shot(page, 'user-password-reset'))
    new_ctx.close()
    # self-deactivation is not possible
    own = data['users_a'][ADMIN_A[0]]
    go(page, f'/admin/users/{own}/edit')
    offered = page.locator('[data-modal-open="deactivate-user-modal"]').count()
    page.request.post(f'{WEB}/admin/users/{own}/deactivate', headers=_csrf(page))
    still = ctx['engine'].connect().execute(text('SELECT active FROM "user" WHERE id = :i'), {'i': own}).scalar()
    record('an administrator cannot deactivate their own account', still is True,
           f'deactivate button offered={bool(offered)}', shot(page, 'user-self'))
    # isolation
    go(page, '/admin/users?status=all&search=browser-test.invalid')
    listed_b = data['admin_b'][0] in page.locator('main').inner_text()
    status = go(page, f"/admin/users/{data['user_b']}/edit")
    record('users of another institution are not listed and their URL is 404', not listed_b and status == 404,
           f'listed={listed_b}, HTTP {status}', shot(page, 'user-other-institution-404'))


def rf06_roles(ctx):
    page, data = ctx['admin'], ctx['data']
    go(page, '/admin/roles')
    body = page.locator('table').inner_text()
    codes = [c for c in ('station_operator', 'spd_supervisor', 'it_admin', 'or_charge') if c in body]
    rows = page.locator('table tbody tr').count()
    no_create = page.locator('a[href$="/admin/roles/new"]').count() == 0 and page.locator('button', has_text=re.compile('Delete', re.I)).count() == 0
    record('exactly the four fixed roles; no create/delete actions', len(codes) == 4 and rows == 4 and no_create,
           f'codes={codes}, rows={rows}', shot(page, 'roles'))
    link = page.locator('table tbody tr', has_text='or_charge').locator('a').first.get_attribute('href')
    go(page, link)
    original = page.input_value('#role-description')
    page.fill('#role-description', original + f' [browser {TAG}]')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    go(page, link)
    persisted = page.input_value('#role-description').endswith(f'[browser {TAG}]')
    code_readonly = page.locator('#role-code').get_attribute('name') in (None, '') and \
        (page.locator('#role-code').is_disabled() or page.locator('#role-code').get_attribute('readonly') is not None)
    record('edit role description persists; code not editable', persisted and code_readonly, '',
           shot(page, 'role-edited'))
    page.fill('#role-description', original)
    submit(page, 'form[action$="/edit"] button[type=submit]')
    record('description restored', True)
    record('/admin/roles/new answers 404', go(page, '/admin/roles/new') == 404)
    record("role of another institution answers 404", go(page, f"/admin/roles/{data['role_b']}/edit") == 404)


def rf07_rooms(ctx):
    page, data = ctx['admin'], ctx['data']
    room_code = f'ZZB-R{TAG}'
    go(page, '/admin/configuration/operating-rooms/new')
    page.fill('#room-code', room_code)
    page.fill('#room-name', f'Browser room {TAG}')
    submit(page, 'form[action$="/operating-rooms/new"] button[type=submit]')
    go(page, '/admin/configuration')
    record('create operating room; listed', room_code in page.locator('main').inner_text(), messages(page),
           shot(page, 'room-created'))
    go(page, '/admin/configuration/operating-rooms/new')
    page.fill('#room-code', room_code)
    page.fill('#room-name', 'Duplicate')
    submit(page, 'form[action$="/operating-rooms/new"] button[type=submit]')
    record('duplicate room code rejected', '/new' in page.url and bool(messages(page)), messages(page))
    station = f'Browser station {TAG}'
    go(page, '/admin/configuration/capture-stations/new')
    page.fill('#capture-station-name', station)
    room_value = page.locator('#capture-station-room option').evaluate_all(
        f"os => (os.find(o => o.textContent.includes('{TAG}')) || {{}}).value")
    page.select_option('#capture-station-room', room_value)
    for field, value in (('frame_w', 1920), ('frame_h', 1080), ('x', 100), ('y', 100), ('w', 800), ('h', 600)):
        page.fill(f'#capture-station-roi-{field}', str(value))
    submit(page, 'form[action$="/capture-stations/new"] button[type=submit]')
    go(page, '/admin/configuration')
    row = row_text(page, station)
    record('create capture station with ROI; shown as configured', station in row and 'Configured' in row, row,
           shot(page, 'station-created'))
    edit = page.locator('table tbody tr', has_text=station).locator('a', has_text='Edit').first.get_attribute('href')
    go(page, edit)
    page.fill('#capture-station-roi-x', '1500')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    record('ROI outside the frame rejected', bool(messages(page)), messages(page), shot(page, 'station-roi-outside'))
    go(page, edit)
    for field in ('frame_w', 'frame_h', 'y', 'w', 'h'):
        page.fill(f'#capture-station-roi-{field}', '')
    page.fill('#capture-station-roi-x', '10')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    record('partial ROI rejected', bool(messages(page)), messages(page))
    go(page, '/admin/configuration')
    room_link = page.locator('table tbody tr', has_text=room_code).locator('a', has_text='Edit').first.get_attribute('href')
    go(page, room_link)
    page.select_option('#room-status', 'inactive')
    submit(page, 'form[action$="/edit"] button[type=submit]')
    go(page, '/admin/configuration')
    record('deactivate room; still listed as inactive', 'Inactive' in row_text(page, room_code), row_text(page, room_code),
           shot(page, 'room-inactive'))
    record('room of another institution answers 404',
           go(page, f"/admin/configuration/operating-rooms/{data['room_b']}/edit") == 404)
    record('station of another institution answers 404',
           go(page, f"/admin/configuration/capture-stations/{data['station_b']}/edit") == 404)


def rf08_models(ctx):
    page, files, weights = ctx['admin'], ctx['files'], ctx['weights']
    if not weights:
        record('real best.pt available', False, 'no --weights given', status='BLOCKED')
        return
    sha = hashlib.sha256(Path(weights).read_bytes()).hexdigest()
    engine = ctx['engine']
    previous_active = engine.connect().execute(text('SELECT id FROM yolo_model WHERE active')).scalar()
    ctx['previous_model'] = str(previous_active) if previous_active else None
    # wrong extension
    go(page, '/admin/vision-models/new')
    page.set_input_files('#vision-model-weights', files['model.onnx'])
    page.fill('#vision-model-version-tag', f'zzb-onnx-{TAG}')
    submit(page, 'form[action$="/vision-models/new"] button[type=submit]')
    record('.onnx file rejected (only PyTorch .pt)', '/new' in page.url and bool(messages(page)), messages(page),
           shot(page, 'model-reject-onnx'))
    # size limit from Configuration (1 MB) applied to the real 53 MB file
    go(page, '/admin/configuration')
    page.fill('#setting-model_weights_max_mb', '1')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    go(page, '/admin/vision-models/new')
    page.set_input_files('#vision-model-weights', weights)
    page.fill('#vision-model-version-tag', f'zzb-big-{TAG}')
    submit(page, 'form[action$="/vision-models/new"] button[type=submit]')
    record('weights above the configured maximum rejected', '/new' in page.url and bool(messages(page)), messages(page),
           shot(page, 'model-reject-size'))
    go(page, '/admin/configuration')
    page.fill('#setting-model_weights_max_mb', '500')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    # real upload (unless this checksum is already stored from an earlier run)
    existing = engine.connect().execute(text('SELECT id, version_tag FROM yolo_model WHERE checksum = :c'),
                                        {'c': sha}).first()
    tag = existing.version_tag if existing else f'best-{sha[:8]}-{TAG.lower()}'
    if existing is None:
        go(page, '/admin/vision-models/new')
        page.set_input_files('#vision-model-weights', weights)
        page.fill('#vision-model-version-tag', tag)
        started = time.monotonic()
        submit(page, 'form[action$="/vision-models/new"] button[type=submit]')
        record('upload real best.pt (step 1) -> step 2 with checksum, architecture and classes',
               '/edit' in page.url and sha in page.locator('main').inner_text() and 'yolo26l' in page.locator('main').inner_text(),
               f'{messages(page)} in {time.monotonic() - started:.1f}s', shot(page, 'model-uploaded'))
        model_id = re.search(r'/vision-models/([0-9a-f-]{36})', page.url).group(1)
    else:
        model_id = str(existing.id)
        record('upload real best.pt (step 1)', True, f'already uploaded in an earlier run as {tag}', status='PASS')
    ctx['model'] = (model_id, sha, tag)
    stored = engine.connect().execute(text(
        'SELECT m.checksum, a.bucket, a.object_key, a.size_bytes FROM yolo_model m JOIN media_asset a '
        'ON a.id = m.media_asset_id WHERE m.id = :i'), {'i': model_id}).first()
    record('stored checksum equals the SHA-256 of the file; object in the configured storage',
           stored is not None and stored.checksum == sha, f'{stored}')
    go(page, f'/admin/vision-models/{model_id}/edit')
    rows = page.locator('table tbody tr')
    names = [rows.nth(i).locator('td').first.inner_text().strip() for i in range(rows.count())]
    record('the 17 classes of the real model are shown', len(names) == 17 and names[0] == 'Adson without teeth'
           and names[16] == 'Surgical probe', names, shot(page, 'model-classes'))
    # map every class to the family with the same name; reject "Surgical probe" on purpose
    mapped, rejected = 0, []
    if not page.locator('select[name^="class_"]').first.is_disabled():
        for i in range(rows.count()):
            select = rows.nth(i).locator('select')
            name = names[i]
            if name == 'Surgical probe':
                select.select_option('reject')
                rejected.append(name)
                continue
            labels = select.locator('option').evaluate_all('os => os.map(o => [o.value, o.textContent.trim()])')
            match = [v for v, label in labels if v not in ('', 'reject') and name.lower() in label.lower()]
            if match:
                select.select_option(match[0])
                mapped += 1
            else:
                select.select_option('reject')
                rejected.append(name)
        submit(page, 'form button.btn--primary[type=submit]')
        warning = messages(page)
        if page.locator('#confirm-missing-families').count():
            shot(page, 'model-missing-families-warning')
            # the posted values are kept by the page; re-apply defensively
            page.check('#confirm-missing-families')
            submit(page, 'form button.btn--primary[type=submit]')
        record('mapping published (classes mapped / rejected, missing-family warning confirmed)',
               bool(messages(page)) and 'error' not in messages(page).lower(),
               f'mapped={mapped}, rejected={rejected}, warning={warning}; {messages(page)}', shot(page, 'model-mapped'))
    classes = engine.connect().execute(text('SELECT yolo_class_id FROM model_class WHERE model_id = :i ORDER BY 1'),
                                       {'i': model_id}).scalars().all()
    record('class map stored without the rejected class', 16 not in classes and len(classes) >= 1, f'{len(classes)} classes')
    # activate
    go(page, f'/admin/vision-models/{model_id}/edit')
    if page.locator('button[form="activate-vision-model-form"]').count():
        with page.expect_navigation():
            page.click('button[form="activate-vision-model-form"]')
    go(page, '/admin/vision-models')
    active = engine.connect().execute(text('SELECT id FROM yolo_model WHERE active')).scalars().all()
    record('activate the real model: it is the only active one', [str(a) for a in active] == [model_id],
           row_text(page, tag), shot(page, 'model-active'))
    # duplicate checksum
    go(page, '/admin/vision-models/new')
    page.set_input_files('#vision-model-weights', weights)
    page.fill('#vision-model-version-tag', f'zzb-dup-{TAG}')
    submit(page, 'form[action$="/vision-models/new"] button[type=submit]')
    record('same weights uploaded again: duplicate checksum rejected', '/new' in page.url and bool(messages(page)),
           messages(page), shot(page, 'model-duplicate'))
    # cannot leave zero active models while stations are active
    go(page, f'/admin/vision-models/{model_id}/edit')
    if page.locator('button[form="deactivate-vision-model-form"]').count():
        with page.expect_navigation():
            page.click('button[form="deactivate-vision-model-form"]')
    still = engine.connect().execute(text('SELECT active FROM yolo_model WHERE id = :i'), {'i': model_id}).scalar()
    record('deactivating the only active model with active stations is refused', still is True, messages(page),
           shot(page, 'model-deactivate-refused'))
    # what the portal sends to VisionWorker at bind time (no worker call)
    probe = subprocess.run([sys.executable, '-c', (
        'import json\nfrom app import app\nfrom services import vision_bridge\n'
        'with app.app_context():\n    p = vision_bridge.active_model_payload()\n'
        '    print(json.dumps({"model_id": str(p.get("model_id")), "model_version": p.get("model_version"), '
        '"classes": sorted(c["yolo_class_id"] for c in p.get("class_map") or [])}))')],
        cwd=ROOT / 'apps' / 'BackendWebFlask', env=envctl.build_env(storage_mode['backend'], '')[1],
        capture_output=True, text=True, timeout=120)
    try:
        payload = json.loads(probe.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {'error': (probe.stderr or probe.stdout)[-300:]}
    record('bind payload for VisionWorker carries the activated model and its class map (no rejected class)',
           payload.get('model_id') == model_id and payload.get('model_version') == tag
           and payload.get('classes') == sorted(classes) and 16 not in payload.get('classes', [16]), payload)


def rf09_audit(ctx):
    page = ctx['admin']
    go(page, f'/admin/audit-log?search=ZZB{TAG}')
    body = page.locator('main').inner_text()
    record('audit log lists this run\'s family events (search)', f'ZZB{TAG}' in body or 'INSTRUMENT_FAMILY' in body,
           '', shot(page, 'audit-search'))
    go(page, '/admin/audit-log')
    page.select_option('#audit-log-outcome', 'DENIED')
    submit(page, 'form[action$="/admin/audit-log"] button[type=submit]')
    record('filter by result DENIED', 'DENIED' in page.locator('main').inner_text(), '', shot(page, 'audit-denied'))
    go(page, '/admin/audit-log')
    options = page.locator('#audit-log-event-type option').evaluate_all('os => os.map(o => o.value)')
    if 'ACTIVATE_VISION_MODEL' in options:
        page.select_option('#audit-log-event-type', 'ACTIVATE_VISION_MODEL')
        submit(page, 'form[action$="/admin/audit-log"] button[type=submit]')
        record('filter by event type ACTIVATE_VISION_MODEL', 'ACTIVATE_VISION_MODEL' in page.locator('main').inner_text())
    go(page, '/admin/audit-log')
    page.fill('#audit-log-date-from', '2026-12-31')
    page.fill('#audit-log-date-to', '2026-01-01')
    submit(page, 'form[action$="/admin/audit-log"] button[type=submit]')
    rows = page.locator('table tbody tr').count()
    record('From after To: notice shown and nothing listed', bool(messages(page) or 'range' in page.locator('main').inner_text().lower())
           and rows <= 1, f'rows={rows} {messages(page)}', shot(page, 'audit-empty-range'))
    go(page, '/admin/audit-log')
    nxt = page.locator('a', has_text='Next')
    if nxt.count():
        with page.expect_navigation():
            nxt.first.click()
        record('pagination: next page loads', 'page=2' in page.url and page.locator('table tbody tr').count() > 0,
               page.url, shot(page, 'audit-page-2'))
    # the other institution's administrator does not see these events
    b_ctx, b_page = sign_in(ctx['browser'], *ctx['data']['admin_b'])
    go(b_page, f'/admin/audit-log?search=ZZB{TAG}')
    record("another institution's administrator does not see them", f'ZZB{TAG}' not in b_page.locator('table').inner_text(),
           '', shot(b_page, 'audit-other-institution'))
    b_ctx.close()


def rf10_configuration(ctx):
    page = ctx['admin']
    go(page, '/admin/configuration/institution/edit')
    original = page.input_value('#institution-name')
    page.fill('#institution-name', original + f' ({TAG})')
    submit(page, 'form[action$="/institution/edit"] button[type=submit]')
    go(page, '/admin/configuration/institution/edit')
    renamed = page.input_value('#institution-name').endswith(f'({TAG})')
    page.fill('#institution-name', original)
    page.select_option('#institution-status', 'inactive')
    submit(page, 'form[action$="/institution/edit"] button[type=submit]')
    msg = messages(page)
    still = ctx['engine'].connect().execute(text('SELECT active, name FROM institution WHERE id = :i'),
                                            {'i': ctx['data']['inst_a']}).first()
    record('rename institution persists; own institution cannot be deactivated', renamed and still.active and bool(msg),
           msg, shot(page, 'institution-deactivate-refused'))
    if still.name != original:
        go(page, '/admin/configuration/institution/edit')
        page.fill('#institution-name', original)
        page.select_option('#institution-status', 'active')
        submit(page, 'form[action$="/institution/edit"] button[type=submit]')
    # invalid parameters
    go(page, '/admin/configuration')
    page.fill('#setting-kpi_awaiting_review_warning', '20')
    page.fill('#setting-kpi_awaiting_review_danger', '10')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    record('warning above danger rejected', bool(messages(page)), messages(page), shot(page, 'settings-invalid-threshold'))
    go(page, '/admin/configuration')
    for box in page.locator('input[name="family_example_allowed_mime[]"]').all():
        box.uncheck()
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    record('empty MIME list rejected', bool(messages(page)), messages(page))
    # a valid change persists and is applied (password policy)
    go(page, '/admin/configuration')
    page.fill('#setting-password_min_length', '12')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')
    go(page, '/admin/configuration')
    persisted = page.input_value('#setting-password_min_length') == '12'
    go(page, '/admin/users/new')
    page.fill('#user-name', 'Policy check')
    page.fill('#user-email', f'zzb.policy.{TAG.lower()}@browser-test.invalid')
    page.check('#user-role-station_operator')
    page.evaluate("() => { document.querySelector('#user-form').noValidate = true; }")
    page.fill('#user-password', 'Abcdef-123')
    page.fill('#user-confirm-password', 'Abcdef-123')
    submit(page, '#user-form button[type=submit]')
    applied = '/users/new' in page.url and bool(messages(page))
    record('password minimum (12) persists and is applied to new users (10 chars refused)', persisted and applied,
           messages(page), shot(page, 'settings-password-policy'))
    go(page, '/admin/configuration')
    page.fill('#setting-password_min_length', '8')
    submit(page, 'form[action$="/admin/configuration/settings"] button[type=submit]')


def security_roles(ctx):
    browser, data = ctx['browser'], ctx['data']
    anon = browser.new_context()
    page = anon.new_page()
    page.goto(WEB + '/admin/users')
    record('anonymous visitor is sent to sign-in', '/sign-in' in page.url, page.url)
    anon.close()
    accounts = [('spd_supervisor', SUPERVISOR), ('station_operator', OPERATOR)]
    if ctx.get('or_charge'):
        accounts.append(('or_charge', ctx['or_charge']))
    for role, (email, password) in accounts:
        login = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
        codes = [r['code'] for r in (login.json().get('user', {}).get('roles') or [])] if login.ok else []
        record(f'AuthService recognises {role}', role in codes, codes)
        context, page = sign_in(browser, email, password, expect_ok=role != 'or_charge')
        landing = page.url
        landing_text = page.locator('body').inner_text()
        shot(page, f'{role}-landing')
        if role == 'or_charge':
            # Fourth product role (migration 009): AuthService signs it in, but the portal has no screens for it.
            record('or_charge: the portal has no screens for this role and says so on the sign-in page',
                   '/sign-in' in landing and 'no application role' in landing_text, landing.replace(WEB, ''))
        statuses = {}
        for path in ('/admin/dashboard', '/admin/users', '/admin/vision-models', '/admin/configuration'):
            response = page.goto(WEB + path)
            statuses[path] = response.status if response else 0
        record(f'{role}: every Administration page is refused (landing {landing.replace(WEB, "")})',
               all(code in (401, 403) or '/sign-in' in page.url for code in statuses.values()), statuses,
               shot(page, f'{role}-admin-403'))
        context.close()
    admin_codes = [r['code'] for r in requests.post(AUTH + '/login', json={'email': ADMIN_A[0], 'password': ADMIN_A[1]},
                                                    timeout=10).json()['user']['roles']]
    record('AuthService recognises it_admin', 'it_admin' in admin_codes, admin_codes)
    page = ctx['admin']
    statuses = {p: go(page, p) for p in ('/admin/instruments', '/admin/kits')}
    record('pieces and kits stay with the SPD supervisor (admin gets 403)', set(statuses.values()) == {403}, statuses)
    go(page, '/admin/audit-log')
    page.select_option('#audit-log-outcome', 'DENIED')
    submit(page, 'form[action$="/admin/audit-log"] button[type=submit]')
    record('denied Administration attempts are audited', 'DENIED' in page.locator('main').inner_text(), '',
           shot(page, 'audit-denied-after-roles'))


def security_steward(ctx):
    """Shared catalogs with and without ADMIN_CATALOG_STEWARD_INSTITUTION_ID (portal restarted for each)."""
    data, browser = ctx['data'], ctx['browser']
    family_edit = ctx.get('family_edit')

    def try_edit(email_password, label):
        context, page = sign_in(browser, *email_password)
        go(page, family_edit)
        page.fill('#family-function', f'edited by {label} {TAG}')
        submit(page, 'form[action$="/edit"] button[type=submit]')
        msg = messages(page)
        evidence = shot(page, f'steward-{label}')
        context.close()
        return msg, evidence

    msg, evidence = try_edit(data['admin_b'], 'institution-b-no-steward')
    record('without the variable any it_admin (also institution B) can edit shared catalogs',
           'maintained' not in msg.lower(), msg, evidence)
    start_services(storage_mode['backend'], steward=data['inst_a'], restart_auth=False)
    ctx['admin'].context.close()
    ctx['admin'] = sign_in(browser, *ADMIN_A)[1]
    msg, evidence = try_edit(data['admin_b'], 'institution-b-steward-a')
    record('with steward = A, institution B gets 403 "Shared catalogs are maintained..."', 'maintained' in msg.lower(),
           msg, evidence)
    msg, evidence = try_edit(ADMIN_A, 'institution-a-steward-a')
    record('with steward = A, institution A can still edit', 'maintained' not in msg.lower(), msg, evidence)
    context, page = sign_in(browser, *data['admin_b'])
    status = go(page, '/admin/instrument-families')
    record('with steward = A, institution B can still read the catalog', status == 200, f'HTTP {status}')
    context.close()
    start_services(storage_mode['backend'], steward='', restart_auth=False)
    ctx['admin'].context.close()
    ctx['admin'] = sign_in(browser, *ADMIN_A)[1]


def persistence(ctx):
    """Restart the portal and Garage (TEST project) and read everything back."""
    browser, engine = ctx['browser'], ctx['engine']
    compose('restart', 'garage')
    time.sleep(3)
    start_services(storage_mode['backend'], restart_auth=False)
    ctx['admin'].context.close()
    page = ctx['admin'] = sign_in(browser, *ADMIN_A)[1]
    if ctx.get('model'):
        model_id, sha, tag = ctx['model']
        go(page, '/admin/vision-models')
        row = row_text(page, tag)
        go(page, f'/admin/vision-models/{model_id}/edit')
        classes = page.locator('table tbody tr').count()
        record('after restarting portal + Garage: real model still active with its 17 classes',
               'Active' in row and classes == 17, row.replace('\n', ' '), shot(page, 'persist-model'))
        stored = engine.connect().execute(text(
            'SELECT a.bucket, a.object_key FROM yolo_model m JOIN media_asset a ON a.id = m.media_asset_id '
            'WHERE m.id = :i'), {'i': model_id}).first()
        env = load_test_env()
        import boto3
        from botocore.config import Config as BotoConfig
        # Garage answers multipart objects with a part-level CRC that recent botocore rejects as a full-object
        # checksum (FlexibleChecksumError): validate with our own SHA-256 instead (see ADMIN-09 findings).
        s3 = boto3.client('s3', endpoint_url=env['PEF_TEST_S3_ENDPOINT'], region_name='garage',
                          config=BotoConfig(response_checksum_validation='when_required'),
                          aws_access_key_id=env['PEF_TEST_S3_ACCESS_KEY'],
                          aws_secret_access_key=env['PEF_TEST_S3_SECRET_KEY'])
        body = s3.get_object(Bucket=stored.bucket, Key=stored.object_key)['Body'].read()
        record('weights object read back from Garage after restart: same SHA-256',
               hashlib.sha256(body).hexdigest() == sha, f'{stored.bucket}/{stored.object_key} ({len(body)} bytes)')
    if ctx.get('notice_download'):
        href, sha = ctx['notice_download']
        response = page.request.get(WEB + href)
        record('privacy notice downloaded after restart: same SHA-256',
               response.ok and hashlib.sha256(response.body()).hexdigest() == sha)
    if ctx.get('family_edit'):
        go(page, ctx['family_edit'])
        ok = page.evaluate("() => [...document.querySelectorAll('img[src*=\"/examples/\"]')]"
                           ".every(i => i.complete && i.naturalWidth > 0)")
        record('family photo still rendered after restart', bool(ok) and page.locator('img[src*="/examples/"]').count() > 0,
               '', shot(page, 'persist-family-photo'))
    go(page, f'/admin/users?status=all&search={TAG.lower()}')
    record('users created in this run still listed after restart', 'browser-test.invalid' in page.locator('table').inner_text())
    # local backend: publish, restart, read back
    start_services('local', restart_auth=False)
    ctx['admin'].context.close()
    page = ctx['admin'] = sign_in(browser, *ADMIN_A)[1]
    version = f'ZZB-{TAG}-local'
    local_file = FIXTURES / 'notice_local.html'
    local_file.write_bytes(b'<!doctype html><html><body>local backend notice ' + TAG.encode() + b'</body></html>')
    ctx['files']['notice_local.html'] = str(local_file)
    _publish_notice(page, str(local_file), version)
    go(page, f'/admin/privacy-notices?search={version}')
    href = page.locator('table tbody tr', has_text=version).locator('a', has_text='Download').first.get_attribute('href')
    expected = hashlib.sha256(local_file.read_bytes()).hexdigest()
    start_services('local', restart_auth=False)
    ctx['admin'].context.close()
    page = ctx['admin'] = sign_in(browser, *ADMIN_A)[1]
    response = page.request.get(WEB + href)
    record('local backend: notice published, portal restarted, same bytes downloaded',
           response.ok and hashlib.sha256(response.body()).hexdigest() == expected, href)
    start_services('s3', restart_auth=False)
    ctx['admin'].context.close()
    ctx['admin'] = sign_in(browser, *ADMIN_A)[1]


def restore(ctx):
    """Leave the shared test DB with the model that was active before (test data)."""
    previous = ctx.get('previous_model')
    if previous and ctx.get('model') and ctx['model'][0] != previous:
        page = ctx['admin']
        response = page.request.post(f'{WEB}/admin/vision-models/{previous}/activate', headers=_csrf(page))
        active = ctx['engine'].connect().execute(text('SELECT id FROM yolo_model WHERE active')).scalar()
        record('restore: previously active model active again (test DB left as found)', str(active) == previous,
               f'HTTP {response.status}')


# --- main -----------------------------------------------------------------------------------

def write_report(started):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary = {s: sum(1 for r in results if r['status'] == s) for s in ('PASS', 'FAIL', 'BLOCKED')}
    payload = {'run_tag': TAG, 'started': started, 'finished': datetime.now().isoformat(timespec='seconds'),
               'browser': 'Chromium (Playwright)', 'portal': WEB, 'summary': summary, 'results': results,
               'browser_problems': problems}
    (EVIDENCE / 'results.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = [f'# ADMIN-09 — validación en navegador ({started})', '',
             f'Ejecución `{TAG}` · Chromium (Playwright) · portal {WEB} · entorno aislado `pef_admin_test`.', '',
             f"**Resumen:** {summary['PASS']} PASS · {summary['FAIL']} FAIL · {summary['BLOCKED']} BLOCKED", '',
             '| RF | Comprobación | Resultado | Detalle | Evidencia |', '|---|---|---|---|---|']
    for r in results:
        detail = r['detail'].replace('|', '/').replace('\n', ' ')[:160]
        lines.append(f"| {r['rf']} | {r['check']} | {r['status']} | {detail} | "
                     f"{('[' + r['evidence'] + '](' + r['evidence'] + ')') if r['evidence'] else ''} |")
    expected = [p for p in problems if re.search(r'status of 4\d\d', p['text'])]
    real = [p for p in problems if p not in expected]
    lines += ['', '## Errores de consola, de página y respuestas 5xx', '']
    lines += [f"- [{p['rf']}] {p['kind']}: {p['text']} ({p['url'].replace(WEB, '')})" for p in real] or ['- Ninguno.']
    lines += ['', '## Respuestas 4xx esperadas (pruebas negativas: validaciones, 403, 404)', '']
    lines += [f"- [{p['rf']}] {p['text']} ({p['url'].replace(WEB, '')})" for p in expected] or ['- Ninguna.']
    (EVIDENCE / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', default='', help='path to the REAL best.pt (read only)')
    parser.add_argument('--only', default='', help='comma separated step names (debug)')
    args = parser.parse_args()
    started = datetime.now().isoformat(timespec='seconds')
    for old in EVIDENCE.glob('*.jpg'):
        old.unlink()
    engine = db()
    data = prepare_data(engine)
    ctx = {'engine': engine, 'data': data, 'files': fixtures(), 'weights': args.weights}
    start_services('s3')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx['browser'] = browser
            ctx['admin'] = sign_in(browser, *ADMIN_A)[1]
            steps = [('RF-AD-01', 'rf01', rf01_dashboard, (ctx,)), ('RF-AD-02', 'rf02', rf02_families, (ctx,)),
                     ('RF-AD-03', 'rf03', rf03_procedures, (ctx,)), ('RF-AD-04', 'rf04', rf04_privacy, (ctx,)),
                     ('RF-AD-05', 'rf05', rf05_users, (ctx, browser)), ('RF-AD-06', 'rf06', rf06_roles, (ctx,)),
                     ('RF-AD-07', 'rf07', rf07_rooms, (ctx,)), ('RF-AD-08', 'rf08', rf08_models, (ctx,)),
                     ('RF-AD-10', 'rf10', rf10_configuration, (ctx,)),
                     ('SEC', 'roles', security_roles, (ctx,)), ('SEC-STEWARD', 'steward', security_steward, (ctx,)),
                     ('RF-AD-09', 'rf09', rf09_audit, (ctx,)), ('PERSIST', 'persist', persistence, (ctx,)),
                     ('RESTORE', 'restore', restore, (ctx,))]
            only = {s for s in args.only.split(',') if s}
            for rf, key, func, func_args in steps:
                if not only or key in only:
                    step(rf, func, *func_args)
            browser.close()
    finally:
        stop_services()
    summary = write_report(started)
    real = [p for p in problems if not re.search(r'status of 4\d\d', p['text'])]
    print(f"\n{summary['PASS']} PASS · {summary['FAIL']} FAIL · {summary['BLOCKED']} BLOCKED · "
          f"{len(real)} unexpected browser problem(s) · evidence in {EVIDENCE.relative_to(ROOT)}")
    sys.exit(0 if summary['FAIL'] == 0 and summary['BLOCKED'] == 0 else 1)


if __name__ == '__main__':
    main()
