"""Creative QA: a rubric the model fills, a gate this module decides.

The model supplies evidence, not the verdict. Scores are validated, the mean and
minimum are computed here against the configured policy, and a defect list is
kept even when the numbers pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Policy
from ..contracts import Brief, CreativeReport, Storyboard, VisionRequest
from ..errors import NomadGuardError
from ..providers.base import TextProvider, VisionProvider
from .llmjson import ModelOutputError, parse_with_repair

DIMENSIONS = ("realism", "visual_consistency", "brief_adherence", "hook_strength", "advertising_quality")
# Reported, but it does not gate: the storyboard is a production plan, not the
# client's requirement. Judging the brief against it measured the wrong thing.
STORYBOARD_DIMENSION = "storyboard_fidelity"
ALL_DIMENSIONS = (*DIMENSIONS, STORYBOARD_DIMENSION)

SYSTEM = (
    "You are a senior art director reviewing frames from a vertical social advertisement "
    "before it ships. You are specific about defects and you do not praise generic AI footage."
)


class CreativeQAError(NomadGuardError):
    """The vision model did not answer with a usable rubric."""


def evaluate(
    frames: list[Path],
    *,
    brief: Brief,
    storyboard: Storyboard,
    vision: VisionProvider,
    policy: Policy,
    repair: TextProvider | None = None,
    max_tokens: int = 1200,
) -> tuple[CreativeReport, float]:
    if not frames:
        raise CreativeQAError("no frames were extracted; creative QA cannot run")
    scenes = "\n".join(
        f"- {scene.start_s:.0f}-{scene.start_s + scene.duration_s:.0f}s ({scene.beat}): {scene.description}"
        for scene in storyboard.scenes
    )
    prompt = f"""Brief: {brief.brand}, {brief.category}, for {brief.audience}.
Core message: {brief.core_message}
Brand feel: {", ".join(brief.brand_feel)}
Storyboard:
{scenes}

These frames are listed in order. Judge them as frames from one 9:16 advertisement,
not as standalone images.

Score 0-10:
- realism: photographic believability; penalty for plastic skin, warped hands, fake bokeh
- visual_consistency: same person, wardrobe, location and grade across frames
- brief_adherence: judge against the CLIENT BRIEF above only. Does the ad show the stated
  problem and land the core message for this audience, without overclaiming? Ignore whether
  the shots match the storyboard; production choices are not the brief.
- hook_strength: does the opening frame stop a thumb without sound
- advertising_quality: could this run as a real ad for this brand
- storyboard_fidelity: how closely the shots match the storyboard description. Reported for
  the production team; it is not scored by the client and does not gate.

Return JSON exactly:
{{"scores":{{"realism":0,"visual_consistency":0,"brief_adherence":0,"hook_strength":0,"advertising_quality":0,"storyboard_fidelity":0}},
 "justifications":{{"realism":"...","visual_consistency":"...","brief_adherence":"...","hook_strength":"...","advertising_quality":"...","storyboard_fidelity":"..."}},
 "defects":["concrete defect, or empty"]}}"""

    result = vision.evaluate(
        VisionRequest(
            system=SYSTEM,
            prompt=prompt,
            image_paths=[str(path) for path in frames],
            max_tokens=max_tokens,
        )
    )
    payload, repair_cost = parse_with_repair(result.text, repair, max_tokens=max_tokens)
    scores = payload.get("scores")
    if not isinstance(scores, dict):
        raise CreativeQAError("scores object is missing")
    clean: dict[str, float] = {}
    for dimension in ALL_DIMENSIONS:
        value = scores.get(dimension)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 10:
            raise CreativeQAError(f"score for {dimension} must be a number 0-10, got {value!r}")
        clean[dimension] = round(float(value), 1)

    justifications_raw = payload.get("justifications") or {}
    justifications = {
        dimension: str(justifications_raw.get(dimension, "")).strip()
        for dimension in ALL_DIMENSIONS
        if isinstance(justifications_raw, dict)
    }
    defects_raw = payload.get("defects") or []
    defects = [str(item).strip() for item in defects_raw if str(item).strip()] if isinstance(defects_raw, list) else []
    if not isinstance(defects_raw, list):
        raise CreativeQAError("defects must be a list")

    gating = {dimension: clean[dimension] for dimension in DIMENSIONS}
    mean = sum(gating.values()) / len(gating)
    passed = mean >= policy.qa_min_mean and min(gating.values()) >= policy.qa_min_dimension
    return (
        CreativeReport(
            model=result.model,
            scores=clean,
            justifications=justifications,
            defects=defects,
            frames=[str(path) for path in frames],
            passed=passed,
        ),
        (result.cost_usd or 0.0) + repair_cost,
    )


def gate_reasons(report: CreativeReport, policy: Policy) -> list[str]:
    gating = {dimension: report.scores[dimension] for dimension in DIMENSIONS}
    mean = sum(gating.values()) / len(gating)
    reasons: list[str] = []
    if mean < policy.qa_min_mean:
        reasons.append(f"creative mean {mean:.1f} is below {policy.qa_min_mean}")
    weakest = min(gating, key=lambda key: gating[key])
    if gating[weakest] < policy.qa_min_dimension:
        reasons.append(
            f"{weakest} scored {gating[weakest]}, below the {policy.qa_min_dimension} floor"
        )
    return reasons


def rubric_json(report: CreativeReport) -> str:
    return json.dumps(report.model_dump(), indent=2)
