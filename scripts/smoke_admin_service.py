import os
import sys
import uuid

import requests


AUTH = os.getenv('AUTH_SERVICE_URL', 'http://127.0.0.1:5001').rstrip('/')
ADMIN = os.getenv('ADMIN_SERVICE_URL', 'http://127.0.0.1:5003').rstrip('/') + '/api/v1'
SUFFIX = uuid.uuid4().hex[:6].upper()
FAILURES = []


def login(email, password):
    response = requests.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    return response.cookies['access_token']


def call(token, method, path, expected, **kwargs):
    response = requests.request(method, f'{ADMIN}{path}', headers={'Authorization': f'Bearer {token}'},
                                timeout=10, **kwargs)
    status = 'ok ' if response.status_code == expected else 'FAIL'
    print(f'{status} {method:6} {path:45} -> {response.status_code}')
    if response.status_code != expected:
        FAILURES.append(f'{method} {path}: expected {expected}, got {response.status_code} {response.text[:200]}')
    try:
        return response.json()
    except ValueError:
        return {}


def main():
    admin = login('admin@instrumed.com', 'DemopwdADM78!')
    spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
    operator = login('operator@instrumed.com', 'DemopwdOP78!')

    catalogs = call(admin, 'GET', '/catalogs?names=categories,cycle_statuses,phases,institutions', 200)
    category = catalogs['categories'][0]['id']

    family = call(admin, 'POST', '/families', 201, json={
        'code': f'SMK_{SUFFIX}', 'name': f'Smoke clamp {SUFFIX}', 'category_id': category,
        'function_text': 'Smoke test family', 'status': 'active'})
    call(admin, 'POST', '/families', 409, json={'code': f'SMK_{SUFFIX}', 'name': 'dup', 'category_id': category})
    call(admin, 'POST', '/families', 400, json={'code': '', 'name': 'x', 'category_id': category})
    call(admin, 'PUT', f"/families/{family['id']}", 200, json={
        'name': f'Smoke clamp {SUFFIX} (edited)', 'category_id': category, 'status': 'active'})

    instrument = call(admin, 'POST', '/instruments', 201, json={
        'internal_code': f'SMK-{SUFFIX}-01', 'family_id': family['id'], 'cycle_status': 'available',
        'status': 'active'})
    call(admin, 'PUT', f"/instruments/{instrument['id']}", 400, json={
        'family_id': family['id'], 'cycle_status': 'maintenance', 'status': 'active'})
    call(admin, 'PUT', f"/instruments/{instrument['id']}", 200, json={
        'family_id': family['id'], 'cycle_status': 'maintenance', 'status': 'active',
        'change_reason': 'Hinge repair'})

    kit = call(admin, 'POST', '/kits', 201, json={
        'name': f'Smoke kit {SUFFIX}', 'status': 'active', 'items': [{'family_id': family['id'], 'quantity': 6}]})
    call(admin, 'POST', '/kits', 400, json={'name': f'Empty kit {SUFFIX}', 'items': []})
    kit_v2 = call(admin, 'PUT', f"/kits/{kit['id']}", 200, json={
        'name': f'Smoke kit {SUFFIX}', 'status': 'active', 'items': [{'family_id': family['id'], 'quantity': 8}]})
    assert kit_v2['version'] == kit['version'] + 1, 'composition change must create a new kit version'
    old_kit = call(admin, 'GET', f"/kits/{kit['id']}", 200)
    assert old_kit['active'] is False and old_kit['items'][0]['quantity'] == 6, 'old version must stay intact'

    phase = catalogs['phases'][0]['id']
    procedure = call(admin, 'POST', '/procedures', 201, json={
        'code': f'smk_{SUFFIX.lower()}', 'name': f'Smoke procedure {SUFFIX}',
        'kits': [{'kit_id': kit_v2['id'], 'technique_label': 'Open', 'is_default': True, 'active': True}],
        'phases': [{'phase_id': phase, 'sort_order': 1, 'is_count_required': True, 'active': True}]})
    call(admin, 'PUT', f"/procedures/{procedure['id']}", 200, json={
        'code': procedure['code'], 'name': f'Smoke procedure {SUFFIX} (edited)',
        'kits': [{'kit_id': kit_v2['id'], 'technique_label': 'Laparoscopic', 'is_default': True, 'active': True}],
        'phases': []})

    institution = catalogs['institutions'][0]['id']
    user = call(admin, 'POST', '/users', 201, json={
        'name': f'Smoke Operator {SUFFIX}', 'email': f'smoke.{SUFFIX.lower()}@instrumed.com',
        'institution_id': institution, 'role_codes': ['station_operator'], 'status': 'active',
        'password': 'SmokePass123!', 'confirm_password': 'SmokePass123!'})
    call(admin, 'POST', f"/users/{user['id']}/password", 200, json={
        'new_password': 'SmokePass456!', 'confirm_new_password': 'SmokePass456!'})
    login(user['email'], 'SmokePass456!')
    call(admin, 'POST', f"/users/{user['id']}/deactivate", 200)
    me = call(admin, 'GET', '/me', 200)
    call(admin, 'POST', f"/users/{me['id']}/deactivate", 400)

    role = call(admin, 'POST', '/roles', 201, json={'code': f'smoke_{SUFFIX.lower()}', 'description': 'Smoke role'})
    call(admin, 'PUT', f"/roles/{role['id']}", 200, json={'description': 'Smoke role (edited)'})
    call(admin, 'DELETE', f"/roles/{role['id']}", 200)

    model = call(admin, 'POST', '/vision-models', 201, json={
        'version_tag': f'smk-{SUFFIX.lower()}', 'asset_reference': f'weights/smk-{SUFFIX.lower()}.pt',
        'checksum': 'b' * 64, 'classes': [{'yolo_class_id': 0, 'family_id': family['id']}]})
    call(admin, 'PUT', f"/vision-models/{model['id']}", 200, json={
        'asset_reference': model['asset_reference'], 'checksum': 'c' * 64,
        'classes': [{'yolo_class_id': 1, 'family_id': family['id']}]})

    room = call(admin, 'POST', '/operating-rooms', 201, json={'code': f'OR-{SUFFIX}', 'name': 'Smoke room'})
    station = call(admin, 'POST', '/capture-stations', 201, json={'name': f'Smoke station {SUFFIX}',
                                                                  'room_id': room['id']})
    call(admin, 'PUT', f"/capture-stations/{station['id']}", 200, json={
        'name': f'Smoke station {SUFFIX}', 'room_id': room['id'], 'status': 'inactive'})

    # Permissions: SPD manages instruments/kits only; operators get nothing.
    call(spd, 'GET', '/instruments', 200)
    call(spd, 'GET', '/users', 403)
    call(operator, 'GET', '/families', 403)
    call('not-a-token', 'GET', '/families', 401)

    events = call(admin, 'GET', f"/audit-events?per_page=50&user={me['id']}", 200)
    actions = {item['action'] for item in events['items']}
    for expected in ('CREATE_INSTRUMENT_FAMILY', 'UPDATE_INSTRUMENT_FAMILY', 'CREATE_INSTRUMENT',
                     'UPDATE_INSTRUMENT', 'CREATE_KIT', 'UPDATE_KIT', 'CREATE_PROCEDURE', 'CREATE_USER',
                     'RESET_USER_PASSWORD', 'DEACTIVATE_USER', 'CREATE_ROLE', 'DELETE_ROLE',
                     'CREATE_VISION_MODEL', 'UPDATE_VISION_MODEL', 'CREATE_OPERATING_ROOM',
                     'CREATE_CAPTURE_STATION', 'DEACTIVATE_CAPTURE_STATION'):
        if expected not in actions:
            FAILURES.append(f'audit action missing: {expected}')

    print()
    if FAILURES:
        print('FAILURES:')
        for failure in FAILURES:
            print(' -', failure)
        sys.exit(1)
    print('All admin smoke checks passed.')


if __name__ == '__main__':
    main()
