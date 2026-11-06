"""The state machine. One implementation behind the CLI and the API.

Every transition is persisted before the next call starts, so a killed process
leaves a resumable record rather than a half-remembered run.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path

from ..config import Settings
from ..contracts import (
    Attempt,
    ImageRequest,
    JobRecord,
    JobState,
    ProviderTaskRef,
    QAReport,
    ShotRecord,
    ShotState,
    VideoRequest,
    utcnow,
)
from ..errors import NomadGuardError, ProviderError, QAFailed, TaskFailed
from ..pipeline.assemble import assemble
from ..pipeline.qa_creative import CreativeQAError, evaluate as evaluate_creative, gate_reasons
from ..pipeline.qa_technical import extract_frames, inspect
from ..pipeline.storyboard import build_storyboard
from ..pipeline.strategy import build_strategy
from ..pipeline.voiceover import write_voiceover
from ..providers.base import ProviderSet
from .store import JobStore


class JobRunner:
    def __init__(
        self,
        store: JobStore,
        providers: ProviderSet,
        settings: Settings,
        *,
        sleep=time.sleep,
    ) -> None:
        self._store = store
        self._providers = providers
        self._settings = settings
        self._sleep = sleep

    def run(self, job_id: str, *, claimed: bool = False, retry_failed: bool = False) -> JobRecord:
        if not claimed:
            self._store.claim(job_id)
        record = self._store.get(job_id)
        try:
            if record.state == JobState.FINALIZED:
                return record
            if record.state == JobState.FAILED:
                if not retry_failed:
                    return record
                # Explicit resume: clear the failure and re-enter the machine. Completed
                # artifacts (strategy, storyboard, keyframes, finished shots) are kept.
                record.error = None
                record.state = JobState.CREATED
                record.substep = "resuming"
                record.attempts.append(
                    Attempt(at=utcnow(), action="job", outcome="resumed", detail="explicit resume")
                )
                self._save(record)
            record.placeholder = self._providers.video.name == "offline"
            self._advance(record)
        except NomadGuardError as exc:
            self._fail(record, exc)
        except Exception as exc:  # a bug must still leave a readable job, not a traceback
            self._fail(record, NomadGuardError(f"{type(exc).__name__}: {exc}"))
        finally:
            self._store.release(job_id)
        return self._store.get(job_id)

    # --- phases ------------------------------------------------------------
    def _advance(self, record: JobRecord) -> None:
        self._plan(record)
        if record.dry_run:
            self._set(record, JobState.PLANNING, "planned")
            return
        self._generate(record)
        self._evaluate(record)

    def _plan(self, record: JobRecord) -> None:
        self._set(record, JobState.PLANNING, "strategy")
        if record.strategy is None:
            strategy, cost = build_strategy(record.brief, self._providers.text)
            record.strategy = strategy
            record.costs.estimated_usd = round(record.costs.estimated_usd + cost, 6)
            self._write(record, "strategy.json", strategy.model_dump(mode="json"))
            self._save(record)

        self._set(record, JobState.PLANNING, "storyboard")
        if record.storyboard is None:
            caps = self._providers.video.capabilities()
            storyboard, cost = build_storyboard(
                record.brief, record.strategy, caps, self._providers.video, self._providers.text
            )
            record.storyboard = storyboard
            record.shots = [ShotRecord(index=scene.index) for scene in storyboard.scenes]
            record.costs.estimated_usd = round(record.costs.estimated_usd + cost, 6)
            self._write(record, "storyboard.json", storyboard.model_dump(mode="json"))
            self._save(record)

    def _generate(self, record: JobRecord) -> None:
        assert record.storyboard is not None
        self._set(record, JobState.GENERATING, "keyframes")
        self._keyframes(record)

        for scene, shot in zip(record.storyboard.scenes, record.shots):
            self._set(record, JobState.GENERATING, f"shot-{scene.index}")
            self._shot(record, scene, shot)

        if self._providers.voice is not None and record.voiceover is None and record.strategy:
            self._set(record, JobState.GENERATING, "voiceover")
            voiceover, cost = write_voiceover(
                record.brief,
                record.strategy,
                self._providers.text,
                self._providers.voice,
                start_s=self._settings.voice_start_s,
                destination=self._store.artifact_dir(record.id) / "voiceover.mp3",
            )
            record.voiceover = voiceover
            record.costs.estimated_usd = round(record.costs.estimated_usd + cost, 6)
            self._save(record)

        self._set(record, JobState.GENERATING, "assembling")
        paths = [Path(shot.local_path) for shot in record.shots if shot.local_path]
        final = self._store.artifact_dir(record.id) / "final.mp4"
        voice_path = Path(record.voiceover.local_path) if record.voiceover else None
        assemble(
            record.storyboard,
            paths,
            final,
            ffmpeg=self._settings.paths.ffmpeg,
            ffprobe=self._settings.paths.ffprobe,
            font_path=self._settings.paths.font_path,
            brand=record.brief.brand,
            cta=record.brief.cta,
            work_dir=self._store.artifact_dir(record.id) / "assembly",
            voiceover=voice_path,
            voice_start_s=record.voiceover.start_s if record.voiceover else 0.0,
            symbol_font=self._settings.paths.symbol_font,
        )
        self._save(record)

    def _keyframes(self, record: JobRecord) -> None:
        assert record.storyboard is not None
        caps = self._providers.video.capabilities()
        if not caps.supports_first_frame:
            return
        hero: Path | None = None
        directory = self._store.artifact_dir(record.id) / "keyframes"
        directory.mkdir(parents=True, exist_ok=True)
        for scene, shot in zip(record.storyboard.scenes, record.shots):
            if shot.keyframe_path and Path(shot.keyframe_path).is_file():
                hero = hero or Path(shot.keyframe_path)
                continue
            destination = directory / f"scene-{scene.index}.png"
            prompt = (
                f"{scene.description} {record.storyboard.continuity_block} "
                "Vertical 9:16 photographic still frame for a premium travel brand. "
                "No on-screen text, no logos, no captions."
            )
            result = self._retry(
                record,
                lambda: self._providers.image.generate(
                    ImageRequest(prompt=prompt, subject_reference_path=str(hero) if hero else None),
                    destination,
                ),
                action=f"image.scene-{scene.index}",
            )
            shot.keyframe_path = result.local_path
            hero = hero or Path(result.local_path)
            record.costs.estimated_usd = round(
                record.costs.estimated_usd + (result.cost_usd or 0.0), 6
            )
            self._save(record)

    def _shot(self, record: JobRecord, scene, shot: ShotRecord) -> None:
        assert record.storyboard is not None
        caps = self._providers.video.capabilities()
        if shot.state == ShotState.DOWNLOADED and shot.local_path and Path(shot.local_path).is_file():
            return

        destination = self._store.artifact_dir(record.id) / f"shot-{scene.index}.mp4"
        first_frame = None
        reference_images: list[str] = []
        hero = record.shots[0].keyframe_path
        if scene.index == 0 and caps.supports_first_frame and shot.keyframe_path:
            # Shot 1 keeps its composed opening frame.
            first_frame = _data_uri(Path(shot.keyframe_path))
        elif caps.supports_reference_image and hero and Path(hero).is_file():
            # Later shots get the hero frame as an identity reference, not as a
            # frozen first frame: same person, wardrobe and bag, new composition.
            reference_images = [_data_uri(Path(hero))]

        if shot.task is None:
            request = VideoRequest(
                prompt=scene.provider_prompt,
                duration_s=scene.duration_s,
                ratio=record.storyboard.aspect_ratio,
                resolution=self._settings.video_resolution,
                first_frame_url=first_frame,
                reference_image_urls=reference_images,
            )
            submission = self._retry(
                record,
                lambda: self._providers.video.generate(request),
                action=f"video.create.{scene.index}",
            )
            shot.task = ProviderTaskRef(
                provider=submission.provider, model=submission.model, task_id=submission.task_id
            )
            shot.state = ShotState.QUEUED
            price = caps.price_per_second_usd.get(self._settings.video_resolution)
            if price is not None:
                record.costs.estimated_usd = round(
                    record.costs.estimated_usd + price * scene.duration_s, 6
                )
            self._save(record)

        assert shot.task is not None
        self._poll(record, scene.index, shot)

        result = self._retry(
            record,
            lambda: self._providers.video.get_result(shot.task, destination),  # type: ignore[arg-type]
            action=f"video.download.{scene.index}",
        )
        shot.local_path = result.local_path
        record.costs.reported_usd = _add_optional(record.costs.reported_usd, result.reported_cost_usd)
        self._save(record)

    def _poll(self, record: JobRecord, index: int, shot: ShotRecord) -> None:
        assert shot.task is not None
        while True:
            status = self._retry(
                record,
                lambda: self._providers.video.get_status(shot.task),  # type: ignore[arg-type]
                action=f"video.status.{index}",
            )
            shot.state = status.state
            self._save(record)
            if status.state == ShotState.DOWNLOADED:
                return
            if status.state == ShotState.FAILED:
                shot.error = status.message or f"provider reported {status.raw_state}"
                self._save(record)
                raise TaskFailed(
                    f"shot {index} failed at the provider: {shot.error}. "
                    "A new generation costs money, so it is not automatic; rerun when you authorize it."
                )
            self._sleep(self._settings.policy.poll_interval_s)

    def _evaluate(self, record: JobRecord) -> None:
        assert record.storyboard is not None
        self._set(record, JobState.EVALUATING, "technical")
        artifacts = self._store.artifact_dir(record.id)
        video_path = artifacts / "final.mp4"
        technical = inspect(
            video_path,
            ffmpeg=self._settings.paths.ffmpeg,
            ffprobe=self._settings.paths.ffprobe,
            policy=self._settings.policy,
            expect_audio=record.voiceover is not None,
        )
        timestamps = []
        for scene in record.storyboard.scenes:
            timestamps.append(scene.start_s + 0.5)
            timestamps.append(scene.start_s + scene.duration_s / 2)
        if technical.duration_s > 2:
            # The end card is part of the ad; not sampling it hides the brand from QA.
            timestamps.append(round(technical.duration_s - 1.0, 2))
        frames = extract_frames(
            video_path, timestamps, artifacts / "qa-frames", ffmpeg=self._settings.paths.ffmpeg
        )
        creative = None
        if frames:
            try:
                creative, creative_cost = evaluate_creative(
                    frames,
                    brief=record.brief,
                    storyboard=record.storyboard,
                    vision=self._providers.vision,
                    policy=self._settings.policy,
                    repair=self._providers.text,
                )
                record.costs.estimated_usd = round(record.costs.estimated_usd + creative_cost, 6)
            except CreativeQAError as exc:
                reasons = [f"creative QA unavailable: {exc}"]
                record.qa = QAReport(
                    technical=technical, creative=None, passed=False, reasons=reasons
                )
                self._save(record)
                raise QAFailed("; ".join(reasons)) from None

        reasons: list[str] = []
        if not technical.valid:
            reasons.extend(technical.failures)
        if creative is not None:
            reasons.extend(gate_reasons(creative, self._settings.policy))
        passed = technical.valid and creative is not None and creative.passed and not reasons
        record.qa = QAReport(technical=technical, creative=creative, passed=passed, reasons=reasons)
        self._write(record, "qa.json", record.qa.model_dump(mode="json"))
        self._save(record)
        if not passed:
            raise QAFailed("; ".join(reasons) or "quality gate did not pass")
        self._set(record, JobState.FINALIZED, "done")

    # --- helpers -----------------------------------------------------------
    def _set(self, record: JobRecord, state: JobState, substep: str) -> None:
        record.state = state
        record.substep = substep
        self._save(record)

    def _save(self, record: JobRecord) -> None:
        self._store.save(record)

    def _write(self, record: JobRecord, name: str, payload: dict) -> None:
        path = self._store.artifact_dir(record.id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _retry(self, record: JobRecord, call, *, action: str):
        attempts = self._settings.policy.retry_max_attempts
        for attempt in range(1, attempts + 1):
            try:
                return call()
            except ProviderError as exc:
                record.attempts.append(
                    Attempt(
                        at=utcnow(),
                        action=action,
                        outcome="error" if attempt == attempts or not exc.retryable else "retry",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
                self._save(record)
                if not exc.retryable or attempt == attempts:
                    raise
                delay = getattr(exc, "retry_after_s", None)
                self._sleep(delay if delay is not None else self._settings.policy.retry_backoff_s * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def _fail(self, record: JobRecord, exc: NomadGuardError) -> None:
        record.state = JobState.FAILED
        record.substep = "failed"
        record.error = (
            exc.as_dict()
            if isinstance(exc, ProviderError)
            else {"class": type(exc).__name__, "message": str(exc), "retryable": False}
        )
        raw = getattr(exc, "raw", None)
        if isinstance(raw, str) and raw.strip():
            path = self._store.artifact_dir(record.id) / "raw-model-output.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding="utf-8")
        record.attempts.append(
            Attempt(at=utcnow(), action="job", outcome="failed", detail=type(exc).__name__)
        )
        self._save(record)


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _add_optional(total: float | None, value: float | None) -> float | None:
    if value is None:
        return total
    return round((total or 0.0) + value, 6)
