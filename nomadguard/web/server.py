"""FastAPI app: lifespan-managed workers, loopback posture, built-UI serving."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import Settings, load_env_file, load_settings
from ..jobs.runner import JobRunner
from ..jobs.store import JobStore
from ..providers.base import ProviderSet
from ..providers.factory import build_providers
from .routes import build_frontend_routes, build_router

FRONTEND_DIST = Path("frontend/dist")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def create_app(
    settings: Settings | None = None,
    *,
    providers: ProviderSet | None = None,
    runner: JobRunner | None = None,
    dist: Path | None = None,
) -> FastAPI:
    if settings is None:
        load_env_file(Path(".env"))
        settings = load_settings()
    store = JobStore(settings.paths.jobs_dir)
    providers = providers or build_providers(settings)
    runner = runner or JobRunner(store, providers, settings)
    dist = dist or FRONTEND_DIST
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nomadguard-job")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="NomadGuard Ad Studio",
        version="0.1.0",
        description="Brief to social ad: strategy, storyboard, generation and QA.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner
    app.state.pool = pool

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and not _is_loopback_origin(origin):
                return JSONResponse({"detail": "cross-origin mutation refused"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.include_router(build_router(settings, store, runner, pool, providers.video.capabilities))
    app.include_router(build_frontend_routes(dist))
    return app


def _is_loopback_origin(origin: str) -> bool:
    host = urlsplit(origin).hostname
    return host in LOOPBACK_HOSTS


def create_app_from_env() -> FastAPI:
    """Uvicorn factory target, so a config error is reported by the CLI, not a stack."""
    return create_app()
