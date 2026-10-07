import os
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
from dotenv import load_dotenv


load_dotenv()


def database_url():
    """Return the configured PostgreSQL URL, preferring DATABASE_URL."""
    configured_url = os.getenv('DATABASE_URL')
    if configured_url:
        return configured_url

    user = quote_plus(os.getenv('DB_USER', 'postgres'))
    password = quote_plus(os.getenv('DB_PASSWORD', 'postgres'))
    host = os.getenv('DB_HOST', 'localhost')
    port = os.getenv('DB_PORT', '5432')
    name = os.getenv('DB_NAME', 'pecausas')
    configured_url = f'postgresql+psycopg://{user}:{password}@{host}:{port}/{name}'
    return _with_connection_timeout(configured_url)


def _with_connection_timeout(url):
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault('connect_timeout', os.getenv('DB_CONNECT_TIMEOUT', '5'))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


class Config:
    SECRET_KEY = os.getenv(
        'SESSION_SECRET_KEY',
        'development-only-session-key-change-me',
    )
    SQLALCHEMY_DATABASE_URI = _with_connection_timeout(database_url())
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', '5')),
        'connect_args': {
            'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', '5')),
            'options': f"-c statement_timeout={int(os.getenv('DB_STATEMENT_TIMEOUT', '5000'))}",
        },
    }
    # Development-only view-data fallback. Authentication always uses the auth service.
    FRONTEND_DEMO_MODE = os.getenv('FRONTEND_DEMO_MODE', 'false').strip().lower() == 'true'
    AUTH_SERVICE_URL = os.getenv('AUTH_SERVICE_URL', os.getenv('AUTH_URL', 'http://127.0.0.1:5001')).strip().rstrip('/')
    AUTH_BROWSER_URL = os.getenv('AUTH_BROWSER_URL', AUTH_SERVICE_URL).strip().rstrip('/')
    AUTH_SERVICE_TIMEOUT = float(os.getenv('AUTH_SERVICE_TIMEOUT', '5'))
    # i18n (Flask-Babel). English msgids are the source text; public locale codes are
    # i18n.SUPPORTED_LOCALES (en, es-MX), catalogs live in translations/<babel locale>/.
    BABEL_DEFAULT_LOCALE = 'en'
    BABEL_DEFAULT_TIMEZONE = 'UTC'
    BABEL_TRANSLATION_DIRECTORIES = 'translations'