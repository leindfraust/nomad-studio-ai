"""ffmpeg assembly: normalize, concatenate, burn the copy that matters.

The video model never renders words. Text is drawn here, deterministically, so
copy is legible and never warped by a generative model.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..contracts import Storyboard
from ..errors import NomadGuardError

# The brief fixes the deliverable window; this is not a tunable.
MIN_DURATION_S = 10
MAX_DURATION_S = 15
FPS = 30
MIN_EDGE = 2  # yuv420p needs even dimensions


class AssemblyError(NomadGuardError):
    """ffmpeg could not produce the final file."""


def probe(path: Path, ffprobe: str) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssemblyError(f"ffprobe failed on {path.name}: {result.stderr.strip()[:300]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssemblyError(f"ffprobe returned non-JSON for {path.name}: {exc}") from None


def canvas_size(first_shot: Path, ffprobe: str) -> tuple[int, int]:
    """Take the canvas from real output instead of assuming a label's geometry."""
    streams = probe(first_shot, ffprobe).get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise AssemblyError(f"{first_shot.name} has no video stream")
    width = int(video.get("width") or 0) // 2 * 2
    height = int(video.get("height") or 0) // 2 * 2
    if width < MIN_EDGE or height < MIN_EDGE:
        raise AssemblyError(f"{first_shot.name} reports an unusable size {width}x{height}")
    return width, height


def assemble(
    storyboard: Storyboard,
    shot_paths: list[Path],
    output: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    font_path: Path,
    brand: str,
    cta: str,
    work_dir: Path,
    voiceover: Path | None = None,
    voice_start_s: float = 0.0,
    symbol_font: Path | None = None,
) -> Path:
    if len(shot_paths) != len(storyboard.scenes):
        raise AssemblyError(f"{len(shot_paths)} shots for {len(storyboard.scenes)} scenes")
    # ffmpeg runs with cwd=work_dir, so every path it receives must be absolute.
    shot_paths = [Path(path).resolve() for path in shot_paths]
    output = Path(output).resolve()
    work_dir = Path(work_dir).resolve()
    for path in shot_paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise AssemblyError(f"missing or empty shot: {path}")

    width, height = canvas_size(shot_paths[0], ffprobe)
    work_dir.mkdir(parents=True, exist_ok=True)
    font_name = _install_font(font_path, work_dir)
    normalized: list[Path] = []
    for index, path in enumerate(shot_paths):
        target = work_dir / f"norm-{index}.mp4"
        _run(
            [
                ffmpeg, "-y", "-v", "error", "-i", str(path),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={FPS},setsar=1",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", str(target),
            ],
            "normalize",
            cwd=work_dir,
        )
        normalized.append(target)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in normalized) + "\n", encoding="utf-8"
    )
    joined = work_dir / "joined.mp4"
    _run(
        [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(joined)],
        "concat",
        cwd=work_dir,
    )

    filters = _overlay_filters(
        storyboard, width, height, font_name, brand, cta, work_dir, _duration_of(joined, ffprobe)
    )
    symbol_name = _install_font(symbol_font, work_dir, "symbol") if symbol_font else None
    filters += _chip_filters(storyboard, width, height, font_name, symbol_name, brand, work_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    voiceover = Path(voiceover).resolve() if voiceover else None
    if voiceover is not None and not voiceover.is_file():
        raise AssemblyError(f"voiceover is missing: {voiceover}")

    command = [ffmpeg, "-y", "-v", "error", "-i", str(joined)]
    if voiceover is not None:
        command += ["-i", str(voiceover)]
    if filters:
        command += ["-vf", ",".join(filters)]
    if voiceover is not None:
        delay_ms = max(0, round(voice_start_s * 1000))
        # apad + -shortest ends the padded track at the video's length (ffmpeg docs).
        command += [
            "-filter:a",
            f"adelay={delay_ms}|{delay_ms},apad,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-map", "0:v:0",
            "-map", "1:a:0",
        ]
    else:
        command += ["-an"]
    encode = [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if voiceover is not None:
        encode += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest"]
    command += encode + [str(output)]
    _run(command, "final encode", cwd=work_dir)
    return output


def _install_font(font_path: Path, work_dir: Path, stem: str = "font") -> str:
    """Copy the font next to the render and return a colon-free name.

    Windows paths contain a drive colon, which ffmpeg's filter option parser reads
    as a separator no matter how it is escaped. A local filename sidesteps it.
    """
    if not font_path.is_file():
        raise AssemblyError(f"font not found: {font_path}")
    local = work_dir / f"{stem}{font_path.suffix or '.ttf'}"
    if not local.is_file():
        local.write_bytes(font_path.read_bytes())
    return local.name


def _chip_filters(
    storyboard: Storyboard,
    width: int,
    height: int,
    font_name: str,
    symbol_name: str | None,
    brand: str,
    work_dir: Path,
) -> list[str]:
    """A product plate over the tension shot: a check and the brand, no claims.

    This is the ad's one on-screen product cue. It is drawn here rather than asked
    of a video model, because generated screens came back black or as pseudo-text.
    """
    if len(storyboard.scenes) < 2:
        return []
    scene = storyboard.scenes[1]
    start = scene.start_s + 0.2
    end = max(start + 0.5, scene.start_s + scene.duration_s - 0.2)
    window = f"enable='between(t,{start:.2f},{end:.2f})'"

    plate_h = round(width * 0.14)
    plate_w = round(width * 0.42)
    plate_x = round(width * 0.07)
    plate_y = round(height * 0.58)
    mark = round(plate_h * 0.62)
    brand_size = round(plate_h * 0.38)

    filters = [
        f"drawbox=x={plate_x}:y={plate_y}:w={plate_w}:h={plate_h}:"
        f"color=0x0B1220@0.72:t=fill:{window}"
    ]

    text_x = plate_x + round(plate_h * 0.28)
    if symbol_name:
        (work_dir / "chip-check.txt").write_text("\u2713", encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={symbol_name}:textfile=chip-check.txt:fontcolor=0x4ADE80:"
            f"fontsize={mark}:x={text_x}:y={plate_y + (plate_h - mark) // 2}:{window}"
        )
        text_x += round(mark * 0.95)

    (work_dir / "chip-brand.txt").write_text(brand, encoding="utf-8")
    filters.append(
        f"drawtext=fontfile={font_name}:textfile=chip-brand.txt:fontcolor=white:"
        f"fontsize={brand_size}:x={text_x}:y={plate_y + (plate_h - brand_size) // 2}:{window}"
    )
    return filters


def _duration_of(path: Path, ffprobe: str) -> float:
    """The provider may return clips longer than requested; the card follows the cut."""
    try:
        fmt = probe(path, ffprobe).get("format") or {}
        return float(fmt.get("duration") or 0.0)
    except (AssemblyError, ValueError):
        return 0.0


def _overlay_filters(
    storyboard: Storyboard,
    width: int,
    height: int,
    font_name: str,
    brand: str,
    cta: str,
    work_dir: Path,
    actual_duration_s: float,
) -> list[str]:
    filters: list[str] = []
    font = f"fontfile={font_name}"
    base = max(18, round(width * 0.072))
    margin = round(height * 0.22)  # keep copy above the Reels/TikTok caption zone

    hook = next((scene.on_screen_text for scene in storyboard.scenes if scene.on_screen_text), None)
    if hook:
        (work_dir / "hook.txt").write_text(hook, encoding="utf-8")
        size = fit_font_size(width, [hook], base)
        filters.append(
            f"drawtext={font}:textfile=hook.txt:"
            f"fontcolor=white:fontsize={size}:line_spacing=6:"
            f"x=(w-text_w)/2:y=h-{margin}-text_h:"
            f"box=1:boxcolor=0x000000AA:boxborderw={round(size * 0.35)}:"
            f"enable='between(t,0.4,3.2)'"
        )

    total = actual_duration_s if actual_duration_s > 0 else float(storyboard.total_duration_s)
    end_start = max(0.0, total - 2.0)
    card_lines = [brand, *wrap_text(cta, 20)]
    (work_dir / "endcard.txt").write_text("\n".join(card_lines), encoding="utf-8")
    size = fit_font_size(width, card_lines, base)
    filters.append(
        f"drawbox=x=0:y=0:w=iw:h=ih:color=0x0B1220@0.90:t=fill:enable='gte(t,{end_start:.2f})'"
    )
    rule_width = round(width * 0.18)
    filters.append(
        f"drawbox=x={(width - rule_width) // 2}:y={max(0, height // 2 - 90)}:"
        f"w={rule_width}:h=3:color=0xF0B24A@0.9:t=fill:enable='gte(t,{end_start:.2f})'"
    )
    filters.append(
        f"drawtext={font}:textfile=endcard.txt:"
        f"fontcolor=white:fontsize={size}:line_spacing={round(size * 0.45)}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='gte(t,{end_start:.2f})'"
    )
    return filters


def wrap_text(text: str, limit: int) -> list[str]:
    """Greedy word wrap. drawtext never wraps on its own."""
    if limit < 1:
        raise ValueError("limit must be positive")
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


# Bold sans-serif advance is roughly 0.56em; below that the text overflows the frame.
_ADVANCE_RATIO = 0.56


def fit_font_size(width: int, lines: list[str], base: int, *, max_width_ratio: float = 0.86) -> int:
    longest = max((len(line) for line in lines), default=0)
    if longest == 0:
        return base
    available = width * max_width_ratio
    return max(14, min(base, int(available / (_ADVANCE_RATIO * longest))))


def _run(command: list[str], stage: str, *, cwd: Path) -> None:
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise AssemblyError(f"ffmpeg {stage} failed: {result.stderr.strip()[:500]}")
