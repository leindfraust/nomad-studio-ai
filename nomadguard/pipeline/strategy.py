"""Brief -> three angles -> a scored, reasoned selection.

Two model calls, both JSON. The winner is computed here from the scores rather
than taken from the model's own claim, so the rationale and the choice cannot
disagree.
"""

from __future__ import annotations

import json
import re

from ..contracts import Angle, AngleScore, Brief, Strategy, TextRequest
from ..errors import NomadGuardError
from ..providers.base import TextProvider
from .llmjson import ask_json

ANGLES_SYSTEM = (
    "You are a senior creative director at a premium brand agency. You write social-first "
    "advertising for short vertical video. You are concrete, you kill cliches, and you never "
    "invent product facts, statistics, prices, payout amounts or guarantees."
)

SCORES_SYSTEM = (
    "You are a creative strategist choosing which of three candidate angles goes into "
    "production. You score honestly and you explain what would make each one fail."
)


class StrategyError(NomadGuardError):
    """The model returned angles that do not satisfy the strategy contract."""


def build_strategy(brief: Brief, text: TextProvider, *, max_tokens: int = 2500) -> tuple[Strategy, float]:
    angles, angles_cost = ask_json(
        text,
        system=ANGLES_SYSTEM,
        user=_angles_prompt(brief),
        parse=_parse_angles,
        max_tokens=max_tokens,
    )
    (scores, reason, insight), scores_cost = ask_json(
        text,
        system=SCORES_SYSTEM,
        user=_scores_prompt(brief, angles),
        parse=lambda payload: _parse_scores(payload, angles),
        max_tokens=max_tokens,
    )

    winner = _winner(scores)
    reason_cost = 0.0
    if _justifies_another_angle(reason, winner.angle_name, angles):
        # The scores decide the winner; the prose must not argue for a different one.
        fixed, reason_cost = ask_json(
            text,
            system=REASON_SYSTEM,
            user=_reason_prompt(brief, winner, scores),
            parse=lambda payload: _require_reason(payload),
            max_tokens=400,
            attempts=2,
        )
        reason = fixed

    return (
        Strategy(
            angles=angles,
            scores=scores,
            selected_name=winner.angle_name,
            selection_reason=reason,
            audience_insight=insight,
        ),
        angles_cost + scores_cost + reason_cost,
    )


def _require_reason(payload: dict) -> str:
    reason = payload.get("selection_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise StrategyError("selection_reason is required")
    return reason.strip()


REASON_SYSTEM = (
    "You justify a decision that has already been made from the scores. You explain that "
    "choice and never argue for a different option."
)


def _justifies_another_angle(reason: str, winner: str, angles: list[Angle]) -> bool:
    """True when the prose names another angle but never names the winner.

    Word boundaries matter: a one-word angle name like "Calm" must not match
    "calmly", and the check must not depend on names being long.
    """

    def mentioned(name: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", reason, re.IGNORECASE) is not None

    names_winner = mentioned(winner)
    names_other = any(mentioned(angle.name) for angle in angles if angle.name != winner)
    return names_other and not names_winner


def _reason_prompt(brief: Brief, winner: AngleScore, scores: list[AngleScore]) -> str:
    table = "\n".join(
        f"- {score.angle_name}: insight {score.insight_strength}, hook {score.hook_viability}, "
        f"message {score.message_fidelity}, total {score.total}"
        for score in scores
    )
    return f"""TASK: REASON

The selected angle is "{winner.angle_name}". It has the highest total score.

Scores:
{table}

Core message: {brief.core_message}

Write the selection_reason for "{winner.angle_name}" only: two or three sentences on the
audience insight it is built on and why it works as a social ad. Do not mention another
angle by name and do not argue for a different choice.

Return JSON exactly:
{{"selection_reason":"..."}}"""


def _angles_prompt(brief: Brief) -> str:
    return f"""TASK: ANGLES

Marketing brief:
{json.dumps(brief.model_dump(), indent=2)}

Produce exactly three creative angles. They must be three genuinely different
strategic bets, not three wordings of one idea. At least one should take an
unexpected route.

Rules:
- "hook" is what stops the scroll in the first two seconds: a line of on-screen
  copy or the visual beat. Twelve words maximum.
- "concept" describes one or two concrete shots. No abstract claims.
- "target_emotion" is one word or a short phrase.
- "cta" respects the brand's core message. Do not promise specific payouts,
  amounts, response times or coverage that the brief does not state.
- Avoid: airport cliches panning across a departure board, slow-motion running,
  stock-photo handshakes, "peace of mind" as a spoken line.

Return JSON exactly:
{{"angles":[{{"name":"...","target_emotion":"...","hook":"...","concept":"...","cta":"..."}}]}}"""


def _scores_prompt(brief: Brief, angles: list[Angle]) -> str:
    return f"""TASK: SCORES

Core message the selected angle must serve: {brief.core_message}

Candidate angles:
{json.dumps([angle.model_dump() for angle in angles], indent=2)}

Score each angle from 0 to 10 on:
- insight_strength: does it come from a real, specific audience tension?
- hook_viability: can the first 2 seconds stop a thumb without relying on sound?
- message_fidelity: does it land the core message without overclaiming?

Give one sentence per score explaining the number.

Then write:
- "selection_reason": two or three sentences justifying the highest-scoring angle,
  naming the insight and why it works as a social ad.
- "audience_insight": the human truth the winning angle is built on, in one sentence.

Return JSON exactly:
{{"scores":[{{"angle_name":"...","insight_strength":0,"hook_viability":0,"message_fidelity":0,"reason":"..."}}],
 "selection_reason":"...","audience_insight":"..."}}"""


def _parse_angles(payload: dict) -> list[Angle]:
    raw = payload.get("angles")
    if not isinstance(raw, list) or len(raw) != 3:
        raise StrategyError("expected exactly three angles")
    try:
        angles = [Angle.model_validate(item) for item in raw]
    except ValueError as exc:
        raise StrategyError(f"angle does not match the contract: {exc}") from None
    if len({angle.name for angle in angles}) != 3:
        raise StrategyError("angle names must be distinct")
    return angles


def _parse_scores(payload: dict, angles: list[Angle]) -> tuple[list[AngleScore], str, str]:
    raw = payload.get("scores")
    if not isinstance(raw, list):
        raise StrategyError("scores must be a list")
    try:
        scores = [AngleScore.model_validate(item) for item in raw]
    except ValueError as exc:
        raise StrategyError(f"score does not match the contract: {exc}") from None
    names = {angle.name for angle in angles}
    if {score.angle_name for score in scores} != names:
        raise StrategyError("scores must cover every angle exactly once")
    reason = payload.get("selection_reason")
    insight = payload.get("audience_insight")
    if not isinstance(reason, str) or not reason.strip():
        raise StrategyError("selection_reason is required")
    if not isinstance(insight, str) or not insight.strip():
        raise StrategyError("audience_insight is required")
    return scores, reason.strip(), insight.strip()


def _winner(scores: list[AngleScore]) -> AngleScore:
    """Highest total; ties break on hook viability, then on listed order."""
    order = {score.angle_name: index for index, score in enumerate(scores)}
    return max(scores, key=lambda score: (score.total, score.hook_viability, -order[score.angle_name]))
