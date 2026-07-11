"""Database engine and session management (SQLite via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from src.config import get_bootstrap

_engine = None


def get_engine():
    """Lazily create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        url = get_bootstrap().database_url
        if url.startswith("sqlite"):
            # Ensure the parent directory of the SQLite file exists.
            db_path = url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
    return _engine


def init_db() -> None:
    """Bring the database schema up to date via Alembic migrations.

    Falls back to SQLModel.create_all when the Alembic environment is not
    available (e.g. certain test setups).
    """
    # Import models so SQLModel metadata is populated.
    from src.database import models  # noqa: F401

    engine = get_engine()
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    if alembic_ini.is_file():
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(alembic_ini))
        cfg.set_main_option("sqlalchemy.url", get_bootstrap().database_url)
        command.upgrade(cfg, "head")
    else:
        SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed database session."""
    with Session(get_engine()) as session:
        yield session
