"""MiniMax adapter: the only file that knows MiniMax's wire format.

Everything vendor-specific lives here: auth header, endpoints, task states,
prompt dialect, capability table and error bodies. The pipeline sees the
protocols in ``base.py`` and nothing else.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

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
from ..errors import (
    AmbiguousResponse,
    CapabilityMismatch,
    ConfigError,
    InsufficientBalance,
    InvalidRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SafetyRefusal,
    SubmissionUnknown,
    TaskFailed,
    TransportFailure,
    error_for_status,
)

DOCS = "https://platform.minimax.io/docs"

# Published pay-as-you-go rates, USD per second of output, by resolution.
# Source: https://platform.minimax.io/docs/pricing/overview.md (video section).
VIDEO_CATALOG: dict[str, dict[str, object]] = {
    "MiniMax-H3": {"resolutions": ("768P", "2K"), "min_s": 4, "prices": {"768P": 0.08, "2K": 0.13}},
    "MiniMax-H3-Highspeed": {"resolutions": ("480P", "768P"), "min_s": 4, "prices": {"480P": 0.04, "768P": 0.06}},
    "MiniMax-H3-Lite": {"resolutions": ("480P", "768P"), "min_s": 4, "prices": {"480P": 0.025, "768P": 0.04}},
    "MiniMax-H3-Max": {"resolutions": ("480P", "768P"), "min_s": 5, "prices": {"480P": 0.05, "768P": 0.08}},
}
RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MAX_DURATION_S = 15

# USD per million tokens (input, output). Source: pricing/overview.md (LLM section).
TEXT_PRICES: dict[str, tuple[float, float]] = {
    "MiniMax-M3.1-Flash": (0.15, 0.60),
    "MiniMax-M3": (0.30, 1.20),
}
IMAGE_MODELS = {"image-01": 0.0035}

_STATUS_MAP = {
    "queued": ShotState.QUEUED,
    "running": ShotState.RUNNING,
    "succeeded": ShotState.DOWNLOADED,
    "failed": ShotState.FAILED,
    "cancelled": ShotState.FAILED,
}


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]: ...


class UrllibTransport:
    """Default transport. Returns the status even for 4xx/5xx; raises only when
    no HTTP response exists at all."""

    def __call__(self, method, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportFailure(f"network failure: {exc}") from exc


class MinimaxHttp:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.minimax.io",
        timeout_s: float = 180.0,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport: Transport = transport or UrllibTransport()

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, response_headers, raw = self._call(method, f"{self._base_url}{path}", headers, body)
        if status >= 400:
            message, request_id = _error_detail(raw)
            retry_after = _retry_after(response_headers)
            raise error_for_status(
                status,
                provider_message=message,
                request_id=request_id,
                retry_after_s=retry_after,
            )
        if not raw:
            raise AmbiguousResponse(f"empty response body from {path}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AmbiguousResponse(f"non-JSON response from {path}: {exc}") from None

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        """One transport call, with an OS-level failure normalised so callers only
        need to handle ``NomadGuardError``."""
        try:
            return self._transport(method, url, headers, body, self._timeout_s)
        except (OSError, TimeoutError) as exc:
            raise TransportFailure(f"network failure: {exc}") from exc

    def download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        status, _, raw = self._call("GET", url, headers, None)
        if status >= 400:
            message, request_id = _error_detail(raw)
            raise error_for_status(status, provider_message=message, request_id=request_id)
        if not raw:
            raise ProviderUnavailable(f"empty download from {url}")
        destination.write_bytes(raw)


def _error_detail(raw: bytes) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:500] or None, None
    if not isinstance(payload, dict):
        return None, None
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or ""), payload.get("request_id")
    return None, payload.get("request_id")


def _retry_after(headers: dict[str, str]) -> float | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                return None
    return None


class _ChatVisionMixin:
    """Shared chat-completions call used by text and vision."""

    name = "minimax"

    def __init__(self, http: MinimaxHttp, model: str) -> None:
        if model not in TEXT_PRICES:
            raise ConfigError(
                f"{model!r} is not a known MiniMax text model. Known: {sorted(TEXT_PRICES)}"
            )
        self._http = http
        self.model = model

    def _chat(self, messages: list[dict], max_tokens: int, json_object: bool) -> tuple[str, int | None, int | None]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "reasoning_effort": "low",
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        data = self._http.request("POST", "/v1/chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("MiniMax returned no choices")
        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            raise ProviderError("MiniMax returned an empty message (check model permissions)")
        usage = data.get("usage") or {}
        return content, usage.get("prompt_tokens"), usage.get("completion_tokens")

    def _cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        input_rate, output_rate = TEXT_PRICES[self.model]
        return round(input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate, 6)


class MinimaxTextProvider(_ChatVisionMixin):
    def generate(self, request: TextRequest) -> TextResult:
        messages: list[dict] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.user})
        text, input_tokens, output_tokens = self._chat(messages, request.max_tokens, request.json_object)
        return TextResult(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._cost(input_tokens, output_tokens),
        )


class MinimaxVisionProvider(_ChatVisionMixin):
    def evaluate(self, request: VisionRequest) -> VisionResult:
        parts: list[dict] = [{"type": "text", "text": request.prompt}]
        for path in request.image_paths:
            parts.append({"type": "image_url", "image_url": {"url": _data_uri(Path(path))}})
        messages: list[dict] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": parts})
        text, input_tokens, output_tokens = self._chat(messages, request.max_tokens, json_object=True)
        return VisionResult(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._cost(input_tokens, output_tokens),
        )


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class MinimaxImageProvider:
    name = "minimax"

    def __init__(self, http: MinimaxHttp, model: str) -> None:
        if model not in IMAGE_MODELS:
            raise ConfigError(f"{model!r} is not a known MiniMax image model. Known: {sorted(IMAGE_MODELS)}")
        self._http = http
        self.model = model

    def generate(self, request: ImageRequest, destination: Path) -> ImageResult:
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "response_format": "base64",
        }
        if request.subject_reference_path:
            payload["subject_reference"] = [
                {
                    "type": "character",
                    "image_file": _data_uri(Path(request.subject_reference_path)),
                }
            ]
        data = self._http.request("POST", "/v1/image_generation", payload)
        images = (data.get("data") or {}).get("image_base64") or []
        if not images:
            raise ProviderError("MiniMax returned no image data")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(base64.b64decode(images[0]))
        return ImageResult(model=self.model, local_path=str(destination), cost_usd=IMAGE_MODELS[self.model])


class MinimaxVideoProvider:
    name = "minimax"

    def __init__(self, http: MinimaxHttp, model: str, *, poll_hint_s: float) -> None:
        if model not in VIDEO_CATALOG:
            raise ConfigError(
                f"{model!r} is not a known MiniMax video model. Known: {sorted(VIDEO_CATALOG)}"
            )
        self._http = http
        self.model = model
        self._poll_hint_s = poll_hint_s

    def capabilities(self) -> VideoCapabilities:
        entry = VIDEO_CATALOG[self.model]
        min_s = int(entry["min_s"])  # type: ignore[arg-type]
        return VideoCapabilities(
            provider=self.name,
            model=self.model,
            durations_s=list(range(min_s, MAX_DURATION_S + 1)),
            ratios=list(RATIOS),
            resolutions=list(entry["resolutions"]),  # type: ignore[arg-type]
            supports_first_frame=True,
            supports_reference_image=True,
            max_reference_images=9,
            price_per_second_usd=dict(entry["prices"]),  # type: ignore[arg-type]
            source_url=f"{DOCS}/pricing/overview.md#models/video",
            verified=False,
        )

    def render_prompt(self, intent: SceneIntent) -> str:
        return (
            f"{intent.subject}, {intent.action}, in {intent.setting}. "
            f"Wardrobe: {intent.wardrobe}. Lighting: {intent.light}. "
            f"Colour palette: {intent.palette}. Camera: [{intent.camera}]. "
            "Photorealistic cinematic advertising footage, 35mm lens, natural motion, "
            "consistent grade with the previous shot. "
            "No visible signage, no lettering, no readable screens, no logos, no on-screen text, "
            "no captions."
        )

    def _validate_resolution(self, resolution: str) -> None:
        caps = self.capabilities()
        if resolution not in caps.resolutions:
            raise CapabilityMismatch(
                f"{self.model} supports resolutions {caps.resolutions}, got {resolution}"
            )

    def generate(self, request: VideoRequest) -> Submission:
        caps = self.capabilities()
        self._validate_resolution(request.resolution)
        if request.duration_s not in caps.durations_s:
            raise CapabilityMismatch(
                f"{self.model} supports durations {caps.durations_s[0]}-{caps.durations_s[-1]}s, "
                f"got {request.duration_s}s"
            )
        if request.ratio not in caps.ratios:
            raise CapabilityMismatch(f"{self.model} does not support ratio {request.ratio}")
        if request.first_frame_url and request.reference_image_urls:
            raise InvalidRequest(
                "MiniMax treats first-frame and reference images as mutually exclusive; "
                "send one or the other"
            )

        content: list[dict] = [{"type": "text", "text": request.prompt}]
        if request.first_frame_url:
            content.append(
                {"type": "image_url", "image_url": {"url": request.first_frame_url}, "role": "first_frame"}
            )
        for url in request.reference_image_urls:
            content.append(
                {"type": "image_url", "image_url": {"url": url}, "role": "reference_image"}
            )

        payload: dict[str, object] = {
            "model": self.model,
            "content": content,
            "duration": request.duration_s,
            "resolution": request.resolution,
            "ratio": request.ratio,
        }
        try:
            data = self._http.request("POST", "/v2/video_generation", payload)
            task_id = data.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise AmbiguousResponse("MiniMax accepted the task but returned no task_id")
        except (TransportFailure, AmbiguousResponse) as exc:
            # The request may have reached the server: never auto-resubmit paid work.
            raise SubmissionUnknown(f"video submission outcome unknown: {exc}") from exc
        return Submission(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            submitted_at=utcnow(),
            poll_hint_s=self._poll_hint_s,
        )

    def get_status(self, task: ProviderTaskRef) -> VideoJobStatus:
        data = self._http.request("GET", f"/v2/query/video_generation/{task.task_id}")
        payload = data.get("task")
        if not isinstance(payload, dict):
            raise ProviderError("MiniMax query response has no task object")
        raw_state = str(payload.get("status") or "")
        state = _STATUS_MAP.get(raw_state)
        if state is None:
            raise ProviderError(f"MiniMax reported an unknown task status {raw_state!r}")
        message = None
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "") or None
        return VideoJobStatus(task_id=task.task_id, state=state, raw_state=raw_state, message=message)

    def get_result(self, task: ProviderTaskRef, destination: Path) -> VideoResult:
        data = self._http.request("GET", f"/v2/query/video_generation/{task.task_id}")
        payload = data.get("task")
        if not isinstance(payload, dict):
            raise ProviderError("MiniMax query response has no task object")
        raw_state = str(payload.get("status") or "")
        state = _STATUS_MAP.get(raw_state)
        if state is None:
            raise ProviderError(f"MiniMax reported an unknown task status {raw_state!r}")
        if state == ShotState.FAILED:
            error = payload.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else None
            raise TaskFailed(f"MiniMax task {task.task_id} ended {raw_state}: {detail or 'no detail'}")
        if state != ShotState.DOWNLOADED:
            raise ProviderError(f"MiniMax task {task.task_id} is still {raw_state}")
        content = payload.get("content") or {}
        url = content.get("url") if isinstance(content, dict) else None
        if not url:
            raise ProviderError("MiniMax reported success without a video URL")
        self._http.download(url, destination)
        usage = payload.get("usage") or {}
        return VideoResult(
            task_id=task.task_id,
            local_path=str(destination),
            provider_url=url,
            duration_s=payload.get("duration"),
            resolution=payload.get("resolution"),
            reported_cost_usd=usage.get("total_cost") if isinstance(usage, dict) else None,
        )


TTS_PRICES = {"speech-2.8-turbo": 60.0, "speech-2.8-hd": 100.0}
TTS_ERRORS = {
    1002: "rate_limit",
    1039: "rate_limit",
    1004: "auth",
    1008: "balance",
    1042: "invalid",
    2013: "invalid",
}


class MinimaxVoiceProvider:
    """MiniMax text-to-speech.

    Two details from the API reference that are easy to get wrong:
    a 200 response can still carry ``base_resp.status_code != 0``, and the audio
    comes back hex-encoded unless ``output_format`` is ``url``.
    """

    name = "minimax"

    def __init__(self, http: MinimaxHttp, model: str, voice_id: str) -> None:
        if model not in TTS_PRICES:
            raise ConfigError(f"{model!r} is not a known MiniMax speech model. Known: {sorted(TTS_PRICES)}")
        self._http = http
        self.model = model
        self.voice_id = voice_id

    def synthesize(self, request: VoiceRequest, destination: Path) -> VoiceResult:
        voice_setting: dict[str, object] = {
            "voice_id": request.voice_id,
            "speed": request.speed,
            "vol": 1,
            "pitch": 0,
        }
        if request.emotion:
            voice_setting["emotion"] = request.emotion
        payload = {
            "model": self.model,
            "text": request.text,
            "stream": False,
            "output_format": "url",
            "language_boost": request.language,
            "voice_setting": voice_setting,
            "audio_setting": {"sample_rate": 44100, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        data = self._http.request("POST", "/v1/t2a_v2", payload)

        base = data.get("base_resp") or {}
        code = base.get("status_code")
        if code not in (0, None):
            message = str(base.get("status_msg") or f"speech error {code}")
            kind = TTS_ERRORS.get(int(code), "unknown")
            if kind == "balance":
                raise InsufficientBalance(message, provider_message=message)
            if kind == "rate_limit":
                raise RateLimited(message, provider_message=message)
            if kind in ("auth", "invalid"):
                raise InvalidRequest(message, provider_message=message)
            raise ProviderError(message, provider_message=message)

        audio = (data.get("data") or {}).get("audio")
        if not isinstance(audio, str) or not audio:
            raise ProviderError("MiniMax returned no audio")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if audio.startswith("http"):
            self._http.download(audio, destination)
        else:
            try:
                destination.write_bytes(bytes.fromhex(audio))
            except ValueError as exc:
                raise ProviderError(f"speech audio is neither a URL nor hex: {exc}") from None

        extra = data.get("extra_info") or {}
        characters = int(extra.get("usage_characters") or 0)
        return VoiceResult(
            model=self.model,
            voice_id=request.voice_id,
            local_path=str(destination),
            duration_s=round(float(extra.get("audio_length") or 0) / 1000, 3),
            characters=characters,
            cost_usd=round(characters / 1_000_000 * TTS_PRICES[self.model], 6),
        )
