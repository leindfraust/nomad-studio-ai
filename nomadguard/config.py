"""Configuration parsed once, validated once, reported in full.

Execution-policy numbers have no defaults here on purpose: the design doc marks
them as proposals, so they arrive from `.env` where a human can see and change
them. A missing value fails loudly instead of running on an unapproved number.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .errors import ConfigError

CAPABILITIES = ("text", "vision", "image", "video", "voice")
PROVIDERS = {
    "text": {"minimax", "openai_compat", "offline"},
    "vision": {"minimax", "openai_compat", "offline"},
    "image": {"minimax", "offline"},
    "video": {"minimax", "offline"},
    "voice": {"minimax", "offline", "none"},
}


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    retry_max_attempts: int = Field(ge=1)
    retry_backoff_s: float = Field(ge=0)
    poll_interval_s: float = Field(gt=0)
    staleness_s: float = Field(gt=0)
    qa_min_mean: float = Field(ge=0, le=10)
    qa_min_dimension: int = Field(ge=0, le=10)
    duration_tolerance_s: float = Field(ge=0)
    aspect_tolerance: float = Field(ge=0, le=1)
    resolution_floor_short_edge: int = Field(ge=1)


class Paths(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    jobs_dir: Path
    brief_path: Path
    ffmpeg: str
    ffprobe: str
    font_path: Path
    symbol_font: Path | None


class OpenAiCompatSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str
    api_key: SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: Binding
    vision: Binding
    image: Binding
    video: Binding
    voice: Binding
    video_resolution: str
    voice_id: str
    voice_start_s: float
    allow_placeholder: bool
    minimax_api_key: SecretStr | None
    openai_compat: OpenAiCompatSettings | None
    policy: Policy
    paths: Paths

    def public(self) -> dict[str, object]:
        """Everything the API may return. Credentials never leave the process."""
        return {
            "capabilities": {
                name: {"provider": getattr(self, name).provider, "model": getattr(self, name).model}
                for name in CAPABILITIES
            },
            "video_resolution": self.video_resolution,
            "policy": self.policy.model_dump(),
        }


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE pairs into the process environment.

    The file wins over an inherited variable. A stale `NOMADGUARD_*` export from an
    earlier shell silently outranking the project's own `.env` is exactly how a live
    configuration turns into an offline demo without anyone noticing.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


def _symbol_font(configured: str) -> Path | None:
    """A font with a check glyph. Optional: without it the product plate is text only."""
    if configured:
        candidate = Path(configured)
        if not candidate.is_file():
            raise ConfigError(f"NOMADGUARD_SYMBOL_FONT does not exist: {candidate}")
        return candidate
    for candidate in (
        Path("C:/Windows/Fonts/seguisym.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Apple Symbols.ttf"),
    ):
        if candidate.is_file():
            return candidate
    return None


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = dict(os.environ if env is None else env)
    missing: list[str] = []

    def required(name: str) -> str:
        value = source.get(name, "").strip()
        if not value:
            missing.append(name)
        return value

    def number(name: str, cast: type[float] | type[int]) -> float | int:
        raw = required(name)
        if not raw:
            return 0  # placeholder; the missing list is what fails
        try:
            return cast(float(raw)) if cast is int else float(raw)
        except ValueError:
            raise ConfigError(f"{name} must be numeric, got {raw!r}") from None

    bindings: dict[str, Binding] = {}
    for capability in CAPABILITIES:
        provider = required(f"NOMADGUARD_{capability.upper()}_PROVIDER")
        model = required(f"NOMADGUARD_{capability.upper()}_MODEL")
        if provider and provider not in PROVIDERS[capability]:
            raise ConfigError(
                f"NOMADGUARD_{capability.upper()}_PROVIDER={provider!r} is not implemented; "
                f"choose one of {sorted(PROVIDERS[capability])}"
            )
        bindings[capability] = Binding(provider=provider or "unset", model=model or "unset")

    video_resolution = required("NOMADGUARD_VIDEO_RESOLUTION")
    voice_id = source.get("NOMADGUARD_VOICE_ID", "English_Persuasive_Man").strip()
    voice_start_s = float(source.get("NOMADGUARD_VOICE_START_S", "0.6"))
    policy = Policy(
        retry_max_attempts=number("NOMADGUARD_RETRY_MAX_ATTEMPTS", int),
        retry_backoff_s=number("NOMADGUARD_RETRY_BACKOFF_S", float),
        poll_interval_s=number("NOMADGUARD_POLL_INTERVAL_S", float),
        staleness_s=number("NOMADGUARD_STALENESS_S", float),
        qa_min_mean=number("NOMADGUARD_QA_MIN_MEAN", float),
        qa_min_dimension=number("NOMADGUARD_QA_MIN_DIMENSION", int),
        duration_tolerance_s=number("NOMADGUARD_DURATION_TOLERANCE_S", float),
        aspect_tolerance=number("NOMADGUARD_ASPECT_TOLERANCE", float),
        resolution_floor_short_edge=number("NOMADGUARD_RESOLUTION_FLOOR_SHORT_EDGE", int),
    ) if not missing else None

    paths = Paths(
        jobs_dir=Path(source.get("NOMADGUARD_JOBS_DIR", "jobs")),
        brief_path=Path(source.get("NOMADGUARD_BRIEF_PATH", "briefs/nomadguard.json")),
        ffmpeg=source.get("NOMADGUARD_FFMPEG", "ffmpeg"),
        ffprobe=source.get("NOMADGUARD_FFPROBE", "ffprobe"),
        font_path=Path(source.get("NOMADGUARD_FONT_PATH", "")),
        symbol_font=_symbol_font(source.get("NOMADGUARD_SYMBOL_FONT", "").strip()),
    )

    used = {bindings[c].provider for c in CAPABILITIES}
    minimax_key = source.get("MINIMAX_API_KEY", "").strip()
    if "minimax" in used and not minimax_key:
        missing.append("MINIMAX_API_KEY")

    openai_compat = None
    if "openai_compat" in used:
        openai_compat = OpenAiCompatSettings(
            base_url=required("OPENAI_COMPAT_BASE_URL"),
            api_key=SecretStr(required("OPENAI_COMPAT_API_KEY")),
        )

    if missing:
        raise ConfigError(
            "Configuration is incomplete. Copy .env.example to .env and set: "
            + ", ".join(sorted(set(missing)))
        )
    if not paths.font_path.is_file():
        raise ConfigError(
            f"NOMADGUARD_FONT_PATH does not exist: {paths.font_path}. The end card needs a bold TTF."
        )
    assert policy is not None  # narrowed by the missing check above

    return Settings(
        **bindings,
        video_resolution=video_resolution,
        voice_id=voice_id,
        voice_start_s=voice_start_s,
        allow_placeholder=source.get("NOMADGUARD_ALLOW_PLACEHOLDER", "").strip() == "1",
        minimax_api_key=SecretStr(minimax_key) if minimax_key else None,
        openai_compat=openai_compat,
        policy=policy,
        paths=paths,
    )
