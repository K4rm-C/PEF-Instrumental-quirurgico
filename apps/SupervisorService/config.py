import os

from dotenv import load_dotenv


load_dotenv()


def _int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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


class Config:
    DATABASE_URL = _database_url()
    DB_CONNECT_TIMEOUT = _int('DB_CONNECT_TIMEOUT', 5)
    DB_STATEMENT_TIMEOUT = _int('DB_STATEMENT_TIMEOUT', 5000)
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'change-this-secret')
    JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    HOST = os.getenv('SUPERVISOR_SERVICE_HOST', '0.0.0.0')
    PORT = _int('SUPERVISOR_SERVICE_PORT', 5004)
