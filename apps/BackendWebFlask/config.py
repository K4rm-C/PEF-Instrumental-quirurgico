import os
from urllib.parse import quote_plus


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
    return f'postgresql+psycopg://{user}:{password}@{host}:{port}/{name}'


class Config:
    SECRET_KEY = os.getenv(
        'SESSION_SECRET_KEY',
        'development-only-session-key-change-me',
    )
    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }