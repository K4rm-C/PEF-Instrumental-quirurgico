import os

from dotenv import load_dotenv


load_dotenv()


def _database_url():
    configured = os.getenv('DATABASE_URL')
    if configured:
        return configured
    return (
        'postgresql+psycopg://'
        f"{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}@"
        f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'InstruMed')}"
    )


def _int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Config:
    DATABASE_URL = _database_url()
    DB_CONNECT_TIMEOUT = _int('DB_CONNECT_TIMEOUT', 5)
    DB_STATEMENT_TIMEOUT = _int('DB_STATEMENT_TIMEOUT', 5000)

    # Same secret/algorithm as BackendAuthService: this service only verifies tokens.
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'change-this-secret')
    JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
    # Redis session store written by BackendAuthService (auth:jwt:{jti}); used to reject
    # revoked tokens (logout) exactly like the auth service does.
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    HOST = os.getenv('ADMIN_SERVICE_HOST', '0.0.0.0')
    # The shared .env uses PORT for the auth service, so this service has its own variable.
    PORT = _int('ADMIN_SERVICE_PORT', 5003)

    # Timezone used to show audit timestamps and to filter the audit log by day.
    DISPLAY_TIMEZONE = os.getenv('ADMIN_DISPLAY_TIMEZONE', 'America/Mexico_City')

    # Bucket recorded in media_asset for vision model weights registered by reference.
    MODEL_WEIGHTS_BUCKET = os.getenv('ADMIN_MODEL_WEIGHTS_BUCKET', 'models')
