"""Any endpoint that speaks the OpenAI chat-completions shape.

Present to prove the seam: a second text/vision implementation behind the same
protocols, selected by config with no pipeline change. It does not claim to
cover OpenAI or Gemini media APIs, which are different surfaces.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path

from ..contracts import TextRequest, TextResult, VisionRequest, VisionResult
from ..errors import ProviderError, ProviderUnavailable, SubmissionUnknown, error_for_status


class OpenAiCompatHttp:
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 180.0, transport=None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport or self._urllib

    def _urllib(self, method, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"network failure: {exc}") from exc

    def chat(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        status, _, raw = self._transport(
            "POST", f"{self._base_url}/chat/completions", headers, body, self._timeout_s
        )
        if status >= 400:
            message = raw.decode("utf-8", "replace")[:500] or None
            raise error_for_status(status, provider_message=message)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"non-JSON chat response: {exc}") from None


def _content(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("no choices in chat response")
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ProviderError("empty chat message")
    return content


class OpenAiCompatTextProvider:
    name = "openai_compat"

    def __init__(self, http: OpenAiCompatHttp, model: str) -> None:
        self._http = http
        self.model = model

    def generate(self, request: TextRequest) -> TextResult:
        messages: list[dict] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.user})
        payload: dict[str, object] = {"model": self.model, "messages": messages, "max_tokens": request.max_tokens}
        if request.json_object:
            payload["response_format"] = {"type": "json_object"}
        data = self._http.chat(payload)
        usage = data.get("usage") or {}
        return TextResult(
            text=_content(data),
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=None,  # rates differ per vendor; never invented here
        )


class OpenAiCompatVisionProvider:
    name = "openai_compat"

    def __init__(self, http: OpenAiCompatHttp, model: str) -> None:
        self._http = http
        self.model = model

    def evaluate(self, request: VisionRequest) -> VisionResult:
        parts: list[dict] = [{"type": "text", "text": request.prompt}]
        for path in request.image_paths:
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        messages: list[dict] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": parts})
        data = self._http.chat(
            {"model": self.model, "messages": messages, "max_tokens": request.max_tokens}
        )
        usage = data.get("usage") or {}
        return VisionResult(
            text=_content(data),
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=None,
        )
