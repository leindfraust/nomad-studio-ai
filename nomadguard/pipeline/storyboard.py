"""Storyboard: legal shot timing, then the model writes the shots inside it.

The partition is a pure function so it can be tested without a network, and it
is driven by the provider's own capabilities rather than by a hardcoded rhythm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..contracts import Brief, Scene, SceneIntent, Storyboard, Strategy, TextRequest, VideoCapabilities
from ..errors import CapabilityMismatch, NomadGuardError
from ..providers.base import TextProvider, VideoProvider
from .llmjson import parse_with_repair

BEATS_BY_COUNT: dict[int, list[str]] = {
    3: ["hook", "tension", "relief"],
    2: ["hook", "relief"],
    1: ["relief"],
}

STORYBOARD_SYSTEM = (
    "You are a commercial director boarding a vertical social ad. You write shots that a "
    "text-to-video model can actually generate: one subject, one action, one camera move per "
    "shot. You never rely on dialogue, on-screen text or a second character to make sense."
)


class StoryboardError(NomadGuardError):
    """The model's scenes do not satisfy the storyboard contract."""


@dataclass(frozen=True)
class ShotPlan:
    durations: list[int]
    reduced_from: int | None


def plan_shots(target_s: int, caps: VideoCapabilities, *, preferred: int = 3) -> ShotPlan:
    """Partition ``target_s`` into shot durations the provider can actually produce.

    Prefers ``preferred`` shots, equal lengths, and drops the shot count when no
    partition exists. Raises rather than emitting a storyboard the model cannot
    render.
    """
    if target_s <= 0:
        raise ValueError("target duration must be positive")
    allowed = sorted({duration for duration in caps.durations_s if duration > 0})
    if not allowed:
        raise CapabilityMismatch(f"{caps.model} advertises no durations")

    for count in range(min(preferred, len(allowed)), 0, -1):
        partitions = _partitions(target_s, allowed, count)
        if partitions:
            best = min(partitions, key=lambda parts: (parts[-1] - parts[0], parts))
            reduced = preferred if count < preferred else None
            return ShotPlan(durations=best, reduced_from=reduced)

    raise CapabilityMismatch(
        f"{caps.model} cannot build {target_s}s from durations {allowed[:3]}"
        f"{'...' if len(allowed) > 3 else ''}"
    )


def _partitions(total: int, allowed: list[int], count: int) -> list[list[int]]:
    """Non-decreasing partitions of ``total`` into ``count`` allowed parts."""
    if count == 1:
        return [[total]] if total in allowed else []
    found: list[list[int]] = []
    for duration in allowed:
        remainder = total - duration
        if remainder < duration * (count - 1) or remainder > allowed[-1] * (count - 1):
            continue
        for tail in _partitions(remainder, allowed, count - 1):
            if tail and tail[0] >= duration:
                found.append([duration, *tail])
    return found


def build_storyboard(
    brief: Brief,
    strategy: Strategy,
    caps: VideoCapabilities,
    video: VideoProvider,
    text: TextProvider,
    *,
    max_tokens: int = 3000,
) -> tuple[Storyboard, float]:
    plan = plan_shots(brief.duration_s, caps)
    beats = BEATS_BY_COUNT[len(plan.durations)]
    result = text.generate(
        TextRequest(
            system=STORYBOARD_SYSTEM,
            max_tokens=max_tokens,
            json_object=True,
            user=_storyboard_prompt(brief, strategy, plan.durations, beats),
        )
    )
    payload, repair_cost = parse_with_repair(result.text, text, max_tokens=max_tokens)
    scenes = _build_scenes(payload, plan.durations, beats, video)
    continuity = payload.get("continuity_block")
    if not isinstance(continuity, str) or not continuity.strip():
        raise StoryboardError("continuity_block is required")
    storyboard = Storyboard(
        target_duration_s=brief.duration_s,
        total_duration_s=sum(plan.durations),
        aspect_ratio=brief.aspect_ratio,
        continuity_block=continuity.strip(),
        scenes=scenes,
    )
    return storyboard, (result.cost_usd or 0.0) + repair_cost

def _storyboard_prompt(brief: Brief, strategy: Strategy, durations: list[int], beats: list[str]) -> str:
    angle = strategy.selected
    shot_lines = "\n".join(
        f"- shot {index}: {duration}s, beat '{beat}'" for index, (duration, beat) in enumerate(zip(durations, beats))
    )
    return f"""TASK: STORYBOARD

Brand: {brief.brand} ({brief.category})
Audience: {brief.audience}
Core message: {brief.core_message}
Brand feel: {", ".join(brief.brand_feel)}
Selected angle: {angle.name}
Hook line available for the opening card: {angle.hook}
CTA: {angle.cta}
Visual notes: {brief.visual_notes or "none"}

Fixed shot timing (do not change it):
{shot_lines}

Write one scene per shot, in order.

Rules:
- Shot 1 must read as a problem or interruption within its first second, visually.
  Do not depend on dialogue or on reading more than four words.
- Exactly one shot must show what the brand actually does, without any lettering:
  a green check on a phone screen, a gate opening, a hand steadying a shoulder.
  A screen may appear only if it carries a simple icon and no words.
- Never put a readable board, sign or line of text in frame. Generated lettering
  comes back as garbled pseudo-text, and this brand is about clarity.
- Every scene uses the same traveller, the same wardrobe, the same single piece of
  luggage and the same colour grade. Name the luggage identically in every intent
  and never substitute a different bag. Describe that once in "continuity_block".
- "on_screen_text" is burned in later by an editor. Five words maximum. Put the
  hook on shot 1, the CTA on the last shot, and null in between. It is never part
  of the video prompt.
- "intent" must be physical and specific: what the camera sees. No brand name, no
  slogans, no copy inside intent fields.
- One action and one camera move per scene.

Return JSON exactly:
{{"continuity_block":"...","scenes":[{{"beat":"hook","description":"...","camera":"...",
"on_screen_text":"...","intent":{{"subject":"...","action":"...","setting":"...","camera":"...",
"wardrobe":"...","light":"...","palette":"..."}}}}]}}"""


def _build_scenes(
    payload: dict, durations: list[int], beats: list[str], video: VideoProvider
) -> list[Scene]:
    raw = payload.get("scenes")
    if not isinstance(raw, list) or len(raw) != len(durations):
        raise StoryboardError(f"expected {len(durations)} scenes, got {len(raw) if isinstance(raw, list) else 'none'}")
    continuity = str(payload.get("continuity_block") or "")
    scenes: list[Scene] = []
    start = 0.0
    for index, (item, duration, beat) in enumerate(zip(raw, durations, beats)):
        if not isinstance(item, dict):
            raise StoryboardError(f"scene {index} is not an object")
        if item.get("beat") != beat:
            raise StoryboardError(f"scene {index} beat must be {beat!r}")
        try:
            intent = SceneIntent.model_validate(item.get("intent"))
        except ValueError as exc:
            raise StoryboardError(f"scene {index} intent: {exc}") from None
        text = item.get("on_screen_text")
        scenes.append(
            Scene(
                index=index,
                start_s=start,
                duration_s=duration,
                beat=beat,  # type: ignore[arg-type]
                description=str(item.get("description") or "").strip(),
                camera=str(item.get("camera") or intent.camera).strip(),
                on_screen_text=str(text).strip() if isinstance(text, str) and text.strip() else None,
                intent=intent,
                universal_prompt=_universal(intent, continuity),
                provider_prompt=video.render_prompt(intent),
            )
        )
        start += duration
    return scenes


def _universal(intent: SceneIntent, continuity: str) -> str:
    from .prompts import universal_prompt

    return universal_prompt(intent, continuity or "single location, consistent grade")
