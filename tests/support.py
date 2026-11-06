"""Test fixtures shared by the backend suites."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from nomadguard.config import Settings
from nomadguard.contracts import (
    Brief,
    ImageRequest,
    ProviderTaskRef,
    SceneIntent,
    TextRequest,
    VideoRequest,
    VisionRequest,
)
from nomadguard.providers.minimax import MinimaxHttp, MinimaxVideoProvider


class FakeTransport:
    """Records calls and replays queued responses.

    A queued item is ``(status, body)``, ``(status, body, headers)`` or an
    exception instance to raise.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body) if body else None,
            }
        )
        if not self.responses:
            raise AssertionError(f"unexpected call to {url}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, payload = item[0], item[1]
        headers_out = item[2] if len(item) > 2 else {}
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, headers_out, raw


def make_settings(tmp: Path, **overrides) -> Settings:
    """Settings built directly, bypassing env parsing (which the tests exercise
    separately). Every value is explicit so no test depends on a hidden default."""

    base = {
        "text": {"provider": "minimax", "model": "MiniMax-M3.1-Flash"},
        "vision": {"provider": "minimax", "model": "MiniMax-M3.1-Flash"},
        "image": {"provider": "minimax", "model": "image-01"},
        "video": {"provider": "minimax", "model": "MiniMax-H3-Lite"},
        "voice": {"provider": "minimax", "model": "speech-2.8-turbo"},
        "video_resolution": "480P",
        "voice_id": "English_Persuasive_Man",
        "voice_start_s": 0.6,
        "allow_placeholder": True,
        "minimax_api_key": "test-key",
        "openai_compat": None,
        "policy": {
            "retry_max_attempts": 3,
            "retry_backoff_s": 0,
            "poll_interval_s": 0.01,
            "staleness_s": 600,
            "qa_min_mean": 7,
            "qa_min_dimension": 6,
            "duration_tolerance_s": 0.5,
            "aspect_tolerance": 0.03,
            "resolution_floor_short_edge": 480,
        },
        "paths": {
            "jobs_dir": str(tmp / "jobs"),
            "brief_path": str(tmp / "briefs" / "nomadguard.json"),
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "font_path": str(_font()),
            "symbol_font": None,
        },
    }
    base.update(overrides)
    return Settings(**base)


def _font() -> Path:
    candidate = Path("C:/Windows/Fonts/arialbd.ttf")
    if candidate.is_file():
        return candidate
    handle = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
    handle.write(b"not-a-real-font")
    handle.close()
    return Path(handle.name)


def valid_env(tmp: Path) -> dict[str, str]:
    return {
        "NOMADGUARD_TEXT_PROVIDER": "minimax",
        "NOMADGUARD_TEXT_MODEL": "MiniMax-M3.1-Flash",
        "NOMADGUARD_VISION_PROVIDER": "minimax",
        "NOMADGUARD_VISION_MODEL": "MiniMax-M3.1-Flash",
        "NOMADGUARD_IMAGE_PROVIDER": "minimax",
        "NOMADGUARD_IMAGE_MODEL": "image-01",
        "NOMADGUARD_VIDEO_PROVIDER": "minimax",
        "NOMADGUARD_VIDEO_MODEL": "MiniMax-H3-Lite",
        "NOMADGUARD_VIDEO_RESOLUTION": "480P",
        "NOMADGUARD_VOICE_PROVIDER": "minimax",
        "NOMADGUARD_VOICE_MODEL": "speech-2.8-turbo",
        "NOMADGUARD_VOICE_ID": "English_Persuasive_Man",
        "MINIMAX_API_KEY": "test-key",
        "NOMADGUARD_RETRY_MAX_ATTEMPTS": "3",
        "NOMADGUARD_RETRY_BACKOFF_S": "5",
        "NOMADGUARD_POLL_INTERVAL_S": "10",
        "NOMADGUARD_STALENESS_S": "600",
        "NOMADGUARD_QA_MIN_MEAN": "7",
        "NOMADGUARD_QA_MIN_DIMENSION": "6",
        "NOMADGUARD_DURATION_TOLERANCE_S": "0.5",
        "NOMADGUARD_ASPECT_TOLERANCE": "0.03",
        "NOMADGUARD_RESOLUTION_FLOOR_SHORT_EDGE": "480",
        "NOMADGUARD_JOBS_DIR": str(tmp / "jobs"),
        "NOMADGUARD_FFMPEG": "ffmpeg",
        "NOMADGUARD_FFPROBE": "ffprobe",
        "NOMADGUARD_FONT_PATH": str(_font()),
    }


def brief_dict() -> dict:
    return {
        "brand": "NomadGuard",
        "category": "travel protection",
        "audience": "frequent travellers aged 25-40",
        "problem": ["flight delays", "cancellations", "unexpected costs"],
        "core_message": "Travel with confidence knowing that you are protected when things go wrong.",
        "brand_feel": ["modern", "premium", "trustworthy", "confident", "polished"],
        "duration_s": 12,
        "aspect_ratio": "9:16",
        "cta": "Travel protected with NomadGuard.",
        "visual_notes": "Airport at dusk, warm cabin light against cool blue glass.",
    }


def brief() -> Brief:
    return Brief(**brief_dict())


def scene_intent() -> SceneIntent:
    return SceneIntent(
        subject="a traveller in a charcoal coat",
        action="looks up as the departure board flickers",
        setting="a modern airport terminal at dusk",
        camera="slow push in",
        wardrobe="charcoal wool coat, tan leather holdall",
        light="cool blue window light with warm cabin spill",
        palette="deep navy, warm amber, off-white",
    )


def video_request(**overrides) -> VideoRequest:
    base = {
        "prompt": "test prompt",
        "duration_s": 4,
        "ratio": "9:16",
        "resolution": "480P",
    }
    base.update(overrides)
    return VideoRequest(**base)


def video_provider(transport: FakeTransport) -> MinimaxVideoProvider:
    return MinimaxVideoProvider(
        MinimaxHttp("test-key", transport=transport), "MiniMax-H3-Lite", poll_hint_s=10
    )


def task_ref(task_id: str = "task-1") -> ProviderTaskRef:
    return ProviderTaskRef(provider="minimax", model="MiniMax-H3-Lite", task_id=task_id)


def make_storyboard(durations: list[int] | None = None) -> "Storyboard":
    """A hand-built storyboard for tests that need one without a model call."""
    from nomadguard.contracts import Scene, Storyboard
    from nomadguard.pipeline.prompts import universal_prompt

    durations = durations or [4, 4, 4]
    beats = ["hook", "tension", "relief"] if len(durations) == 3 else ["hook", "relief"]
    scenes: list[Scene] = []
    start = 0.0
    for index, (duration, beat) in enumerate(zip(durations, beats)):
        intent = scene_intent()
        scenes.append(
            Scene(
                index=index,
                start_s=start,
                duration_s=duration,
                beat=beat,  # type: ignore[arg-type]
                description=f"scene {index}: the traveller reacts to the delay",
                camera=intent.camera,
                on_screen_text="Your flight just vanished." if index == 0 else None,
                intent=intent,
                universal_prompt=universal_prompt(intent, "same coat, same terminal, dusk"),
                provider_prompt=f"{intent.subject} {intent.action} [{intent.camera}]",
            )
        )
        start += duration
    return Storyboard(
        target_duration_s=sum(durations),
        total_duration_s=sum(durations),
        aspect_ratio="9:16",
        continuity_block="charcoal coat, tan holdall, dusk terminal, cool blue with warm amber",
        scenes=scenes,
    )
