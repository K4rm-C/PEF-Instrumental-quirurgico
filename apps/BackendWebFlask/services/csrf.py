"""MS-04 (P7): CSRF protection of the portal (cookie-authenticated, server-rendered forms + same-origin fetch).

Mechanism — signed double-submit token bound to the user:
- a random nonce lives in an HttpOnly cookie (`__Host-pef_csrf` over HTTPS, `pef_csrf` in development),
  SameSite=Strict, Path=/;
- the token rendered in every form (`csrf_token` hidden field) and in `<meta name="csrf-token">` (sent by the
  page script as the `X-CSRF-Token` header of same-origin fetch/XHR) is `nonce.HMAC(SECRET_KEY, nonce|subject)`,
  subject = the verified user id (or "anon" for guests);
- every state-changing request (POST/PUT/PATCH/DELETE) must carry a token whose nonce equals the cookie and
  whose HMAC matches the current user. A cross-site page can neither read the token nor forge the HMAC, and a
  planted cookie (cookie tossing) is useless without a matching HMAC for the victim.
- complementary check: when the browser sends Origin (or Referer) it must be this site or a configured
  trusted origin (CSRF_TRUSTED_ORIGINS). SameSite=Lax on the auth cookies is not relied upon alone.

The portal has no Bearer-authenticated API: every unsafe request is checked (no exemptions). Microservices
and the API Gateway are Bearer-only and never read cookies, so they need no CSRF token. WebSockets are opened
against VisionWorker (GET upgrade, own ticket + Origin check) and are not affected.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, request
from markupsafe import Markup, escape

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})
FORM_FIELD = 'csrf_token'
HEADER = 'X-CSRF-Token'
FAILURE_MESSAGE = ('The form expired or the request did not come from this site. '
                   'Reload the page and try again.')


def cookie_name() -> str:
    # The __Host- prefix (Secure, Path=/, no Domain) cannot be set or overwritten by a sibling subdomain.
    return '__Host-pef_csrf' if current_app.config.get('PORTAL_COOKIE_SECURE') else 'pef_csrf'


def _nonce() -> str:
    # Kept on the request (not on g: an app context, and its g, may outlive one request).
    nonce = request.environ.get('pef.csrf_nonce')
    if nonce is None:
        nonce = request.cookies.get(cookie_name())
        if not nonce or len(nonce) > 128:
            nonce = secrets.token_urlsafe(32)
            request.environ['pef.csrf_new'] = True
        request.environ['pef.csrf_nonce'] = nonce
    return nonce


def _subject() -> str:
    user = g.get('verified_user') if 'verified_user' in g else _verified_user()
    return str(user['id']) if user and user.get('id') else 'anon'


def _verified_user():
    # Guests (no auth cookie) are "anon" without asking AuthService (and without touching the session).
    if not (request.cookies.get('access_token') or request.cookies.get('refresh_token')):
        return None
    from controllers.routes import _current_user
    return _current_user()


def _sign(nonce: str, subject: str) -> str:
    key = current_app.config['SECRET_KEY'].encode('utf-8')
    return hmac.new(key, f'{nonce}|{subject}'.encode('utf-8'), hashlib.sha256).hexdigest()


def generate_token() -> str:
    nonce = _nonce()
    return f'{nonce}.{_sign(nonce, _subject())}'


def csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="{FORM_FIELD}" value="{escape(generate_token())}">')


def _same_site_origin(value: str) -> bool:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return False
    origin = f'{parts.scheme}://{parts.netloc}'.lower()
    trusted = {o.lower() for o in current_app.config.get('CSRF_TRUSTED_ORIGINS', ())}
    return parts.netloc.lower() == request.host.lower() or origin in trusted


def check_request():
    """Refusal reason for an unsafe request ('origin', 'missing', 'invalid'), or None when it is valid."""
    origin = request.headers.get('Origin')
    if origin:
        if origin == 'null' or not _same_site_origin(origin):
            return 'origin'
    else:
        referer = request.headers.get('Referer')
        if referer and not _same_site_origin(referer):
            return 'origin'
    token = request.headers.get(HEADER) or request.form.get(FORM_FIELD)
    cookie = request.cookies.get(cookie_name())
    if not token or not cookie:
        return 'missing'
    nonce, _, signature = token.partition('.')
    if not signature or not hmac.compare_digest(nonce, cookie):
        return 'invalid'
    if not hmac.compare_digest(signature, _sign(nonce, _subject())):
        return 'invalid'
    return None


def _refuse(reason: str):
    current_app.logger.warning('CSRF refused (%s): %s %s', reason, request.method, request.path)
    wants_json = (request.headers.get(HEADER) is not None or request.is_json
                  or request.accept_mimetypes.best == 'application/json')
    if wants_json:
        return jsonify({'ok': False, 'error': FAILURE_MESSAGE, 'reason': f'csrf_{reason}'}), 403
    from flask_babel import gettext
    body = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>403</title></head><body>'
            f'<h1>403</h1><p>{escape(gettext(FAILURE_MESSAGE))}</p></body></html>')
    return body, 403, {'Content-Type': 'text/html; charset=utf-8'}


def init_app(app) -> None:
    app.config.setdefault('CSRF_ENABLED', True)
    app.config.setdefault('CSRF_TRUSTED_ORIGINS', ())
    app.jinja_env.globals['csrf_token'] = generate_token
    app.jinja_env.globals['csrf_field'] = csrf_field

    @app.get('/csrf-token')
    def csrf_token_endpoint():
        """Token for non-browser clients of the portal (scripts, tests). Same-origin JSON: a foreign page
        cannot read it (no CORS on the portal); it is useless without the HttpOnly cookie it comes with."""
        response = jsonify({'csrf_token': generate_token(), 'header': HEADER, 'field': FORM_FIELD})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def csrf_protect():
        if not app.config['CSRF_ENABLED'] or request.method in SAFE_METHODS:
            return None
        reason = check_request()
        return _refuse(reason) if reason else None

    @app.after_request
    def csrf_cookie(response):
        if request.environ.get('pef.csrf_new'):
            response.set_cookie(cookie_name(), request.environ['pef.csrf_nonce'], httponly=True,
                                samesite='Strict', path='/',
                                secure=bool(app.config.get('PORTAL_COOKIE_SECURE')))
        return response
