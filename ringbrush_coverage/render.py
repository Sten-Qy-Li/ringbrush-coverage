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


def _layout(width: int, height: int) -> dict:
    """Compute the box geometry for header, sidebar, mouth viz, status pill, and timeline.

    All regions are guaranteed not to overlap. Decoration overhang from the mouth viz
    (drop shadow + lip arcs) is absorbed into the mouth box's outer margins so the
    decorations stay clear of every other panel.
    """
    short_side = min(width, height)
    pad = max(20, int(short_side * 0.045))
    gutter = max(10, int(short_side * 0.022))

    header_top = pad
    header_height = max(72, int(height * 0.13))
    header_bottom = header_top + header_height

    timeline_height = max(8, int(height * 0.014))
    timeline_top = height - pad - timeline_height
    timing_band_height = max(22, int(height * 0.04))

    pill_height = max(40, int(height * 0.072))
    pill_bottom = timeline_top - timing_band_height
    pill_top = pill_bottom - pill_height

    sidebar_width = max(260, int(width * 0.27))
    sidebar_left = width - pad - sidebar_width
    sidebar_right = width - pad
    sidebar_top = header_bottom + gutter
    sidebar_bottom = pill_top - gutter

    deco_x = max(18, int(short_side * 0.025))
    deco_top = max(10, int(short_side * 0.014))
    deco_bottom = max(28, int(short_side * 0.07))
    mouth_left = pad + deco_x
    mouth_right = sidebar_left - gutter - deco_x
    mouth_top = sidebar_top + deco_top
    mouth_bottom = pill_top - gutter - deco_bottom

    pill_right = max(pad + 280, int(width * 0.42))

    title_x = pad + max(8, int(width * 0.005))
    title_y = header_top + max(2, int(height * 0.006))
    subtitle_y = title_y + max(36, int(height * 0.075))

    return {
        "pad": pad,
        "gutter": gutter,
        "title_anchor": (title_x, title_y),
        "subtitle_anchor": (title_x, subtitle_y),
        "sidebar_box": (sidebar_left, sidebar_top, sidebar_right, sidebar_bottom),
        "pill_box": (pad, pill_top, pill_right, pill_bottom),
        "timeline_box": (pad, timeline_top, width - pad, timeline_top + timeline_height),
        "timing_text_y": timeline_top - timing_band_height + max(2, int(height * 0.004)),
        "timing_text_right": width - pad,
        "mouth_box": (mouth_left, mouth_top, mouth_right, mouth_bottom),
    }


def _coverage_bar_geometry(layout: dict, height: int) -> dict:
    sleft, stop, sright, sbottom = layout["sidebar_box"]
    panel_pad = max(14, int(height * 0.025))
    heading_y = stop + panel_pad
    heading_height = max(28, int(height * 0.045))
    panel_inner_top = heading_y + heading_height + max(8, int(height * 0.018))
    panel_inner_bottom = sbottom - panel_pad
    bar_thickness = max(10, int(height * 0.025))
    section_h = (panel_inner_bottom - panel_inner_top) / len(SURFACE_LABELS)
    return {
        "panel_pad": panel_pad,
        "heading_y": heading_y,
        "heading_height": heading_height,
        "panel_inner_top": panel_inner_top,
        "panel_inner_bottom": panel_inner_bottom,
        "section_h": section_h,
        "bar_thickness": bar_thickness,
        "bar_left": sleft + panel_pad,
        "bar_right": sright - panel_pad,
    }


def _build_static_base(analysis: SessionAnalysis, width: int, height: int, layout: dict) -> Image.Image:
    image = _gradient_background(width, height).copy().convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")

    title_size = max(24, int(height * 0.055))
    subtitle_size = max(14, int(height * 0.026))
    panel_label_size = max(15, int(height * 0.030))
    bar_label_size = max(12, int(height * 0.024))

    title_font = _load_font(title_size, bold=True)
    subtitle_font = _load_font(subtitle_size)
    panel_label_font = _load_font(panel_label_size, bold=True)
    bar_label_font = _load_font(bar_label_size)

    mouth_box = layout["mouth_box"]
    left, top, right, bottom = mouth_box
    mouth_w = right - left
    mouth_h = bottom - top

    shadow_x = max(6, int(mouth_w * 0.014))
    shadow_top = max(8, int(mouth_h * 0.06))
    shadow_bottom = max(20, int(mouth_h * 0.13))
    arc_top_x = max(8, int(mouth_w * 0.016))
    arc_top_top = max(4, int(mouth_h * 0.020))
    arc_top_bottom = max(6, int(mouth_h * 0.030))
    arc_low_x = max(10, int(mouth_w * 0.019))
    arc_low_top = max(6, int(mouth_h * 0.025))
    arc_low_bottom = max(12, int(mouth_h * 0.075))
    outline_w = max(4, int(mouth_h * 0.018))
    arc_top_w = max(3, int(mouth_h * 0.014))
    arc_low_w = max(2, int(mouth_h * 0.010))

    draw.ellipse(
        (left - shadow_x, top + shadow_top, right + shadow_x, bottom + shadow_bottom),
        fill=(78, 24, 18, 35),
    )
    draw.ellipse(mouth_box, fill=(66, 10, 15, 255), outline=(199, 82, 70, 255), width=outline_w)
    draw.arc(
        (left - arc_top_x, top - arc_top_top, right + arc_top_x, bottom + arc_top_bottom),
        start=204,
        end=336,
        fill=(245, 171, 149, 190),
        width=arc_top_w,
    )
    draw.arc(
        (left - arc_low_x, top + arc_low_top, right + arc_low_x, bottom + arc_low_bottom),
        start=24,
        end=156,
        fill=(161, 47, 50, 180),
        width=arc_low_w,
    )

    title_x, title_y = layout["title_anchor"]
    draw.text((title_x, title_y), "Ringbrush Coverage", fill=(35, 38, 42, 255), font=title_font)
    subtitle_x, subtitle_y = layout["subtitle_anchor"]
    subtitle = f"{analysis.source_path.name}  |  calibration: {analysis.calibration_source}"
    draw.text((subtitle_x, subtitle_y), subtitle, fill=(80, 90, 92, 255), font=subtitle_font)

    pill_box = layout["pill_box"]
    pill_radius = max(14, int((pill_box[3] - pill_box[1]) * 0.45))
    draw.rounded_rectangle(pill_box, radius=pill_radius, fill=(255, 255, 255, 225))

    sidebar_box = layout["sidebar_box"]
    sidebar_radius = max(16, int(height * 0.028))
    draw.rounded_rectangle(sidebar_box, radius=sidebar_radius, fill=(255, 255, 255, 225))

    bar_geom = _coverage_bar_geometry(layout, height)
    draw.text(
        (bar_geom["bar_left"], bar_geom["heading_y"]),
        "Coverage so far",
        fill=(35, 40, 44, 255),
        font=panel_label_font,
    )

    for idx, label in enumerate(SURFACE_LABELS):
        section_top = bar_geom["panel_inner_top"] + (idx * bar_geom["section_h"])
        bar_top = int(section_top + bar_geom["section_h"] - bar_geom["bar_thickness"] - max(2, int(bar_geom["section_h"] * 0.05)))
        draw.text(
            (bar_geom["bar_left"], int(section_top)),
            DISPLAY_NAMES[label],
            fill=(60, 68, 70, 255),
            font=bar_label_font,
        )
        draw.rounded_rectangle(
            (bar_geom["bar_left"], bar_top, bar_geom["bar_right"], bar_top + bar_geom["bar_thickness"]),
            radius=bar_geom["bar_thickness"] // 2,
            fill=(229, 235, 231, 255),
        )

    timeline_box = layout["timeline_box"]
    timeline_radius = max(2, (timeline_box[3] - timeline_box[1]) // 2)
    draw.rounded_rectangle(timeline_box, radius=timeline_radius, fill=(215, 222, 221, 255))

    return image


def _draw_frame(
    analysis: SessionAnalysis,
    time_s: float,
    width: int,
    height: int,
    base_frame: Image.Image,
    layout: dict,
) -> Image.Image:
    image = base_frame.copy()
    draw = ImageDraw.Draw(image, "RGBA")

    pill_text_size = max(14, int(height * 0.030))
    bar_pct_size = max(12, int(height * 0.024))
    timing_size = max(13, int(height * 0.025))
    pill_font = _load_font(pill_text_size, bold=True)
    pct_font = _load_font(bar_pct_size, bold=True)
    timing_font = _load_font(timing_size)

    state = _interpolate_state(analysis, time_s)
    current_ratio = {
        label: 1.0 - np.exp(-(state["coverage"][label] / max(analysis.target_zone_seconds, 1e-6)))
        for label in SURFACE_LABELS
    }

    mouth_box = layout["mouth_box"]
    left, top, right, bottom = mouth_box
    mouth_h = bottom - top

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
    trail_w = max(3, int(mouth_h * 0.012))
    for idx in range(1, len(trail_pixels)):
        opacity = int(35 + (idx / max(len(trail_pixels) - 1, 1)) * 120)
        draw.line((trail_pixels[idx - 1], trail_pixels[idx]), fill=(255, 233, 190, opacity), width=trail_w)

    cursor_x = left + (state["cursor"][0] * (right - left))
    cursor_y = top + (state["cursor"][1] * (bottom - top))
    cursor_radius = max(8, int(mouth_h * 0.025))
    _draw_glow(draw, (cursor_x, cursor_y), radius=cursor_radius)
    draw.ellipse(
        (cursor_x - cursor_radius - 1, cursor_y - cursor_radius - 1, cursor_x + cursor_radius + 1, cursor_y + cursor_radius + 1),
        fill=(254, 241, 179, 255),
        outline=(255, 255, 255, 255),
        width=2,
    )

    pill_box = layout["pill_box"]
    pleft, ptop, pright, pbottom = pill_box
    dominant_label = state["dominant"]
    dominant_text = f"Current region: {DISPLAY_NAMES.get(dominant_label, dominant_label)}"
    pill_text_x = pleft + max(18, int((pright - pleft) * 0.06))
    pill_bbox = draw.textbbox((0, 0), dominant_text, font=pill_font)
    pill_text_y = ptop + ((pbottom - ptop) - (pill_bbox[3] - pill_bbox[1])) // 2 - 2
    draw.text((pill_text_x, pill_text_y), dominant_text, fill=(35, 46, 47, 255), font=pill_font)

    bar_geom = _coverage_bar_geometry(layout, height)
    for idx, label in enumerate(SURFACE_LABELS):
        ratio = float(current_ratio[label])
        section_top = bar_geom["panel_inner_top"] + (idx * bar_geom["section_h"])
        bar_top = int(section_top + bar_geom["section_h"] - bar_geom["bar_thickness"] - max(2, int(bar_geom["section_h"] * 0.05)))
        bar_left = bar_geom["bar_left"]
        bar_right = bar_geom["bar_right"]
        bar_thickness = bar_geom["bar_thickness"]
        fill_right = bar_left + ((bar_right - bar_left) * ratio)
        if fill_right > bar_left + (bar_thickness // 2):
            draw.rounded_rectangle(
                (bar_left, bar_top, fill_right, bar_top + bar_thickness),
                radius=bar_thickness // 2,
                fill=(*_mix("#90c8a2", "#3fc278", ratio), 255),
            )
        pct_text = f"{int(round(ratio * 100))}%"
        pct_bbox = draw.textbbox((0, 0), pct_text, font=pct_font)
        draw.text(
            (bar_right - (pct_bbox[2] - pct_bbox[0]), int(section_top)),
            pct_text,
            fill=(54, 96, 76, 255),
            font=pct_font,
        )

    total_duration = max(analysis.parsed_session.duration_s, 1e-6)
    progress = max(0.0, min(1.0, time_s / total_duration))
    tleft, ttop, tright, tbottom = layout["timeline_box"]
    timeline_radius = max(2, (tbottom - ttop) // 2)
    fill_right = tleft + ((tright - tleft) * progress)
    if fill_right > tleft + timeline_radius:
        draw.rounded_rectangle(
            (tleft, ttop, fill_right, tbottom),
            radius=timeline_radius,
            fill=(81, 184, 122, 255),
        )

    timing = f"{time_s:5.1f}s / {analysis.parsed_session.duration_s:5.1f}s"
    timing_bbox = draw.textbbox((0, 0), timing, font=timing_font)
    draw.text(
        (layout["timing_text_right"] - timing_bbox[2], layout["timing_text_y"]),
        timing,
        fill=(63, 72, 73, 255),
        font=timing_font,
    )

    return image.convert("RGB")


def render_mp4(
    analysis: SessionAnalysis,
    output_path: Path,
    *,
    fps: int = 2,
    width: int = 1280,
    height: int = 720,
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
    layout = _layout(width, height)
    base_frame = _build_static_base(analysis, width, height, layout)

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
            frame = _draw_frame(analysis, time_s, width, height, base_frame, layout)
            process.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
    finally:
        process.stdin.close()

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg exited with status {return_code}.")
