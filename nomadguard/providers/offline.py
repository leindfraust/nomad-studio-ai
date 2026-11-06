"""Deterministic offline providers.

They exist so the whole workflow, API and UI can be exercised without a key or a
cent. Output is deliberately labelled: a job generated this way is marked
``placeholder`` and cannot be mistaken for the submitted advertisement.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..contracts import (
    ImageRequest,
    ImageResult,
    ProviderTaskRef,
    SceneIntent,
    ShotState,
    Submission,
    TextRequest,
    TextResult,
    VideoCapabilities,
    VideoJobStatus,
    VideoRequest,
    VideoResult,
    VisionRequest,
    VisionResult,
    VoiceRequest,
    VoiceResult,
    utcnow,
)
from ..errors import TaskFailed

OFFLINE_ANGLES = {
    "angles": [
        {
            "name": "The Rebooking Race",
            "target_emotion": "relief",
            "hook": "Your flight just vanished from the board.",
            "concept": "A traveller sprints through an airport while the departure board dissolves into a calm hotel lobby.",
            "cta": "Travel protected with NomadGuard.",
        },
        {
            "name": "The 3AM Call",
            "target_emotion": "security",
            "hook": "It's 3AM. Your connection is cancelled.",
            "concept": "A midnight phone call answered instantly, cutting to a traveller asleep again.",
            "cta": "Someone answers. NomadGuard.",
        },
        {
            "name": "The Cost of Maybe",
            "target_emotion": "confidence",
            "hook": "One delay. Three unexpected bills.",
            "concept": "Receipts stack up on a hotel desk, then one card clears them all.",
            "cta": "Cover the maybe. NomadGuard.",
        },
    ]
}

OFFLINE_SCORES = {
    "scores": [
        {
            "angle_name": "The Rebooking Race",
            "insight_strength": 8,
            "hook_viability": 9,
            "message_fidelity": 8,
            "reason": "Motion plus a vanishing board reads in under two seconds and shows the problem, not the policy.",
        },
        {
            "angle_name": "The 3AM Call",
            "insight_strength": 9,
            "hook_viability": 6,
            "message_fidelity": 8,
            "reason": "Strong emotional insight but a darker, slower opening for a feed.",
        },
        {
            "angle_name": "The Cost of Maybe",
            "insight_strength": 8,
            "hook_viability": 7,
            "message_fidelity": 9,
            "reason": "Financially concrete, but needs reading rather than watching.",
        },
    ],
    "selected_name": "The Rebooking Race",
    "selection_reason": "Highest combined score: movement in the first second, a problem the audience already fears, and the brand promise arrives as a resolution rather than a claim.",
    "audience_insight": "Frequent travellers do not fear delays as much as they fear being stranded alone with the cost and the logistics.",
}

OFFLINE_RUBRIC = {
    "scores": {
        "realism": 8,
        "visual_consistency": 8,
        "brief_adherence": 9,
        "hook_strength": 7,
        "advertising_quality": 8,
        "storyboard_fidelity": 7,
    },
    "justifications": {
        "realism": "Offline placeholder: solid-colour frames, no photographic detail.",
        "visual_consistency": "Identical grade across shots because the source is generated.",
        "brief_adherence": "Copy and beats follow the brief.",
        "hook_strength": "Opening card states the problem within one second.",
        "advertising_quality": "Reads as an animatic, not a finished film.",
        "storyboard_fidelity": "Beats and durations match the storyboard.",
    },
    "defects": ["Offline placeholder footage, not a generated advertisement."],
    "passed": True,
}


OFFLINE_STORYBOARD = {
    "continuity_block": "Same traveller: charcoal wool coat, tan leather holdall, dusk terminal, cool blue glass with warm amber spill, 35mm, consistent grade.",
    "scenes": [
        {
            "beat": "hook",
            "description": "He looks up as the departure board clears his flight.",
            "camera": "slow push in",
            "on_screen_text": "Your flight just vanished.",
            "intent": {
                "subject": "a traveller in a charcoal coat",
                "action": "stops and looks up as the departure board clears",
                "setting": "a modern airport terminal at dusk",
                "camera": "slow push in",
                "wardrobe": "charcoal wool coat, tan leather holdall",
                "light": "cool blue window light with warm cabin spill",
                "palette": "deep navy, warm amber, off-white",
            },
        },
        {
            "beat": "tension",
            "description": "He stands alone with the phone in his hand, the terminal emptying behind him.",
            "camera": "handheld, slight drift",
            "on_screen_text": None,
            "intent": {
                "subject": "the same traveller in a charcoal coat",
                "action": "stands still, phone in hand, jaw tight",
                "setting": "the same terminal, now nearly empty",
                "camera": "handheld, slight drift",
                "wardrobe": "charcoal wool coat, tan leather holdall",
                "light": "cool blue window light with warm cabin spill",
                "palette": "deep navy, warm amber, off-white",
            },
        },
        {
            "beat": "relief",
            "description": "He exhales, shoulders dropping, and walks toward the lit gate.",
            "camera": "pull back to reveal the gate",
            "on_screen_text": "Travel protected.",
            "intent": {
                "subject": "the same traveller in a charcoal coat",
                "action": "exhales and walks toward the lit gate",
                "setting": "the same terminal, gate lights ahead",
                "camera": "pull back to reveal the gate",
                "wardrobe": "charcoal wool coat, tan leather holdall",
                "light": "warmer gate light against cool blue glass",
                "palette": "deep navy, warm amber, off-white",
            },
        },
    ],
}


OFFLINE_VOICEOVER = {
    "first_line": "The gate changed. Your plans did not survive it.",
    "second_line": "NomadGuard keeps the trip on your terms.",
}


class OfflineTextProvider:
    name = "offline"
    model = "offline-text"

    def generate(self, request: TextRequest) -> TextResult:
        if "TASK: SCORES" in request.user:
            payload = OFFLINE_SCORES
        elif "TASK: STORYBOARD" in request.user:
            payload = OFFLINE_STORYBOARD
        elif "TASK: VOICEOVER" in request.user:
            payload = OFFLINE_VOICEOVER
        else:
            payload = OFFLINE_ANGLES
        return TextResult(text=json.dumps(payload), model=self.model, cost_usd=0.0)


class OfflineVisionProvider:
    name = "offline"
    model = "offline-vision"

    def evaluate(self, request: VisionRequest) -> VisionResult:
        return VisionResult(text=json.dumps(OFFLINE_RUBRIC), model=self.model, cost_usd=0.0)


class OfflineImageProvider:
    name = "offline"
    model = "offline-image"

    def __init__(self, ffmpeg: str) -> None:
        self._ffmpeg = ffmpeg

    def generate(self, request: ImageRequest, destination: Path) -> ImageResult:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self._ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=0x0B1220:s=480x854",
                "-frames:v", "1", str(destination),
            ],
            check=True,
        )
        return ImageResult(model=self.model, local_path=str(destination), cost_usd=0.0)


class OfflineVoiceProvider:
    """Silent placeholder track, so the offline path exercises the same mixing code."""

    name = "offline"
    model = "offline-voice"
    voice_id = "offline"

    def __init__(self, ffmpeg: str) -> None:
        self._ffmpeg = ffmpeg

    def synthesize(self, request: VoiceRequest, destination: Path) -> VoiceResult:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        duration = max(1.0, min(12.0, len(request.text) / 14))
        subprocess.run(
            [
                self._ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
                "-t", f"{duration:.2f}", "-c:a", "libmp3lame", "-q:a", "6", str(destination),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return VoiceResult(
            model=self.model,
            voice_id=self.voice_id,
            local_path=str(destination),
            duration_s=round(duration, 2),
            characters=len(request.text),
            cost_usd=0.0,
        )


class OfflineVideoProvider:
    name = "offline"
    model = "offline-video"

    def __init__(self, ffmpeg: str, font_path: Path) -> None:
        self._ffmpeg = ffmpeg
        self._font = font_path

    def capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(
            provider=self.name,
            model=self.model,
            durations_s=list(range(4, 16)),
            ratios=["9:16"],
            resolutions=["480P"],
            supports_first_frame=False,
            supports_reference_image=False,
            max_reference_images=0,
            price_per_second_usd={"480P": 0.0},
            source_url="offline://placeholder",
            verified=True,
        )

    def render_prompt(self, intent: SceneIntent) -> str:
        return f"{intent.subject} {intent.action}; {intent.camera}; {intent.light}"

    def generate(self, request: VideoRequest) -> Submission:
        return Submission(
            provider=self.name,
            model=self.model,
            task_id=f"offline-{utcnow().timestamp():.0f}-{request.duration_s}",
            submitted_at=utcnow(),
            poll_hint_s=0,
        )

    def get_status(self, task: ProviderTaskRef) -> VideoJobStatus:
        return VideoJobStatus(task_id=task.task_id, state=ShotState.DOWNLOADED, raw_state="succeeded")

    def get_result(self, task: ProviderTaskRef, destination: Path) -> VideoResult:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        duration = 4
        font = destination.parent / f"placeholder-font{self._font.suffix or '.ttf'}"
        if not font.is_file():
            font.write_bytes(self._font.read_bytes())
        try:
            subprocess.run(
                [
                    self._ffmpeg, "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"color=c=0x101820:s=480x854:r=24:d={duration}",
                    "-vf",
                    f"drawtext=fontfile={font.name}:text=OFFLINE-PLACEHOLDER:"
                    "fontcolor=white:fontsize=28:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(destination),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=destination.parent,
            )
        except subprocess.CalledProcessError as exc:
            raise TaskFailed(f"offline placeholder render failed: {exc.stderr.strip()[:300]}") from None
        return VideoResult(
            task_id=task.task_id,
            local_path=str(destination),
            provider_url="offline://placeholder",
            duration_s=float(duration),
            resolution="480P",
            reported_cost_usd=0.0,
        )
