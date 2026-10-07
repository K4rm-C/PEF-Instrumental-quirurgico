import pytest
from flask import render_template_string

import i18n
from controllers import routes


def html(response):
    return response.get_data(as_text=True)


# --- A. Locale resolution ---------------------------------------------------------------

@pytest.mark.parametrize('cookie, expected', [
    (None, 'en'),
    ('en', 'en'),
    ('es-MX', 'es-MX'),
    ('es', 'es-MX'),
    ('fr', 'en'),
    ('es_MX', 'es-MX'),
    ('', 'en'),
])
def test_guest_locale_from_cookie(app, cookie, expected):
    headers = {'Cookie': f'pef_locale={cookie}'} if cookie is not None else {}
    with app.test_request_context('/', headers=headers):
        assert i18n.resolve_locale(None) == expected


def test_authenticated_preference_wins_over_cookie(app):
    with app.test_request_context('/', headers={'Cookie': 'pef_locale=es-MX'}):
        assert i18n.resolve_locale({'ui_preferences': {'locale': 'en'}}) == 'en'
    with app.test_request_context('/', headers={'Cookie': 'pef_locale=en'}):
        assert i18n.resolve_locale({'ui_preferences': {'locale': 'es-MX'}}) == 'es-MX'
        assert i18n.resolve_locale({'ui_preferences': {}}) == 'en'


def test_babel_locale_uses_posix_form_only_internally():
    assert i18n.babel_locale('es-MX') == 'es_MX'
    assert i18n.babel_locale('en') == 'en'
    assert i18n.babel_locale('xx') == 'en'


# --- Real gettext (no placeholder translator left) ----------------------------------------

def test_jinja_gettext_is_flask_babel(app):
    assert not any('template_helpers' == getattr(fn, '__name__', '')
                   for fn in app.template_context_processors.get('web', []))
    with app.test_request_context('/', headers={'Cookie': 'pef_locale=es-MX'}):
        assert render_template_string('{{ _("Expected Inventory") }}') == 'Inventario esperado'
        # Dynamic values: empty stays empty (no catalog header), '%' and markup stay safe.
        assert render_template_string('[{{ _(v) }}]', v='') == '[]'
        assert render_template_string('{{ _(v) }}', v='94.2%') == '94.2%'
        assert render_template_string('{{ _(v) }}', v='<b>x</b>') == '&lt;b&gt;x&lt;/b&gt;'
        assert render_template_string('{{ _("Page %(current)s of %(total)s", current=1, total=3) }}') \
            == 'Página 1 de 3'


def test_python_labels_and_plurals(app):
    from flask_babel import gettext, ngettext
    with app.test_request_context('/', headers={'Cookie': 'pef_locale=es-MX'}):
        assert gettext(routes.ROLE_LABELS['station_operator']) == 'Operador CDE'
        assert gettext('Awaiting Review') == 'Esperando revisión de SPD'
        assert ngettext('%(num)d open discrepancy', '%(num)d open discrepancies', 1) == '1 discrepancia abierta'
        assert ngettext('%(num)d open discrepancy', '%(num)d open discrepancies', 3) == '3 discrepancias abiertas'
    with app.test_request_context('/'):
        assert gettext('Awaiting Review') == 'Awaiting Review'


# --- B / E / F. Rendering, <html lang> and the header selector ----------------------------

GUEST_PAGES = [
    ('/', 'How It Works', 'Cómo funciona'),
    ('/sign-in', 'Enter your password', 'Ingresa tu contraseña'),
]


@pytest.mark.parametrize('path, english, spanish', GUEST_PAGES)
def test_guest_pages_render_in_both_languages(client, path, english, spanish):
    page = html(client.get(path))
    assert '<html lang="en">' in page and english in page and spanish not in page
    client.set_cookie('pef_locale', 'es-MX')
    page = html(client.get(path))
    assert '<html lang="es-MX">' in page and spanish in page
    assert '<span>ES</span>' in page
    assert '"Cancelar"' in page  # window.PEF_I18N for static/js


def _operator_detail(session_id):
    return {
        'id': session_id, 'session_id': 'WS-ABCDEF12', 'status_code': 'in_progress',
        'status_label': 'In Progress', 'status_variant': 'info', 'capture_mode': 'vision',
        'privacy_label': 'Notice OK · Vision', 'privacy_variant': 'success',
        'procedure_name': 'Apendicectomia', 'operating_room': 'OR-1', 'kit_name': 'Kit Demo',
        'capture_station_name': 'CDE-01', 'scheduled_at': '2026-10-07 08:00', 'started_at': '',
        'discrepancy_count': 0, 'privacy': {
            'has_agreement': False,
            'message': 'No privacy notice linked. Start Session will freeze capture_mode = manual_no_privacy.',
        },
        'expected_items': [{
            'family_id': 'f1', 'family_name': 'Pinza Kelly', 'family_code': 'KELLY',
            'expected_quantity': 2, 'reported_quantity': None, 'difference': None,
            'status_label': 'Pending', 'status_variant': 'neutral', 'source_label': 'Kit',
            'category_label': 'Hemostasis', 'category_code': 'hemostasis',
        }],
        'action_url': '#',
    }


def _schedule_options(_institution_id):
    return {key: [] for key in ('operators', 'procedures', 'rooms', 'stations', 'kits', 'patients',
                                'physicians', 'surgical_roles', 'phases', 'privacy_notices', 'families')}


AUTH_PAGES = [
    ('station_operator', '/operator/sessions', 'Assigned Sessions', 'Sesiones asignadas'),
    ('station_operator', '/operator/sessions/33333333-3333-3333-3333-333333333333',
     'Privacy / Capture', 'Privacidad / Captura'),
    ('spd_supervisor', '/supervisor/dashboard',
     'Sessions Requiring Supervisor Review', 'Sesiones que requieren revisión del supervisor'),
    ('spd_supervisor', '/supervisor/sessions/new', 'Schedule counting session', 'Programar sesión de conteo'),
    ('spd_supervisor', '/supervisor/sessions/WS-026', 'Supervisor Session Details',
     'Detalles de la sesión del supervisor'),
    ('it_admin', '/admin/dashboard', 'Catalog Overview', 'Resumen de catálogos'),
    ('station_operator', '/operator/profile', 'Interface language', 'Idioma de la interfaz'),
]


@pytest.mark.parametrize('role, path, english, spanish', AUTH_PAGES)
def test_authenticated_pages_render_in_both_languages(client, login, monkeypatch, role, path, english, spanish):
    import services.rf_session
    monkeypatch.setattr(routes, 'operator_session_detail', lambda sid, uid: _operator_detail(sid))
    monkeypatch.setattr(services.rf_session, 'schedule_form_options', _schedule_options)

    login(role=role, locale='en')
    response = client.get(path)
    assert response.status_code == 200
    page = html(response)
    assert '<html lang="en">' in page and english in page and spanish not in page
    assert '<span>EN</span>' in page

    login(role=role, locale='es-MX')
    page = html(client.get(path))
    assert '<html lang="es-MX">' in page and spanish in page and english not in page
    assert '<span>ES</span>' in page


def test_status_labels_are_translated_but_codes_are_not(client, login, monkeypatch):
    monkeypatch.setattr(routes, 'operator_session_detail', lambda sid, uid: _operator_detail(sid))
    login(locale='es-MX')
    page = html(client.get('/operator/sessions/33333333-3333-3333-3333-333333333333'))
    assert 'En progreso' in page and 'Aviso OK · Visión' in page
    assert 'No hay aviso de privacidad vinculado.' in page
    # Business data from the DB is shown as stored.
    assert 'Apendicectomia' in page and 'Pinza Kelly' in page and 'WS-ABCDEF12' in page


def test_profile_reflects_current_language(client, login):
    login(locale='es-MX')
    page = html(client.get('/operator/profile'))
    assert 'Español (México)' in page
    login(locale='en')
    page = html(client.get('/operator/profile'))
    assert '<span class="profile-preferences__value">English</span>' in page


def test_operator_role_label_translated_in_header(client, login):
    login(locale='es-MX')
    assert 'Operador CDE' in html(client.get('/operator/sessions'))


# --- C / D. Language endpoint and persistence ---------------------------------------------

@pytest.mark.parametrize('locale', ['en', 'es-MX'])
def test_guest_locale_endpoint_sets_cookie(client, locale):
    response = client.post('/locale', json={'locale': locale})
    assert response.status_code == 200
    assert response.get_json()['ok'] is True
    cookie = response.headers['Set-Cookie']
    assert f'pef_locale={locale}' in cookie and 'Path=/' in cookie and 'SameSite=Lax' in cookie
    assert 'Max-Age=31536000' in cookie


@pytest.mark.parametrize('locale', ['fr', 'es', 'es_MX', '', None, 5])
def test_locale_endpoint_rejects_unsupported(client, locale):
    response = client.post('/locale', json={'locale': locale})
    assert response.status_code == 400
    assert 'Set-Cookie' not in response.headers


def test_authenticated_locale_change_persists_through_auth_service(client, login):
    fake = login(role='station_operator', locale='en', theme='dark')
    response = client.post('/locale', json={'locale': 'es-MX'})
    assert response.status_code == 200
    assert response.get_json()['ui_preferences'] == {'locale': 'es-MX', 'theme': 'dark'}
    calls = [call for call in fake.calls if call[1] == '/preferences']
    assert calls == [('post', '/preferences', 'test-access-token', {'json': {'locale': 'es-MX'}})]
    assert 'pef_locale=es-MX' in response.headers['Set-Cookie']
    # theme untouched; next request renders Spanish from the stored preference.
    assert fake.preferences == {'locale': 'es-MX', 'theme': 'dark'}
    client.delete_cookie('pef_locale')
    assert '<html lang="es-MX">' in html(client.get('/operator/sessions'))


def test_authenticated_locale_change_fails_when_auth_rejects(client, login, monkeypatch):
    fake = login()
    original = fake.__call__

    def failing(method, path, token=None, **kwargs):
        if path == '/preferences':
            from tests.conftest import FakeResponse
            return FakeResponse(503)
        return original(method, path, token=token, **kwargs)

    monkeypatch.setattr(routes, '_auth_request', failing)
    response = client.post('/locale', json={'locale': 'es-MX'})
    assert response.status_code == 503
    # Nothing saved: the cookie keeps (or is resynced to) the stored account preference.
    assert 'pef_locale=es-MX' not in response.headers.get('Set-Cookie', '')


def test_login_preference_overrides_guest_cookie(client, login):
    """Guest cookie es-MX + account preference en -> English UI and cookie resynced to en."""
    client.set_cookie('pef_locale', 'es-MX')
    login(locale='en')
    response = client.get('/operator/sessions')
    assert '<html lang="en">' in html(response)
    assert 'pef_locale=en' in response.headers.get('Set-Cookie', '')


def test_sign_in_page_exposes_translated_auth_errors(client):
    client.set_cookie('pef_locale', 'es-MX')
    page = html(client.get('/sign-in'))
    # tojson escapes non-ASCII characters for the inline script.
    assert r'"Credenciales inv\u00e1lidas."' in page
    assert r'"El servicio de autenticaci\u00f3n no est\u00e1 disponible."' in page


def test_catalog_has_no_empty_translations():
    from pathlib import Path

    from babel.messages.pofile import read_po
    po = Path(__file__).resolve().parents[1] / 'translations' / 'es_MX' / 'LC_MESSAGES' / 'messages.po'
    with po.open('rb') as handle:
        catalog = read_po(handle)
    untranslated = [m.id for m in catalog if m.id and not all(m.string if isinstance(m.string, tuple)
                                                                  else [m.string])]
    assert untranslated == []
    assert not [m.id for m in catalog if m.id and m.fuzzy]
