"""Database engine and session management (SQLite via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from src.config import get_settings

_engine = None


def get_engine():
    """Lazily create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
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
    """Create all tables if they do not exist."""
    # Import models so SQLModel metadata is populated.
    from src.database import models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed database session."""
    with Session(get_engine()) as session:
        yield session
