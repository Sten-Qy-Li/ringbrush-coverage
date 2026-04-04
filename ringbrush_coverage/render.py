from __future__ import annotations

import bisect
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

from ringbrush_coverage.core import DISPLAY_NAMES, SURFACE_LABELS, SessionAnalysis

ZONE_POLYGONS = {
    "outer-left": [(0.08, 0.33), (0.20, 0.20), (0.31, 0.28), (0.28, 0.50), (0.17, 0.66), (0.08, 0.56)],
    "outer-front": [(0.31, 0.27), (0.50, 0.17), (0.69, 0.27), (0.69, 0.47), (0.50, 0.56), (0.31, 0.47)],
    "outer-right": [(0.92, 0.33), (0.80, 0.20), (0.69, 0.28), (0.72, 0.50), (0.83, 0.66), (0.92, 0.56)],
    "inner-upper": [(0.26, 0.22), (0.50, 0.08), (0.74, 0.22), (0.62, 0.36), (0.50, 0.31), (0.38, 0.36)],
    "inner-lower": [(0.23, 0.58), (0.50, 0.68), (0.77, 0.58), (0.69, 0.81), (0.50, 0.92), (0.31, 0.81)],
}

ZONE_LABEL_POSITIONS = {
    "outer-left": (0.18, 0.50),
    "outer-front": (0.50, 0.40),
    "outer-right": (0.82, 0.50),
    "inner-upper": (0.50, 0.21),
    "inner-lower": (0.50, 0.79),
}


@lru_cache(maxsize=None)
def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"])
    else:
        candidates.extend(["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"])
    candidates.append("DejaVuSans.ttf")

    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix(color_a: str, color_b: str, amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    a = ImageColor.getrgb(color_a)
    b = ImageColor.getrgb(color_b)
    return tuple(int((1.0 - amount) * a[idx] + amount * b[idx]) for idx in range(3))


@lru_cache(maxsize=None)
def _gradient_background(width: int, height: int) -> Image.Image:
    top = np.array(ImageColor.getrgb("#fff5ec"), dtype=float)
    bottom = np.array(ImageColor.getrgb("#e7f3ee"), dtype=float)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        blend = y / max(height - 1, 1)
        image[y, :, :] = ((1.0 - blend) * top + blend * bottom).astype(np.uint8)
    return Image.fromarray(image, mode="RGB")


def _scale_polygon(points: list[tuple[float, float]], box: tuple[int, int, int, int]) -> list[tuple[float, float]]:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    return [(left + (x * width), top + (y * height)) for x, y in points]


def _draw_glow(draw: ImageDraw.ImageDraw, center: tuple[float, float], radius: int) -> None:
    x, y = center
    for index, alpha in enumerate((70, 50, 35, 20), start=1):
        spread = radius + (index * 7)
        draw.ellipse((x - spread, y - spread, x + spread, y + spread), fill=(117, 214, 138, alpha))


def _interpolate_state(analysis: SessionAnalysis, time_s: float) -> dict[str, object]:
    if len(analysis.windows) == 1:
        window = analysis.windows[0]
        return {
            "cursor": window.cursor,
            "coverage": window.coverage_seconds,
            "probabilities": window.probabilities,
            "dominant": window.dominant_label,
        }

    centers = [window.center_s for window in analysis.windows]
    index = bisect.bisect_left(centers, time_s)
    if index <= 0:
        current = analysis.windows[0]
        nxt = analysis.windows[1]
    elif index >= len(analysis.windows):
        current = analysis.windows[-2]
        nxt = analysis.windows[-1]
    else:
        current = analysis.windows[index - 1]
        nxt = analysis.windows[index]

    span = max(nxt.center_s - current.center_s, 1e-6)
    mix = max(0.0, min(1.0, (time_s - current.center_s) / span))
    cursor = (
        (current.cursor[0] * (1.0 - mix)) + (nxt.cursor[0] * mix),
        (current.cursor[1] * (1.0 - mix)) + (nxt.cursor[1] * mix),
    )
    coverage = {
        label: (current.coverage_seconds[label] * (1.0 - mix)) + (nxt.coverage_seconds[label] * mix)
        for label in SURFACE_LABELS
    }
    probabilities = {
        label: (current.probabilities[label] * (1.0 - mix)) + (nxt.probabilities[label] * mix)
        for label in current.probabilities
    }
    dominant = max(probabilities, key=probabilities.get)
    return {
        "cursor": cursor,
        "coverage": coverage,
        "probabilities": probabilities,
        "dominant": dominant,
    }


def _trail_points(analysis: SessionAnalysis, time_s: float) -> list[tuple[float, float]]:
    points = [window.cursor for window in analysis.windows if (time_s - 2.5) <= window.center_s <= time_s]
    return points[-12:]


def _build_static_base(analysis: SessionAnalysis, width: int, height: int) -> Image.Image:
    image = _gradient_background(width, height).copy().convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = _load_font(34, bold=True)
    body_font = _load_font(18)
    small_font = _load_font(15)
    label_font = _load_font(16, bold=True)

    mouth_box = (105, 80, width - 105, height - 120)
    left, top, right, bottom = mouth_box

    draw.ellipse((left - 12, top + 26, right + 12, bottom + 50), fill=(78, 24, 18, 35))
    draw.ellipse(mouth_box, fill=(66, 10, 15, 255), outline=(199, 82, 70, 255), width=8)
    draw.arc((left - 14, top - 8, right + 14, bottom + 12), start=204, end=336, fill=(245, 171, 149, 190), width=6)
    draw.arc((left - 16, top + 10, right + 16, bottom + 30), start=24, end=156, fill=(161, 47, 50, 180), width=4)

    draw.text((48, 26), "Ringbrush Coverage", fill=(42, 42, 42, 255), font=title_font)
    subtitle = f"{analysis.source_path.name}  |  calibration: {analysis.calibration_source}"
    draw.text((48, 66), subtitle, fill=(77, 87, 88, 255), font=body_font)

    draw.rounded_rectangle((48, height - 92, 332, height - 40), radius=18, fill=(255, 255, 255, 185))

    progress_left = width - 290
    progress_top = 84
    draw.rounded_rectangle((progress_left, progress_top, width - 34, height - 66), radius=24, fill=(255, 255, 255, 195))
    draw.text((progress_left + 20, progress_top + 16), "Coverage so far", fill=(40, 40, 40, 255), font=label_font)

    for idx, label in enumerate(SURFACE_LABELS):
        top_y = progress_top + 58 + (idx * 64)
        bar_left = progress_left + 20
        bar_right = width - 58
        draw.text((bar_left, top_y - 24), DISPLAY_NAMES[label], fill=(51, 59, 60, 255), font=small_font)
        draw.rounded_rectangle((bar_left, top_y, bar_right, top_y + 18), radius=9, fill=(229, 235, 231, 255))

    timeline_left = 48
    timeline_right = width - 48
    timeline_top = height - 28
    draw.rounded_rectangle((timeline_left, timeline_top, timeline_right, timeline_top + 8), radius=4, fill=(215, 222, 221, 255))
    return image


def _draw_frame(
    analysis: SessionAnalysis,
    time_s: float,
    width: int,
    height: int,
    base_frame: Image.Image,
) -> Image.Image:
    image = base_frame.copy()
    draw = ImageDraw.Draw(image, "RGBA")
    body_font = _load_font(18)
    small_font = _load_font(15)

    state = _interpolate_state(analysis, time_s)
    current_ratio = {
        label: 1.0 - np.exp(-(state["coverage"][label] / max(analysis.target_zone_seconds, 1e-6)))
        for label in SURFACE_LABELS
    }

    mouth_box = (105, 80, width - 105, height - 120)
    left, top, right, bottom = mouth_box

    for label in SURFACE_LABELS:
        ratio = float(current_ratio[label])
        fill_rgb = _mix("#571019", "#4fd284", 0.18 + (0.82 * ratio))
        outline_rgb = _mix("#8d3340", "#b8ffd0", 0.10 + (0.90 * ratio))
        polygon = _scale_polygon(ZONE_POLYGONS[label], mouth_box)
        draw.polygon(polygon, fill=(*fill_rgb, 205), outline=(*outline_rgb, 255))

    trail = _trail_points(analysis, time_s)
    trail_pixels = [
        (left + (point[0] * (right - left)), top + (point[1] * (bottom - top)))
        for point in trail
    ]
    for idx in range(1, len(trail_pixels)):
        opacity = int(35 + (idx / max(len(trail_pixels) - 1, 1)) * 120)
        draw.line((trail_pixels[idx - 1], trail_pixels[idx]), fill=(255, 233, 190, opacity), width=4)

    cursor_x = left + (state["cursor"][0] * (right - left))
    cursor_y = top + (state["cursor"][1] * (bottom - top))
    _draw_glow(draw, (cursor_x, cursor_y), radius=8)
    draw.ellipse((cursor_x - 9, cursor_y - 9, cursor_x + 9, cursor_y + 9), fill=(254, 241, 179, 255), outline=(255, 255, 255, 255), width=2)

    dominant_label = state["dominant"]
    dominant_text = f"Current region: {DISPLAY_NAMES.get(dominant_label, dominant_label)}"
    draw.text((64, height - 80), dominant_text, fill=(35, 46, 47, 255), font=body_font)

    progress_left = width - 290
    progress_top = 84
    for idx, label in enumerate(SURFACE_LABELS):
        ratio = float(current_ratio[label])
        top_y = progress_top + 58 + (idx * 64)
        bar_left = progress_left + 20
        bar_right = width - 58
        fill_right = bar_left + ((bar_right - bar_left) * ratio)
        draw.rounded_rectangle((bar_left, top_y, fill_right, top_y + 18), radius=9, fill=(*_mix("#90c8a2", "#4fd284", ratio), 255))

    total_duration = max(analysis.parsed_session.duration_s, 1e-6)
    progress = max(0.0, min(1.0, time_s / total_duration))
    timeline_left = 48
    timeline_right = width - 48
    timeline_top = height - 28
    draw.rounded_rectangle((timeline_left, timeline_top, timeline_left + ((timeline_right - timeline_left) * progress), timeline_top + 8), radius=4, fill=(81, 184, 122, 255))
    timing = f"{time_s:5.1f}s / {analysis.parsed_session.duration_s:5.1f}s"
    bbox = draw.textbbox((0, 0), timing, font=small_font)
    draw.text((timeline_right - bbox[2], timeline_top - 22), timing, fill=(63, 72, 73, 255), font=small_font)

    return image.convert("RGB")


def render_mp4(
    analysis: SessionAnalysis,
    output_path: Path,
    *,
    fps: int = 2,
    width: int = 640,
    height: int = 360,
) -> None:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "MP4 export requires imageio-ffmpeg. Install the project dependencies first."
        ) from exc

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    total_frames = max(1, int(np.ceil(analysis.parsed_session.duration_s * fps)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_frame = _build_static_base(analysis, width, height)

    command = [
        ffmpeg_path,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-vcodec",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert process.stdin is not None
    try:
        for frame_idx in range(total_frames):
            time_s = frame_idx / fps
            frame = _draw_frame(analysis, time_s, width, height, base_frame)
            process.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
    finally:
        process.stdin.close()

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg exited with status {return_code}.")
