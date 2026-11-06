"""Capability protocols. The pipeline talks to these, never to a vendor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts import (
    ImageRequest,
    ImageResult,
    ProviderTaskRef,
    SceneIntent,
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
)


@runtime_checkable
class TextProvider(Protocol):
    name: str
    model: str

    def generate(self, request: TextRequest) -> TextResult: ...


@runtime_checkable
class VisionProvider(Protocol):
    name: str
    model: str

    def evaluate(self, request: VisionRequest) -> VisionResult: ...


@runtime_checkable
class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, request: ImageRequest, destination: Path) -> ImageResult: ...


@runtime_checkable
class VoiceProvider(Protocol):
    name: str
    model: str
    voice_id: str

    def synthesize(self, request: VoiceRequest, destination: Path) -> VoiceResult: ...


@runtime_checkable
class VideoProvider(Protocol):
    name: str
    model: str

    def capabilities(self) -> VideoCapabilities: ...

    def render_prompt(self, intent: SceneIntent) -> str: ...

    def generate(self, request: VideoRequest) -> Submission: ...

    def get_status(self, task: ProviderTaskRef) -> VideoJobStatus: ...

    def get_result(self, task: ProviderTaskRef, destination: Path) -> VideoResult: ...


@dataclass(frozen=True)
class ProviderSet:
    text: TextProvider
    vision: VisionProvider
    image: ImageProvider
    video: VideoProvider
    voice: VoiceProvider | None = None
