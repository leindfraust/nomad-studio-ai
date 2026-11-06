"""JSON out of model text, without pretending a malformed answer was fine.

Models occasionally emit an unescaped quote. Rather than a tolerant parser that
would silently invent structure, one explicit repair call is made and the result
is still validated strictly. If that fails, the raw text is kept for diagnosis.
"""

from __future__ import annotations

import json

from ..contracts import TextRequest
from ..errors import NomadGuardError
from ..providers.base import TextProvider


class ModelOutputError(NomadGuardError):
    """The model answered, but not with the JSON the contract requires."""

    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


REPAIR_SYSTEM = (
    "You repair malformed JSON. Return the same information as strictly valid JSON. "
    "Fix only syntax: escape inner quotes, remove trailing commas, close brackets. "
    "Do not add, drop, reorder or reword any field value."
)


def parse_json_object(text: str) -> dict:
    """Accept a bare object, or one wrapped in a ```json fence, and nothing else."""
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        candidate = "\n".join(lines).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"model output is not JSON: {exc}", raw=text) from None
    if not isinstance(payload, dict):
        raise ModelOutputError("model output must be a JSON object", raw=text)
    return payload


def parse_with_repair(
    text: str, provider: TextProvider | None, *, max_tokens: int = 2500
) -> tuple[dict, float]:
    """Parse, and on a syntax error spend one small call to repair it.

    Returns the payload and the repair cost (0.0 when no repair was needed).
    With no text provider available the first failure is raised unchanged.
    """
    try:
        return parse_json_object(text), 0.0
    except ModelOutputError as first:
        if provider is None:
            raise
        repaired = provider.generate(
            TextRequest(
                system=REPAIR_SYSTEM,
                user=text,
                max_tokens=max_tokens,
                json_object=True,
            )
        )
        try:
            return parse_json_object(repaired.text), repaired.cost_usd or 0.0
        except ModelOutputError as second:
            raise ModelOutputError(
                f"{first}; repair attempt also failed: {second}", raw=first.raw
            ) from None


def ask_json(
    provider: TextProvider,
    *,
    system: str,
    user: str,
    parse,
    max_tokens: int,
    attempts: int = 2,
):
    """Ask for JSON, validate it, and on a contract miss say what was wrong once.

    Syntax slips get the repair pass; a wrong field name or a missing key gets the
    validator's own message fed back. Both are cheap calls that save an otherwise
    wasted run. After the last attempt the error is raised, never coerced.
    """
    cost = 0.0
    complaint = ""
    last_error: Exception | None = None
    for _ in range(attempts):
        prompt = user if not complaint else (
            f"{user}\n\nYour previous answer was rejected by the validator:\n{complaint}\n"
            "Return only the corrected JSON."
        )
        result = provider.generate(
            TextRequest(system=system, user=prompt, max_tokens=max_tokens, json_object=True)
        )
        cost += result.cost_usd or 0.0
        payload, repair = parse_with_repair(result.text, provider, max_tokens=max_tokens)
        cost += repair
        try:
            return parse(payload), cost
        except (ValueError, NomadGuardError) as exc:
            complaint = str(exc)[:500]
            last_error = exc
    raise last_error if last_error else ModelOutputError("no attempt was made")
