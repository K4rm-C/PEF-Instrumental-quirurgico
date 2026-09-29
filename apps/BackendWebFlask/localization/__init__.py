"""Application-level localization (English / Spanish) for BackendWebFlask.

English is the canonical language for persisted database business data and catalog data; UI
localization is handled here, at the application layer, and never writes catalog rows.

- UI text: gettext catalogs in translations/<language>/LC_MESSAGES/messages.po (Flask-Babel).
- Catalog-backed values: translated by stable code (localization.labels), never by display name.
- Locale resolution (resolve_locale), once per request:
      authenticated user -> user.ui_preferences.locale, else English
      anonymous visitor  -> Flask session['locale'],     else English
  Anything missing, invalid or malformed falls back to English; missing translations fall back
  to the English source text.
"""
import json
from urllib.parse import urlsplit

from flask import current_app, g, has_request_context, request, session, url_for
from flask_babel import Babel, gettext, refresh
from sqlalchemy import cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError

from extensions import db

SUPPORTED_LOCALES = ('en', 'es')
DEFAULT_LOCALE = 'en'
SESSION_KEY = 'locale'
# Babel locale used for dates/numbers; translations are found by language (es_MX -> es).
FORMAT_LOCALES = {'en': 'en', 'es': 'es_MX'}
# Endonyms: each language is always named in its own language (not translated).
LANGUAGES = (
    {'code': 'en', 'label': 'English', 'short_label': 'EN'},
    {'code': 'es', 'label': 'Español', 'short_label': 'ES'},
)

babel = Babel()
_user_loader = None


def init_app(app, user_loader):
    """user_loader() -> verified user dict (with 'id') or None; called at most once per request."""
    global _user_loader
    _user_loader = user_loader
    babel.init_app(app, default_locale=DEFAULT_LOCALE,
                   locale_selector=lambda: FORMAT_LOCALES[resolve_locale()])

    @app.before_request
    def _resolve_request_locale():
        # resolved before any view work, so a failing preference read can never touch a business transaction
        if request.endpoint != 'static':
            resolve_locale()

    from localization import labels
    # globals (not context values) so imported macro templates can use them too
    app.jinja_env.globals.update(catalog_label=labels.label, role_name=labels.role_name,
                                 format_number=labels.format_number, js_messages=js_messages)

    @app.context_processor
    def _localization_context():
        return {
            'current_locale': resolve_locale(),
            'language_options': LANGUAGES,
            'locale_next_url': current_page_path(),
            'locale_switch_url': url_for('web.set_locale_preference'),
        }


def normalize_locale(value):
    """'en' / 'es', or None for anything else (strict: no guessing, no partial matches)."""
    return value if isinstance(value, str) and value in SUPPORTED_LOCALES else None


def stored_user_locale(user_id):
    """The user's ui_preferences.locale, or None when missing, malformed or unreadable."""
    from models.User import User
    from services.admin_service import parse_uuid

    uid = parse_uuid(user_id)
    if uid is None:
        return None
    try:
        preferences = db.session.execute(select(User.ui_preferences).where(User.id == uid)).scalar_one_or_none()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Could not read the UI locale preference; using English')
        return None
    return normalize_locale(preferences.get('locale')) if isinstance(preferences, dict) else None


def resolve_locale():
    """Locale code of the current request ('en' / 'es'), cached for the request."""
    if not has_request_context():
        return DEFAULT_LOCALE
    if 'pef_locale' not in g:
        user = _user_loader() if _user_loader else None
        code = stored_user_locale(user.get('id')) if user else normalize_locale(session.get(SESSION_KEY))
        g.pef_locale = code or DEFAULT_LOCALE
    return g.pef_locale


def set_locale(code, user=None):
    """Apply an explicit choice: web session always, user.ui_preferences.locale when signed in.

    Returns False (and stores nothing) for an unsupported value. The caller commits.
    """
    code = normalize_locale(code)
    if code is None:
        return False
    if user:
        _store_user_locale(user.get('id'), code)
    session[SESSION_KEY] = code
    g.pef_locale = code
    refresh()
    return True


def _store_user_locale(user_id, code):
    """Update only the 'locale' key of ui_preferences, atomically; every other key is kept."""
    from models.User import User
    from services.admin_service import parse_uuid

    uid = parse_uuid(user_id)
    if uid is None:
        return
    statement = user_locale_update(uid, code, db.session.get_bind().dialect.name)
    if statement is not None:
        db.session.execute(statement)
        return
    user = db.session.get(User, uid)  # other dialects: read-modify-write of the same object
    if user is not None:
        user.ui_preferences = {**(user.ui_preferences or {}), 'locale': code}


def user_locale_update(user_id, code, dialect):
    """Single-statement merge of {"locale": code} into ui_preferences (None for unsupported dialects)."""
    from models.User import User

    if dialect == 'postgresql':
        # jsonb merge replacing only 'locale'; the dict is bound as a JSONB object (a pre-encoded string
        # would arrive as a JSON *string* and `object || string` yields an array)
        value = User.ui_preferences.op('||')(cast(literal({'locale': code}, JSONB), JSONB))
    elif dialect == 'sqlite':
        value = func.json_patch(User.ui_preferences, json.dumps({'locale': code}))
    else:
        return None
    return update(User).where(User.id == user_id).values(ui_preferences=value)


def remember_for_signed_out_session(code):
    """Keep the UI language for the public pages after the web session is cleared (sign-out)."""
    code = normalize_locale(code)
    if code:
        session[SESSION_KEY] = code


def safe_local_path(value):
    """A same-site path (+ query) to return to after switching language, else None."""
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//') or '\\' in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    return value


def current_page_path():
    """Where the language switch should return: this page for GET, the referring page otherwise."""
    if not has_request_context():
        return '/'
    if request.method == 'GET':
        return request.full_path.rstrip('?')
    referrer = urlsplit(request.referrer or '')
    if referrer.netloc and referrer.netloc != request.host:
        return ''
    return safe_local_path(referrer.path + (f'?{referrer.query}' if referrer.query else '')) or ''


def js_messages():
    """The only strings JavaScript builds itself; rendered once per page into base.html."""
    return {
        'remove': gettext('Remove'),
        'uploading': gettext('Uploading...'),
        'passwordMismatch': gettext('Passwords do not match.'),
    }
