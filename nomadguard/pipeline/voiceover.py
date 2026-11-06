"""Voiceover: one short script from the selected angle, then one TTS call.

Speech length is bounded by the cut, not by taste: about 150 words per minute of
runtime is what a calm read fits, so a 12 second ad has room for roughly 30 words.
"""

from __future__ import annotations

import json

from ..contracts import Brief, Strategy, TextRequest, Voiceover, VoiceRequest
from ..errors import NomadGuardError
from ..providers.base import TextProvider, VoiceProvider
from .llmjson import parse_with_repair

WORDS_PER_MINUTE = 150  # a calm commercial read, used to bound the script
PAUSE_MARKER = "<#0.6#>"

SYSTEM = (
    "You write voiceover for short vertical advertising. You write for the ear: short "
    "sentences, concrete nouns, no hype, no statistics, no promises of specific payouts or "
    "coverage. You never describe what the viewer can already see."
)


class VoiceoverError(NomadGuardError):
    """The script or the synthesis did not satisfy the contract."""


def word_budget(duration_s: float) -> int:
    return max(6, round(duration_s / 60 * WORDS_PER_MINUTE))


def write_voiceover(
    brief: Brief,
    strategy: Strategy,
    text: TextProvider,
    voice: VoiceProvider,
    *,
    start_s: float,
    destination,
    max_tokens: int = 400,
) -> tuple[Voiceover, float]:
    budget = word_budget(brief.duration_s)
    result = text.generate(
        TextRequest(
            system=SYSTEM,
            max_tokens=max_tokens,
            json_object=True,
            user=_prompt(brief, strategy, budget),
        )
    )
    payload, repair_cost = parse_with_repair(result.text, text, max_tokens=max_tokens)
    first = str(payload.get("first_line") or "").strip()
    second = str(payload.get("second_line") or "").strip()
    if not first or not second:
        raise VoiceoverError("both first_line and second_line are required")
    words = len((first + " " + second).split())
    if words > budget:
        raise VoiceoverError(f"script is {words} words, over the {budget}-word budget for this cut")

    script = f"{first}{PAUSE_MARKER}{second}"
    spoken = voice.synthesize(
        VoiceRequest(
            text=script,
            voice_id=voice.voice_id,
            speed=0.98,
            emotion="calm",
            language="English",
        ),
        destination,
    )
    return (
        Voiceover(
            text=script,
            start_s=start_s,
            voice_id=spoken.voice_id,
            local_path=spoken.local_path,
            duration_s=spoken.duration_s,
            cost_usd=spoken.cost_usd,
        ),
        (result.cost_usd or 0.0) + repair_cost + (spoken.cost_usd or 0.0),
    )


def _prompt(brief: Brief, strategy: Strategy, budget: int) -> str:
    angle = strategy.selected
    return f"""TASK: VOICEOVER

Brand: {brief.brand} ({brief.category})
Audience: {brief.audience}
Core message: {brief.core_message}
Brand feel: {", ".join(brief.brand_feel)}
Selected angle: {angle.name}
Angle concept: {angle.concept}
Hook already shown on screen: {angle.hook}
Call to action on the end card: {angle.cta}

Write two spoken lines, {budget} words in total or fewer:
- "first_line": the problem or tension, under eight words, spoken as the opening beat.
- "second_line": the promise, ending with the brand name.

Rules:
- Read aloud, it must sound like a confident person, not a slogan generator.
- No numbers, no prices, no payout amounts, no response times, no coverage claims.
- Do not repeat the on-screen hook word for word.
- No exclamation marks, no "peace of mind", no "travel with confidence" cliches: the
  core message is the requirement, not the script.

Return JSON exactly:
{{"first_line":"...","second_line":"..."}}"""
