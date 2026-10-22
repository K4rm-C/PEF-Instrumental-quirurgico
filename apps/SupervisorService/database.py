from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

from config import Config


class Base(DeclarativeBase):
    pass


engine = create_engine(
    Config.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args={
        'connect_timeout': Config.DB_CONNECT_TIMEOUT,
        'options': f'-c statement_timeout={Config.DB_STATEMENT_TIMEOUT}',
    },
)
db = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
