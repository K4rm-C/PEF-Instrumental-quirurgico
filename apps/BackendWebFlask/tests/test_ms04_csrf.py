"""MS-04 (P7) — CSRF protection of the portal (services/csrf.py).

Positive and negative cases: legitimate requests (header and form field), missing / invalid / foreign tokens,
another user's token, foreign Origin/Referer, safe methods, cookie attributes, rendered forms. The rest of
the suite runs with the protection ON through conftest.CsrfClient (legitimate token on every request).
"""
import re

import pytest

from controllers import routes
from .conftest import FakeAuth, FakeResponse

USER_A = '22222222-2222-2222-2222-222222222222'
USER_B = '33333333-3333-3333-3333-333333333333'


class FakeAuthB(FakeAuth):
    def __call__(self, method, path, token=None, **kwargs):
        response = super().__call__(method, path, token, **kwargs)
        if path == '/verify':
            response._payload['user']['id'] = USER_B
        return response


def _token(client):
    return client.get('/csrf-token').get_json()['csrf_token']


def _locale(client, token=None, **headers):
    if token is not None:
        headers['X-CSRF-Token'] = token
    return client.post('/locale', json={'locale': 'es-MX'}, headers=headers, csrf=False)


# --- legitimate requests ------------------------------------------------------------------------------


def test_guest_request_with_its_token_is_accepted(client):
    assert _locale(client, _token(client)).status_code == 200


def test_authenticated_request_with_header_token_is_accepted(client, login):
    login(role='station_operator')
    response = _locale(client, _token(client), Origin='http://localhost')
    assert response.status_code == 200


def test_form_field_token_is_accepted(client, login):
    login(role='station_operator')
    token = _token(client)
    response = client.post('/sign-out', data={'csrf_token': token}, csrf=False)
    assert response.status_code == 302 and '/sign-in' in response.headers['Location']


def test_same_origin_referer_is_accepted(client, login):
    login(role='station_operator')
    assert _locale(client, _token(client), Referer='http://localhost/operator/sessions').status_code == 200


def test_configured_trusted_origin_is_accepted(client, login, app, monkeypatch):
    monkeypatch.setitem(app.config, 'CSRF_TRUSTED_ORIGINS', ('https://pef.hospital.example',))
    login(role='station_operator')
    assert _locale(client, _token(client), Origin='https://pef.hospital.example').status_code == 200


def test_safe_methods_need_no_token(client, login):
    login(role='station_operator')
    assert client.get('/operator/sessions').status_code == 200
    assert client.head('/sign-in').status_code == 200


# --- refused requests ---------------------------------------------------------------------------------


def test_missing_token_is_refused(client, login):
    fake = login(role='station_operator')
    response = _locale(client)
    assert response.status_code == 403 and response.get_json()['reason'] == 'csrf_missing'
    assert not [call for call in fake.calls if call[1] == '/preferences']   # nothing changed


def test_missing_cookie_is_refused(client, login):
    login(role='station_operator')
    token = _token(client)
    client.delete_cookie('pef_csrf')
    assert _locale(client, token).status_code == 403


@pytest.mark.parametrize('mutate', [
    lambda t: t + 'x',                                   # tampered signature
    lambda t: t.split('.')[0] + '.' + '0' * 64,          # forged signature
    lambda t: t.split('.')[0],                           # no signature
    lambda t: 'other-nonce.' + t.split('.')[1],          # nonce of another cookie
    lambda t: 'garbage',
])
def test_invalid_tokens_are_refused(client, login, mutate):
    login(role='station_operator')
    response = _locale(client, mutate(_token(client)))
    assert response.status_code == 403 and response.get_json()['reason'] == 'csrf_invalid'


def test_planted_cookie_without_matching_signature_is_refused(client, login):
    """Cookie tossing: the attacker sets the CSRF cookie to a nonce of their choice; without the server's
    HMAC for the victim the token is useless."""
    login(role='station_operator')
    client.set_cookie('pef_csrf', 'attacker-nonce')
    assert _locale(client, 'attacker-nonce.' + 'a' * 64).status_code == 403


def test_token_of_another_user_is_refused(client, monkeypatch):
    monkeypatch.setattr(routes, '_auth_request', FakeAuth(role='station_operator'))
    client.set_cookie('access_token', 'token-of-a')
    token_a = _token(client)
    monkeypatch.setattr(routes, '_auth_request', FakeAuthB(role='station_operator'))
    assert _locale(client, token_a).status_code == 403
    assert _locale(client, _token(client)).status_code == 200


def test_guest_token_is_not_valid_after_sign_in(client, login):
    guest_token = _token(client)
    login(role='station_operator')
    assert _locale(client, guest_token).status_code == 403


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://evil.example'},
    {'Origin': 'null'},
    {'Origin': 'http://localhost.evil.example'},
    {'Referer': 'https://evil.example/attack.html'},
])
def test_foreign_origin_is_refused_even_with_a_valid_token(client, login, headers):
    login(role='station_operator')
    response = _locale(client, _token(client), **headers)
    assert response.status_code == 403 and response.get_json()['reason'] == 'csrf_origin'


def test_bearer_header_does_not_exempt_the_portal(client, login):
    """The portal authenticates with cookies only: an Authorization header changes nothing."""
    login(role='station_operator')
    assert _locale(client, Authorization='Bearer anything').status_code == 403


def test_html_form_refusal_is_a_403_page(client, login):
    login(role='station_operator')
    response = client.post('/sign-out', data={}, csrf=False)
    assert response.status_code == 403 and 'text/html' in response.content_type
    assert 'Reload the page' in response.get_data(as_text=True)


@pytest.mark.parametrize('method', ['put', 'patch', 'delete'])
def test_every_unsafe_method_is_checked(client, login, method):
    login(role='station_operator')
    assert getattr(client, method)('/locale', csrf=False).status_code == 403


# --- cookies and rendered pages -----------------------------------------------------------------------


def test_csrf_cookie_attributes(client):
    response = client.get('/csrf-token')
    cookie = next(v for k, v in response.headers.items() if k == 'Set-Cookie' and v.startswith('pef_csrf='))
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and 'Path=/' in cookie
    assert response.headers['Cache-Control'] == 'no-store'


def test_secure_mode_uses_host_prefix_and_secure_flag(client, app, monkeypatch):
    monkeypatch.setitem(app.config, 'PORTAL_COOKIE_SECURE', True)
    response = client.get('/csrf-token', base_url='https://localhost')
    cookie = next(v for k, v in response.headers.items() if k == 'Set-Cookie' and v.startswith('__Host-pef_csrf='))
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'Domain' not in cookie


def test_session_cookie_settings(app):
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_pages_carry_the_token_in_meta_and_every_post_form(client, login, fake_admin):
    login(role='it_admin')
    html = client.get('/admin/procedures/new').get_data(as_text=True)
    assert re.search(r'<meta name="csrf-token" content="[^"]+\.[0-9a-f]{64}">', html)
    assert 'js/csrf.js' in html
    forms = re.findall(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', html, flags=re.I | re.S)
    assert forms and all('name="csrf_token"' in body for body in forms)


def test_every_post_form_template_renders_the_field():
    """Static guard: no POST form template without {{ csrf_field() }} right after its opening tag."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'templates'
    missing = []
    for path in root.rglob('*.html'):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'<form\b[^>]*\bmethod=["\']post["\'][^>]*>', text, flags=re.I):
            if not text[match.end():match.end() + 40].lstrip().startswith('{{ csrf_field() }}'):
                missing.append(f'{path.relative_to(root)}:{match.group(0)[:60]}')
    assert missing == []


def test_websocket_ticket_and_capture_state_are_get_requests(client, login):
    """The live-preview ticket and the capture polling are GET: CSRF never blocks the WebSocket flow."""
    login(role='station_operator')
    for path in ('/operator/sessions/00000000-0000-0000-0000-000000000000/capture/ws-ticket',
                 '/operator/sessions/00000000-0000-0000-0000-000000000000/capture/state'):
        assert client.get(path).status_code != 403
