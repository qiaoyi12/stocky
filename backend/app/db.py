"""SQLite engine, session factory, and schema bootstrap for STOCKY.

Provides the single SQLAlchemy engine bound to ``DATABASE_URL`` (default
``sqlite:///./stocky.db``), a session factory, and ``init_db()`` which creates
all tables declared in ``app.models``. This module owns database connectivity;
it holds no business logic and performs no inventory mutation itself.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# ``check_same_thread=False`` lets the SQLite connection be shared across the
# threads FastAPI uses for request handling. It is required only for SQLite.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Declarative base shared by every model in ``app.models``.
Base = declarative_base()


def init_db() -> None:
    """Create all tables defined on ``Base`` if they do not already exist.

    Importing ``app.models`` registers every table on ``Base.metadata`` before
    ``create_all`` runs, so a single call materialises the full schema.
    """
    # Import for side effect: registers models on Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session():
    """FastAPI dependency yielding a session that is always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
