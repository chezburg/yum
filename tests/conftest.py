"""Shared test fixtures."""

from __future__ import annotations

import pytest

from src.reconstruction.schemas import (
    EvidenceSource,
    Ingredient,
    InstructionStep,
    StructuredRecipe,
)

TEST_SECRET_KEY = "test-secret-key-0123456789abcdef"


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Isolated app environment: temp SQLite DB + SECRET_KEY, fresh caches.

    Creates the schema via SQLModel.create_all (fast; skips Alembic).
    Yields the database path.
    """
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")

    import src.config as config
    import src.database.connection as connection
    from src.services import settings_service

    config.get_bootstrap.cache_clear()
    connection._engine = None
    settings_service.invalidate_cache()

    from sqlmodel import SQLModel

    from src.database import models  # noqa: F401

    SQLModel.metadata.create_all(connection.get_engine())

    yield tmp_path / "test.db"

    config.get_bootstrap.cache_clear()
    connection._engine = None
    settings_service.invalidate_cache()


@pytest.fixture
def client(app_env, monkeypatch):
    """FastAPI TestClient with a stubbed pipeline executor.

    Bypasses main.py's lifespan-driven Alembic migration (schema already
    created by app_env) while still wiring the job submitter.
    """
    from fastapi.testclient import TestClient

    import src.main as main

    submitted: list[str] = []
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "_submit", submitted.append)

    with TestClient(main.app) as test_client:
        test_client.submitted_jobs = submitted
        yield test_client


@pytest.fixture
def sample_recipe() -> StructuredRecipe:
    """A complete, valid recipe for testing validation and export."""
    return StructuredRecipe(
        title="Garlic Butter Pasta",
        description="Quick weeknight pasta.",
        ingredients=[
            Ingredient(
                name="spaghetti",
                amount="200 g",
                source=EvidenceSource.CAPTION,
                confidence=0.95,
            ),
            Ingredient(
                name="garlic",
                amount="4 cloves",
                preparation="minced",
                source=EvidenceSource.CREATOR_REPLY,
                confidence=0.97,
            ),
            Ingredient(
                name="butter",
                amount="3 tbsp",
                source=EvidenceSource.OCR,
                confidence=0.85,
            ),
        ],
        instructions=[
            InstructionStep(
                step_number=1,
                text="Boil the spaghetti until al dente.",
                duration="8 min",
                source=EvidenceSource.TRANSCRIPT,
                confidence=0.9,
            ),
            InstructionStep(
                step_number=2,
                text="Melt butter, add garlic, toss with pasta.",
                source=EvidenceSource.TRANSCRIPT,
                confidence=0.88,
            ),
        ],
        equipment=["large pot", "skillet"],
        prep_time="5 min",
        cook_time="10 min",
        servings="2 servings",
        tags=["italian", "pasta"],
        overall_confidence=0.9,
        notes=[],
    )
