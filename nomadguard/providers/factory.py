"""One place that turns configuration into implementations.

Provider names are a closed set. An unknown or removed name raises instead of
falling back to something else that happens to be constructible.
"""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from .base import ProviderSet
from .minimax import (
    MinimaxHttp,
    MinimaxImageProvider,
    MinimaxTextProvider,
    MinimaxVideoProvider,
    MinimaxVisionProvider,
    MinimaxVoiceProvider,
)
from .offline import (
    OfflineImageProvider,
    OfflineTextProvider,
    OfflineVideoProvider,
    OfflineVisionProvider,
    OfflineVoiceProvider,
)
from .openai_compat import OpenAiCompatHttp, OpenAiCompatTextProvider, OpenAiCompatVisionProvider


def build_providers(settings: Settings) -> ProviderSet:
    offline = [
        name for name in ("text", "vision", "image", "video", "voice")
        if getattr(settings, name).provider == "offline"
    ]
    if offline and not settings.allow_placeholder:
        raise ConfigError(
            f"offline providers are selected for: {', '.join(offline)}. "
            "Placeholder output is disabled by default, so nothing is produced. "
            "Set NOMADGUARD_ALLOW_PLACEHOLDER=1 to allow a demo run, "
            "or point those capabilities at a real provider in .env."
        )

    shared = _Shared(settings)

    text = shared.text(settings.text.provider, settings.text.model)
    vision = shared.vision(settings.vision.provider, settings.vision.model)
    image = shared.image(settings.image.provider, settings.image.model)
    video = shared.video(settings.video.provider, settings.video.model)
    voice = shared.voice(settings.voice.provider, settings.voice.model)
    return ProviderSet(text=text, vision=vision, image=image, video=video, voice=voice)


class _Shared:
    """Builds adapters lazily and caches the two HTTP clients."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._minimax: MinimaxHttp | None = None
        self._compat: OpenAiCompatHttp | None = None

    def _minimax_http(self) -> MinimaxHttp:
        if self._minimax is None:
            if self._settings.minimax_api_key is None:
                raise ConfigError("MINIMAX_API_KEY is required for MiniMax providers")
            self._minimax = MinimaxHttp(self._settings.minimax_api_key.get_secret_value())
        return self._minimax

    def _compat_http(self) -> OpenAiCompatHttp:
        if self._compat is None:
            compat = self._settings.openai_compat
            if compat is None:
                raise ConfigError("OPENAI_COMPAT_* settings are required for the openai_compat provider")
            self._compat = OpenAiCompatHttp(
                compat.base_url, compat.api_key.get_secret_value()
            )
        return self._compat

    def text(self, provider: str, model: str):
        if provider == "minimax":
            return MinimaxTextProvider(self._minimax_http(), model)
        if provider == "openai_compat":
            return OpenAiCompatTextProvider(self._compat_http(), model)
        if provider == "offline":
            return OfflineTextProvider()
        raise ConfigError(f"text provider {provider!r} is not implemented")

    def vision(self, provider: str, model: str):
        if provider == "minimax":
            return MinimaxVisionProvider(self._minimax_http(), model)
        if provider == "openai_compat":
            return OpenAiCompatVisionProvider(self._compat_http(), model)
        if provider == "offline":
            return OfflineVisionProvider()
        raise ConfigError(f"vision provider {provider!r} is not implemented")

    def image(self, provider: str, model: str):
        if provider == "minimax":
            return MinimaxImageProvider(self._minimax_http(), model)
        if provider == "offline":
            return OfflineImageProvider(self._settings.paths.ffmpeg)
        raise ConfigError(f"image provider {provider!r} is not implemented")

    def video(self, provider: str, model: str):
        if provider == "minimax":
            return MinimaxVideoProvider(
                self._minimax_http(), model, poll_hint_s=self._settings.policy.poll_interval_s
            )
        if provider == "offline":
            return OfflineVideoProvider(self._settings.paths.ffmpeg, self._settings.paths.font_path)
        raise ConfigError(f"video provider {provider!r} is not implemented")

    def voice(self, provider: str, model: str):
        if provider == "minimax":
            return MinimaxVoiceProvider(self._minimax_http(), model, self._settings.voice_id)
        if provider == "offline":
            return OfflineVoiceProvider(self._settings.paths.ffmpeg)
        if provider == "none":
            return None
        raise ConfigError(f"voice provider {provider!r} is not implemented")
