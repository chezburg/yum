"""yum - self-hosted Instagram recipe extractor.

FastAPI application entrypoint: mounts the JSON API and the web UI,
initializes the database (Alembic migrations), and manages the
background pipeline executor.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import __version__
from src.api.routes import router as api_router
from src.api.routes import set_job_submitter as set_api_submitter
from src.config import get_bootstrap
from src.database.connection import init_db
from src.pipeline import run_pipeline
from src.web.routes import router as web_router
from src.web.routes import set_job_submitter as set_web_submitter

logger = logging.getLogger(__name__)

# Single-worker executor: extraction is resource-heavy (Whisper/OCR), so jobs
# are processed one at a time. Increase max_workers if you have the hardware.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


def _submit(job_id: str) -> None:
    _executor.submit(run_pipeline, job_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap = get_bootstrap()
    logging.basicConfig(
        level=bootstrap.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not bootstrap.secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Add it to your environment/.env "
            "(generate with: openssl rand -hex 32)."
        )
    init_db()
    set_api_submitter(_submit)
    set_web_submitter(_submit)
    logger.info("yum v%s started.", __version__)
    yield
    _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="yum",
    description="Self-hosted Instagram recipe extractor",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(web_router)
