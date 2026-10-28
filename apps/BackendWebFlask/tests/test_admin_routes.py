from pathlib import Path

import pytest

from services.admin_local.messages import AdminDomainError


TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'
USER_ID = '44444444-4444-4444-4444-444444444444'
FAMILY_ID = '41414141-0000-4000-8000-000000000001'


def html(response):
    return response.get_data(as_text=True)


def test_admin_templates_have_no_demo_placeholders():
    offenders = []
    for path in list((TEMPLATES / 'admin').rglob('*.html')) + list((TEMPLATES / 'components' / 'modals').glob('*.html')):
        text = path.read_text(encoding='utf-8')
        if path.parent.name == 'modals' and path.name not in {
                'change_password.html', 'deactivate_user.html', 'activate_vision_model.html'}:
            continue
        for marker in ('TEMPORARY DEMO DATA', 'not implemented', 'frontend simulation'):
            if marker in text:
                offenders.append(f'{path.relative_to(TEMPLATES)}: {marker}')
    assert offenders == []


def test_dashboard_shows_service_values(client, login, fake_admin):
    fake_admin.responses[('GET', '/dashboard')] = {
        'stats': {'active_users': 7, 'active_families': 12, 'registered_instruments': 140, 'active_kits': 3,
                  'procedures': 4, 'capture_stations': 2, 'vision_models': 2, 'active_vision_model': 1,
                  'inactive_users': 1, 'roles': 3, 'operating_rooms': 2, 'active_institutions': 1},
        'active_model': {'id': 'm', 'version_tag': 'yolo-0.4.2', 'class_count': 11},
        'operations': {'total_sessions': 9, 'total_discrepancies': 4, 'open_discrepancies': 1,
                       'human_corrections': 2, 'ai_human_agreement': None, 'avg_resolution_minutes': 12.5},
    }
    login(role='it_admin')
    page = html(client.get('/admin/dashboard'))
    assert 'yolo-0.4.2' in page and '140' in page and '12.5 min' in page
    assert 'No data yet' in page  # agreement without data is never invented
    assert '94.2%' not in page


def test_family_list_forwards_filters(client, login, fake_admin):
    fake_admin.responses[('GET', '/families')] = {
        'items': [{'id': FAMILY_ID, 'code': 'KELLY', 'name': 'Kelly forceps', 'active': True,
                   'function_text': 'Clamp vessels', 'category': {'id': 'c', 'code': 'hemostasis',
                                                                  'name': 'Hemostasis', 'rank': 3}}],
        'page': 1, 'per_page': 20, 'total': 1, 'total_pages': 1, 'shown': 1,
        'filters': {'search': 'kel', 'category': 'hemostasis', 'status': 'all'},
    }
    login(role='it_admin')
    page = html(client.get('/admin/instrument-families?search=kel&category=hemostasis&status=all'))
    _, _, params, _ = next(call for call in fake_admin.calls if call[1] == '/families')
    assert params['search'] == 'kel' and params['category'] == 'hemostasis' and params['status'] == 'all'
    assert 'KELLY' in page and f'/admin/instrument-families/{FAMILY_ID}/edit' in page


def test_kit_form_is_sent_as_composition(client, login, fake_admin):
    login(role='it_admin')
    response = client.post('/admin/kits/new', data={
        'name': 'General kit', 'status': 'active',
        'instrument_family[]': [FAMILY_ID, '41414141-0000-4000-8000-000000000002'],
        'expected_quantity[]': ['6', '8'],
    })
    assert response.status_code == 302 and response.headers['Location'].endswith('/admin/kits')
    method, path, _, payload = fake_admin.calls[-1]
    assert (method, path) == ('POST', '/kits')
    assert payload['items'] == [
        {'family_id': FAMILY_ID, 'quantity': '6'},
        {'family_id': '41414141-0000-4000-8000-000000000002', 'quantity': '8'},
    ]


def test_procedure_rows_keep_their_checkbox_state(client, login, fake_admin):
    login(role='it_admin')
    client.post('/admin/procedures/new', data={
        'code': 'appendectomy', 'name': 'Appendectomy',
        'kit[]': ['k1', 'k2'], 'kit_row_index[]': ['0', '1'], 'technique_label[]': ['Open', 'Lap'],
        'is_default[]': ['1'], 'kit_active[]': ['0', '1'],
        'phase[]': ['p1'], 'phase_row_index[]': ['0'], 'sort_order[]': ['1'], 'count_required[]': ['0'],
        'phase_active[]': ['0'],
    })
    _, path, _, payload = fake_admin.calls[-1]
    assert path == '/procedures'
    assert payload['kits'][0] == {'kit_id': 'k1', 'technique_label': 'Open', 'is_default': False, 'active': True}
    assert payload['kits'][1]['is_default'] is True
    assert payload['phases'] == [{'phase_id': 'p1', 'sort_order': '1', 'is_count_required': True, 'active': True}]


def test_validation_error_is_shown_translated(client, login, fake_admin):
    fake_admin.responses[('POST', '/families')] = AdminDomainError(
        409, 'family_code_exists', 'That family code already exists.', 'code')
    login(role='it_admin', locale='es-MX')
    response = client.post('/admin/instrument-families/new', data={'code': 'KELLY', 'name': 'Kelly', 'category': 'c'})
    assert response.status_code == 409
    page = html(response)
    assert 'role="alert"' in page and 'KELLY' in page  # submitted values are kept
    assert 'That family code already exists.' not in page  # shown in Spanish


def test_expired_session_redirects_to_sign_in(client, login, fake_admin):
    fake_admin.responses[('GET', '/users')] = AdminDomainError(401, 'token_revoked')
    login(role='it_admin')
    response = client.get('/admin/users')
    assert response.status_code == 302 and '/sign-in' in response.headers['Location']


def test_database_outage_keeps_page_and_explains(client, login, fake_admin):
    fake_admin.responses[('GET', '/roles')] = AdminDomainError(503, 'database_unavailable')
    login(role='it_admin')
    response = client.get('/admin/roles')
    assert response.status_code == 200
    assert 'database is not available' in html(response)


@pytest.mark.parametrize('path, status', [('/admin/kits', 200), ('/admin/instruments', 200),
                                          ('/admin/users', 403), ('/admin/audit-log', 403)])
def test_spd_supervisor_only_reaches_catalogs(client, login, path, status):
    login(role='spd_supervisor')
    assert client.get(path).status_code == status


def test_password_reset_and_deactivation_are_posts(client, login, fake_admin):
    login(role='it_admin')
    client.post(f'/admin/users/{USER_ID}/password',
                data={'new_password': 'Secret123', 'confirm_new_password': 'Secret123'})
    client.post(f'/admin/users/{USER_ID}/deactivate')
    paths = [(call[0], call[1]) for call in fake_admin.calls]
    assert ('POST', f'/users/{USER_ID}/password') in paths
    assert ('POST', f'/users/{USER_ID}/deactivate') in paths
    assert client.get(f'/admin/users/{USER_ID}/deactivate').status_code == 405


def test_audit_log_renders_service_events(client, login, fake_admin):
    fake_admin.responses[('GET', '/audit-events')] = {
        'items': [{'occurred_at': '2026-10-20 09:15:00', 'user': {'id': 'u', 'name': 'Admin Demo',
                                                                  'email': 'admin@x.org'},
                   'role_codes': ['it_admin'], 'action': 'CREATE_KIT', 'entity': 'kit',
                   'record_label': 'General kit v1', 'outcome': 'success', 'ip': '10.0.0.5'}],
        'page': 1, 'per_page': 50, 'total': 1, 'total_pages': 1, 'shown': 1,
        'options': {'users': [], 'actions': ['CREATE_KIT'], 'entities': ['kit']},
    }
    login(role='it_admin')
    page = html(client.get('/admin/audit-log?action=CREATE_KIT'))
    assert 'CREATE_KIT' in page and 'General kit v1' in page and '10.0.0.5' in page


def test_spanish_search_sends_localized_matches(client, login, fake_admin):
    login(role='it_admin', locale='es-MX')
    client.get('/admin/procedures?search=Colecistectomia')
    client.get('/admin/instrument-families?search=pinza')
    calls = {call[1]: call[2] for call in fake_admin.calls}
    assert {'lap_chole', 'open_chole'} <= set(calls['/procedures']['match'])
    assert 'KELLY' in calls['/families']['match']
