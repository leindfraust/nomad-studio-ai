"""HTTP surface. Thin: validate, hand to the runner, serialize the record."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..contracts import Brief, JobRecord, JobState
from ..errors import JobBusy
from ..jobs.runner import JobRunner
from ..jobs.store import JobStore


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: Brief
    dry_run: bool = False


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: JobRecord
    stalled: bool


def build_router(
    settings: Settings,
    store: JobStore,
    runner: JobRunner,
    pool: ThreadPoolExecutor,
    capabilities,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def submit(job_id: str, *, claimed: bool = False) -> None:
        pool.submit(runner.run, job_id, claimed=claimed, retry_failed=True)

    def summarise(record: JobRecord) -> JobSummary:
        return JobSummary(record=record, stalled=store.is_stalled(record, settings.policy.staleness_s))

    @router.get("/providers")
    def providers() -> dict:
        caps = capabilities()
        return {
            **settings.public(),
            "video": {
                "model": caps.model,
                "durations_s": [caps.durations_s[0], caps.durations_s[-1]],
                "ratios": caps.ratios,
                "resolutions": caps.resolutions,
                "supports_first_frame": caps.supports_first_frame,
                "supports_reference_image": caps.supports_reference_image,
                "price_per_second_usd": caps.price_per_second_usd,
                "source_url": caps.source_url,
                "verified": caps.verified,
            },
        }

    @router.get("/brief")
    def get_brief() -> Brief:
        if not settings.paths.brief_path.is_file():
            raise HTTPException(status_code=404, detail=f"no brief at {settings.paths.brief_path}")
        return Brief.model_validate_json(settings.paths.brief_path.read_text(encoding="utf-8"))

    @router.put("/brief")
    def put_brief(brief: Brief) -> Brief:
        settings.paths.brief_path.parent.mkdir(parents=True, exist_ok=True)
        settings.paths.brief_path.write_text(
            json.dumps(brief.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return brief

    @router.get("/jobs")
    def list_jobs() -> list[JobSummary]:
        return [summarise(record) for record in store.list()]

    @router.post("/jobs", status_code=202)
    def create_job(payload: CreateJob) -> dict:
        record = store.create(payload.brief, dry_run=payload.dry_run)
        submit(record.id)
        return {"id": record.id, "state": record.state}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> JobSummary:
        try:
            return summarise(store.get(job_id))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}") from None

    @router.post("/jobs/{job_id}/resume", status_code=202)
    def resume_job(job_id: str) -> dict:
        if not store.exists(job_id):
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
        try:
            store.claim(job_id)  # conflict is decided here, not inside a worker thread
        except JobBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        submit(job_id, claimed=True)
        return {"id": job_id, "state": "resuming"}

    @router.post("/jobs/{job_id}/produce", status_code=202)
    def produce_job(job_id: str) -> dict:
        """Turn a planned job into a produced one.

        A dry run stops after the storyboard on purpose. Producing it later reuses
        the strategy, storyboard and keyframes already paid for instead of redoing
        the planning stage.
        """
        if not store.exists(job_id):
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
        record = store.get(job_id)
        if record.dry_run:
            record.dry_run = False
            record.substep = "queued"
            record.error = None
            store.save(record)
        try:
            store.claim(job_id)
        except JobBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        submit(job_id, claimed=True)
        return {"id": job_id, "state": "producing"}

    @router.get("/jobs/{job_id}/events")
    async def events(job_id: str, request: Request) -> StreamingResponse:
        if not store.exists(job_id):
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")

        async def stream():
            last_revision = -1
            while True:
                if await request.is_disconnected():
                    return
                record = store.get(job_id)
                if record.revision != last_revision:
                    last_revision = record.revision
                    payload = summarise(record).model_dump(mode="json")
                    yield f"data: {json.dumps(payload)}\n\n"
                if record.state in (JobState.FINALIZED, JobState.FAILED):
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.get("/jobs/{job_id}/files/{path:path}")
    def artifact(job_id: str, path: str) -> FileResponse:
        try:
            resolved = store.resolve_artifact(job_id, path)
        except PermissionError:
            raise HTTPException(status_code=403, detail="path is outside this job") from None
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(resolved, headers={"Cache-Control": "no-store"})

    return router


def build_frontend_routes(dist: Path) -> APIRouter:
    """Serve the built SPA without letting it swallow API 404s."""
    router = APIRouter()

    @router.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse({"detail": "not found"}, status_code=404)
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(
                "<h1>NomadGuard Ad Studio</h1><p>The frontend is not built. Run "
                "<code>npm --prefix frontend ci &amp;&amp; npm --prefix frontend run build</code>, "
                "or use the CLI.</p><p><a href='/docs'>API docs</a></p>",
                status_code=200,
            )
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)

    return router
