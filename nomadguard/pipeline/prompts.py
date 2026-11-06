"""Vendor-neutral prompt text. The adapter renders its own dialect from intent."""

from __future__ import annotations

from ..contracts import SceneIntent


def universal_prompt(intent: SceneIntent, continuity: str) -> str:
    """What the shot requires, in words no vendor owns.

    Stored for inspection and comparison; the model never receives this string.
    """
    return (
        f"{intent.subject} {intent.action}. Setting: {intent.setting}. "
        f"Camera: {intent.camera}. Wardrobe: {intent.wardrobe}. "
        f"Light: {intent.light}. Palette: {intent.palette}. Continuity: {continuity}"
    )
