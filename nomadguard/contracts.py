"""Strict boundary records shared by the pipeline, the API and the job store.

Rules carried from the spec: unknown keys are rejected, unknown enum values raise,
and ``None`` means "not known" rather than "zero" or "empty".
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- inputs -----------------------------------------------------------------


class Brief(StrictModel):
    brand: str = Field(min_length=1)
    category: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    problem: list[str] = Field(min_length=1)
    core_message: str = Field(min_length=1)
    brand_feel: list[str] = Field(min_length=1)
    duration_s: int = Field(ge=10, le=15)
    aspect_ratio: Literal["9:16"] = "9:16"
    cta: str = Field(min_length=1)
    visual_notes: str | None = None


class TextRequest(StrictModel):
    user: str = Field(min_length=1)
    system: str | None = None
    max_tokens: int = Field(gt=0)
    json_object: bool = False


class TextResult(StrictModel):
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class VisionRequest(StrictModel):
    prompt: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)
    system: str | None = None
    max_tokens: int = Field(gt=0)


class VisionResult(StrictModel):
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class ImageRequest(StrictModel):
    prompt: str = Field(min_length=1)
    aspect_ratio: Literal["9:16"] = "9:16"
    subject_reference_path: str | None = None


class ImageResult(StrictModel):
    model: str
    local_path: str
    cost_usd: float | None = None


class VoiceRequest(StrictModel):
    """Text for one spoken block. Pauses use MiniMax's <#seconds#> marker."""

    text: str = Field(min_length=1, max_length=2000)
    voice_id: str = Field(min_length=1)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    emotion: str | None = None
    language: str = "auto"


class VoiceResult(StrictModel):
    model: str
    voice_id: str
    local_path: str
    duration_s: float
    characters: int
    cost_usd: float | None = None


class Voiceover(StrictModel):
    """The spoken track: what is said, when it starts, and what it cost."""

    text: str
    start_s: float = Field(ge=0)
    voice_id: str
    local_path: str
    duration_s: float
    cost_usd: float | None = None


# --- creative plan ----------------------------------------------------------


class Angle(StrictModel):
    name: str = Field(min_length=1)
    target_emotion: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    cta: str = Field(min_length=1)


class AngleScore(StrictModel):
    angle_name: str = Field(min_length=1)
    insight_strength: int = Field(ge=0, le=10)
    hook_viability: int = Field(ge=0, le=10)
    message_fidelity: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)

    @property
    def total(self) -> int:
        return self.insight_strength + self.hook_viability + self.message_fidelity


class Strategy(StrictModel):
    angles: list[Angle] = Field(min_length=2)
    scores: list[AngleScore] = Field(min_length=2)
    selected_name: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    audience_insight: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "Strategy":
        names = {angle.name for angle in self.angles}
        if self.selected_name not in names:
            raise ValueError("selected_name is not one of the angles")
        scored = {score.angle_name for score in self.scores}
        if scored != names:
            raise ValueError("every angle needs exactly one score")
        return self

    @property
    def selected(self) -> Angle:
        return next(angle for angle in self.angles if angle.name == self.selected_name)


class SceneIntent(StrictModel):
    """Vendor-neutral description of one shot. Rendered by the provider adapter."""

    subject: str
    action: str
    setting: str
    camera: str
    wardrobe: str
    light: str
    palette: str


class Scene(StrictModel):
    index: int = Field(ge=0)
    start_s: float = Field(ge=0)
    duration_s: int = Field(ge=4, le=15)
    beat: Literal["hook", "tension", "relief"]
    description: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    on_screen_text: str | None
    intent: SceneIntent
    universal_prompt: str = Field(min_length=1)
    provider_prompt: str = Field(min_length=1)


class Storyboard(StrictModel):
    target_duration_s: int = Field(ge=10, le=15)
    total_duration_s: int = Field(ge=10, le=15)
    aspect_ratio: Literal["9:16"]
    continuity_block: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)

    @model_validator(mode="after")
    def _sums(self) -> "Storyboard":
        if sum(scene.duration_s for scene in self.scenes) != self.total_duration_s:
            raise ValueError("scene durations must sum to total_duration_s")
        return self


# --- provider boundary ------------------------------------------------------


class VideoCapabilities(StrictModel):
    provider: str
    model: str
    durations_s: list[int] = Field(min_length=1)
    ratios: list[str] = Field(min_length=1)
    resolutions: list[str] = Field(min_length=1)
    supports_first_frame: bool
    supports_reference_image: bool
    max_reference_images: int = Field(ge=0)
    price_per_second_usd: dict[str, float]
    source_url: str
    verified: bool


class VideoRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=7000)
    duration_s: int = Field(ge=1, le=60)
    ratio: str
    resolution: str
    first_frame_url: str | None = None
    reference_image_urls: list[str] = Field(default_factory=list)
    seed: int | None = None


class Submission(StrictModel):
    provider: str
    model: str
    task_id: str
    submitted_at: datetime
    poll_hint_s: float


class ProviderTaskRef(StrictModel):
    provider: str
    model: str
    task_id: str


class JobState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    EVALUATING = "EVALUATING"
    FINALIZED = "FINALIZED"
    FAILED = "FAILED"


class ShotState(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class VideoJobStatus(StrictModel):
    task_id: str
    state: ShotState
    raw_state: str
    message: str | None = None


class VideoResult(StrictModel):
    task_id: str
    local_path: str
    provider_url: str
    duration_s: float | None = None
    resolution: str | None = None
    reported_cost_usd: float | None = None


# --- quality ----------------------------------------------------------------


class TechReport(StrictModel):
    file: str
    size_bytes: int
    duration_s: float
    width: int
    height: int
    aspect_ratio: str
    codec: str | None
    pixel_format: str | None
    fps: float | None
    has_audio: bool
    decodable: bool
    failures: list[str]
    valid: bool


class CreativeReport(StrictModel):
    model: str
    scores: dict[str, float]
    justifications: dict[str, str]
    defects: list[str]
    frames: list[str]
    passed: bool


class QAReport(StrictModel):
    technical: TechReport
    creative: CreativeReport | None
    passed: bool
    reasons: list[str]


# --- job record -------------------------------------------------------------


class Attempt(StrictModel):
    at: datetime
    action: str
    outcome: str
    detail: str | None = None


class Costs(StrictModel):
    estimated_usd: float = 0.0
    reported_usd: float | None = None


class ShotRecord(StrictModel):
    index: int = Field(ge=0)
    task: ProviderTaskRef | None = None
    state: ShotState = ShotState.PENDING
    attempts: list[Attempt] = Field(default_factory=list)
    keyframe_path: str | None = None
    local_path: str | None = None
    error: str | None = None


class JobRecord(StrictModel):
    id: str
    state: JobState = JobState.CREATED
    substep: str = "queued"
    revision: int = 0
    dry_run: bool = False
    placeholder: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    brief: Brief
    strategy: Strategy | None = None
    storyboard: Storyboard | None = None
    voiceover: Voiceover | None = None
    shots: list[ShotRecord] = Field(default_factory=list)
    qa: QAReport | None = None
    error: dict[str, object] | None = None
    costs: Costs = Field(default_factory=Costs)
    attempts: list[Attempt] = Field(default_factory=list)

    @property
    def final_video(self) -> str | None:
        path = f"artifacts/final.mp4"
        return path if self.state == JobState.FINALIZED else None
