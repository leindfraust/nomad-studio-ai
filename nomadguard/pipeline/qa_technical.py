"""Technical QA. Deterministic, and it can fail the job."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import Policy
from ..contracts import TechReport
from .assemble import MAX_DURATION_S, MIN_DURATION_S, probe


def inspect(path: Path, *, ffmpeg: str, ffprobe: str, policy: Policy, expect_audio: bool = False) -> TechReport:
    failures: list[str] = []
    if not path.is_file():
        return TechReport(
            file=path.name, size_bytes=0, duration_s=0.0, width=0, height=0,
            aspect_ratio="0:0", codec=None, pixel_format=None, fps=None,
            has_audio=False, decodable=False,
            failures=["file does not exist"], valid=False,
        )
    size = path.stat().st_size
    if size == 0:
        failures.append("file is empty")

    try:
        metadata = probe(path, ffprobe)
    except Exception as exc:  # probe raises AssemblyError with ffprobe's own words
        return TechReport(
            file=path.name, size_bytes=size, duration_s=0.0, width=0, height=0,
            aspect_ratio="0:0", codec=None, pixel_format=None, fps=None,
            has_audio=False, decodable=False,
            failures=[f"not probeable: {exc}"], valid=False,
        )

    streams = metadata.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not video:
        failures.append("no video stream")
    duration = _float((metadata.get("format") or {}).get("duration"))
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    codec = (video or {}).get("codec_name")
    pix_fmt = (video or {}).get("pix_fmt")
    fps = _ratio((video or {}).get("r_frame_rate"))

    if duration is None:
        failures.append("duration is unreadable")
    elif not (MIN_DURATION_S - policy.duration_tolerance_s <= duration <= MAX_DURATION_S + policy.duration_tolerance_s):
        failures.append(
            f"duration {duration:.2f}s is outside {MIN_DURATION_S}-{MAX_DURATION_S}s "
            f"(tolerance {policy.duration_tolerance_s}s)"
        )

    if width and height:
        actual = width / height
        target = 9 / 16
        if abs(actual - target) / target > policy.aspect_tolerance:
            failures.append(
                f"aspect {width}x{height} is not 9:16 within {policy.aspect_tolerance:.1%}"
            )
        if min(width, height) < policy.resolution_floor_short_edge:
            failures.append(
                f"short edge {min(width, height)}px is below the {policy.resolution_floor_short_edge}px floor"
            )
    else:
        failures.append("resolution is unreadable")

    if codec != "h264":
        failures.append(f"codec {codec or 'unknown'} is not h264")
    if pix_fmt != "yuv420p":
        failures.append(f"pixel format {pix_fmt or 'unknown'} is not yuv420p")
    if expect_audio and not has_audio:
        failures.append("voiceover was generated but the file has no audio stream")

    decodable = _decodes(path, ffmpeg)
    if not decodable:
        failures.append("full decode failed (playable in a player is not guaranteed)")

    return TechReport(
        file=path.name,
        size_bytes=size,
        duration_s=duration or 0.0,
        width=width,
        height=height,
        aspect_ratio=_ratio_label(width, height, policy.aspect_tolerance),
        codec=codec,
        pixel_format=pix_fmt,
        fps=fps,
        has_audio=has_audio,
        decodable=decodable,
        failures=failures,
        valid=not failures,
    )


def extract_frames(
    path: Path, timestamps: list[float], out_dir: Path, *, ffmpeg: str
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        destination = out_dir / f"frame-{index:02d}-{timestamp:.2f}s.jpg"
        result = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
             "-frames:v", "1", "-q:v", "2", str(destination)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and destination.is_file():
            frames.append(destination)
    return frames


def _decodes(path: Path, ffmpeg: str) -> bool:
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stderr.strip()


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ratio(value: object) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        return round(float(numerator) / float(denominator), 3)
    except (ValueError, ZeroDivisionError):
        return None


def _ratio_label(width: int, height: int, tolerance: float) -> str:
    """Name the intended standard ratio when the measured one is within tolerance.

    A 480x854 canvas is 240:427 exactly; calling that "9:16" is the rounding the
    deliverable asks for, and the tolerance that licenses it is the same one QA
    enforces. Anything further off keeps its exact reduced form.
    """
    if not width or not height:
        return "0:0"
    from math import gcd

    actual = width / height
    for numerator, denominator in ((9, 16), (16, 9), (1, 1), (4, 5), (5, 4), (4, 3), (3, 4), (2, 3), (21, 9)):
        target = numerator / denominator
        if abs(actual - target) / target <= tolerance:
            return f"{numerator}:{denominator}"
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"
