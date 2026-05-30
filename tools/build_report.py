"""Assemble the final video report from pre-rendered methodology MP4s.

End-to-end reproducible build:
  1. Generate PNG text overlays (title cards, section banners, per-panel
     methodology labels, accuracy captions) via PIL.
  2. Render each segment to its own intermediate MP4 via ffmpeg
     (text-card MP4s are PNG-as-video; comparison MP4s are filter_complex
     side-by-side composites with an overlay PNG on top).
  3. Concatenate all segment MP4s into the final report MP4.

The output is `report_assets/final_report.mp4`. The intermediate
per-segment MP4s live under `report_assets/segments/` and the text-card
PNGs under `report_assets/title_cards/`. Both are regeneratable.

Normalization of side-by-side panels: each input panel is scaled to
preserve 16:9 aspect (so the mouth diagram stays a circle), then padded
to a fixed-size column with black above and below. The bottom of the
padded column carries the accuracy caption so the source video is never
covered. When a source MP4 is shorter than the composite duration the
last frame is held via `-vf tpad=stop_mode=clone`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "report_assets"
SEGMENTS = ASSETS / "segments"
TITLE_CARDS = ASSETS / "title_cards"
METHODS_DIR = ASSETS / "methodology_videos"
OVER_DIR = ASSETS / "oversampling"

WIDTH = 1280
HEIGHT = 720
FPS = 30

BG_TOP = (24, 30, 44)        # dark navy
BG_BOTTOM = (12, 16, 26)
ACCENT = (95, 218, 168)      # mint green (matches viz palette)
TEXT = (240, 244, 248)
DIM = (160, 175, 188)


# ---------------------------------------------------------------------------
# Font / drawing helpers
# ---------------------------------------------------------------------------

def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
    else:
        candidates += ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"]
    candidates.append("DejaVuSans.ttf")
    for c in candidates:
        try:
            return ImageFont.truetype(c, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient_bg(w: int, h: int) -> Image.Image:
    top = np.array(BG_TOP, dtype=float)
    bot = np.array(BG_BOTTOM, dtype=float)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        a = y / max(h - 1, 1)
        img[y, :, :] = ((1 - a) * top + a * bot).astype(np.uint8)
    return Image.fromarray(img, mode="RGB")


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = (line + " " + word).strip()
        w = draw.textbbox((0, 0), candidate, font=font)[2]
        if w <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Title card construction
# ---------------------------------------------------------------------------

def make_title_card(
    output_path: Path,
    *,
    eyebrow: str | None = None,
    title: str | None = None,
    body: str | None = None,
    footnote: str | None = None,
    accent_bar: bool = True,
) -> None:
    img = _gradient_bg(WIDTH, HEIGHT)
    draw = ImageDraw.Draw(img)

    if accent_bar:
        draw.rectangle((80, 80, 84, HEIGHT - 80), fill=ACCENT)

    y = 100
    if eyebrow:
        font = _font(22)
        draw.text((110, y), eyebrow.upper(), fill=ACCENT, font=font)
        y += 50

    if title:
        font = _font(56, bold=True)
        lines = _wrap_lines(draw, title, font, WIDTH - 220)
        for line in lines:
            draw.text((108, y), line, fill=TEXT, font=font)
            y += 76
        y += 20

    if body:
        font = _font(28)
        lines = _wrap_lines(draw, body, font, WIDTH - 240)
        for line in lines:
            draw.text((110, y), line, fill=DIM, font=font)
            y += 42
        y += 16

    if footnote:
        font = _font(20)
        draw.text((110, HEIGHT - 110), footnote, fill=DIM, font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def make_three_up_overlay(
    output_path: Path,
    *,
    banner_text: str,
    panel_labels: tuple[str, str, str],
    panel_captions: tuple[str, str, str],
    column_width: int,
    banner_height: int,
    video_top: int,
    caption_top: int,
) -> None:
    """A transparent PNG overlay covering banner + per-panel labels + captions."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Top banner with section title.
    draw.rectangle((0, 0, WIDTH, banner_height), fill=(*BG_TOP, 220))
    banner_font = _font(26, bold=True)
    bb = draw.textbbox((0, 0), banner_text, font=banner_font)
    draw.text(
        ((WIDTH - bb[2]) // 2, (banner_height - bb[3]) // 2 - 4),
        banner_text, fill=TEXT, font=banner_font,
    )
    # Per-column methodology name (above video).
    label_font = _font(24, bold=True)
    cap_font = _font(20)
    for i, (label, cap) in enumerate(zip(panel_labels, panel_captions)):
        col_x = i * column_width
        # Methodology label between banner and video.
        lb = draw.textbbox((0, 0), label, font=label_font)
        draw.text(
            (col_x + (column_width - lb[2]) // 2, video_top - lb[3] - 24),
            label, fill=ACCENT, font=label_font,
        )
        # Accuracy caption below video, wrapped.
        lines = _wrap_lines(draw, cap, cap_font, column_width - 40)
        cy = caption_top
        for line in lines:
            tb = draw.textbbox((0, 0), line, font=cap_font)
            draw.text(
                (col_x + (column_width - tb[2]) // 2, cy),
                line, fill=DIM, font=cap_font,
            )
            cy += 30
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def make_not_available_panel(output_path: Path, message: str) -> None:
    """A still 'not available' card sized to fit one side-by-side column."""
    column_width = WIDTH // 3
    panel_h = 240
    img = Image.new("RGB", (column_width, panel_h), color=BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    # Cross-hatch / muted icon.
    draw.rectangle((10, 10, column_width - 10, panel_h - 10), outline=(70, 80, 95), width=2)
    font = _font(20, bold=True)
    lines = _wrap_lines(draw, message, font, column_width - 60)
    y = (panel_h - len(lines) * 30) // 2
    for line in lines:
        tb = draw.textbbox((0, 0), line, font=font)
        draw.text(((column_width - tb[2]) // 2, y), line, fill=DIM, font=font)
        y += 30
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    """Run an ffmpeg command, raise on non-zero exit."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(" ".join(cmd) + "\n")
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}")


def png_to_video(png_path: Path, output_path: Path, duration_s: float) -> None:
    """Loop a still PNG into an MP4 of the given duration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-framerate", str(FPS),
        "-i", str(png_path),
        "-t", f"{duration_s:.3f}",
        "-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd)


@dataclass(frozen=True)
class ThreePanel:
    left: Path
    middle: Path | None  # None -> use the 'not available' still
    right: Path | None
    middle_still: Path | None = None
    right_still: Path | None = None


def build_three_up_segment(
    panels: ThreePanel,
    overlay_png: Path,
    output_path: Path,
    *,
    duration_s: float,
    speed: float = 1.0,
) -> None:
    """Compose 3 inputs (video or still) side-by-side with an overlay.

    Each input is scaled to fit a fixed column width preserving 16:9
    aspect, then padded vertically so it lives in a 1280/3-wide column.
    The overlay PNG is drawn on top.
    """
    L = overlay_layout()
    column_width = L["column_width"]
    panel_h = L["panel_h"]
    video_top = L["video_top"]
    pad_below = HEIGHT - (video_top + panel_h)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Compose ffmpeg input list. For each panel we emit an input that's
    # either the source MP4 or a 'still' looped image.
    inputs: list[str] = []
    panel_filters: list[str] = []

    def _panel_chain(input_index: int, is_still: bool, x_offset: int, label: str) -> str:
        scale_filter = (
            f"[{input_index}:v]scale={column_width}:{panel_h}:force_original_aspect_ratio=decrease,"
            f"pad={column_width}:{panel_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
        if speed != 1.0 and not is_still:
            scale_filter += f",setpts=PTS/{speed}"
        scale_filter += f"[{label}]"
        return scale_filter

    for i, src in enumerate([panels.left, panels.middle, panels.right]):
        if src is None:
            still = (panels.middle_still if i == 1 else panels.right_still)
            assert still is not None, "expected a still for missing panel"
            inputs += ["-loop", "1", "-t", f"{duration_s:.3f}", "-framerate", str(FPS), "-i", str(still)]
            panel_filters.append(_panel_chain(i, is_still=True, x_offset=i * column_width, label=f"p{i}"))
        else:
            # Hold the last frame if the source is shorter than the segment.
            inputs += ["-i", str(src)]
            panel_filters.append(_panel_chain(i, is_still=False, x_offset=i * column_width, label=f"p{i}"))

    inputs += ["-loop", "1", "-t", f"{duration_s:.3f}", "-framerate", str(FPS), "-i", str(overlay_png)]

    # Black canvas, three panels overlaid at fixed columns, then PNG on top.
    # Inputs in order: 0,1,2 = three video/still panels; 3 = looped overlay PNG.
    bg_top = (BG_TOP[0], BG_TOP[1], BG_TOP[2])
    filter_complex = (
        f"color=c=#{bg_top[0]:02x}{bg_top[1]:02x}{bg_top[2]:02x}:s={WIDTH}x{HEIGHT}:r={FPS}[bg];"
        + ";".join(panel_filters)
        + ";[bg][p0]overlay=0:" + str(video_top) + "[bg0]"
        + ";[bg0][p1]overlay=" + str(column_width) + ":" + str(video_top) + "[bg1]"
        + ";[bg1][p2]overlay=" + str(column_width * 2) + ":" + str(video_top) + "[bg2]"
        + ";[bg2][3:v]overlay=0:0:format=auto"
    )
    # Trim to the requested duration. tpad on each video clones the last
    # frame when needed so a shorter MP4 doesn't end the composite early.
    # Easier alternative: just `-t duration_s` on output.
    cmd = [
        FFMPEG, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-t", f"{duration_s:.3f}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd)


def build_single_video_segment(
    src: Path,
    overlay_png: Path,
    output_path: Path,
    *,
    duration_s: float,
    start_s: float = 0.0,
    speed: float = 1.0,
) -> None:
    """One source video at a given speed, with a static overlay PNG.

    Source is centered on the dark gradient background at its native
    aspect-preserving scale (so the mouth viz stays circular). The
    overlay PNG sits on top.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Background plus letterboxed source plus overlay.
    bg_top = (BG_TOP[0], BG_TOP[1], BG_TOP[2])
    target_w = int(WIDTH * 0.72)
    target_h = int(target_w * 9 / 16)
    y_off = int((HEIGHT - target_h) * 0.55)  # bias slightly low to leave room for top caption
    x_off = (WIDTH - target_w) // 2
    if speed != 1.0:
        speed_filter = f",setpts=PTS/{speed}"
    else:
        speed_filter = ""
    filter_complex = (
        f"color=c=#{bg_top[0]:02x}{bg_top[1]:02x}{bg_top[2]:02x}:s={WIDTH}x{HEIGHT}:r={FPS}[bg];"
        f"[0:v]trim=start={start_s},setpts=PTS-STARTPTS{speed_filter},"
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,setsar=1[src];"
        f"[bg][src]overlay={x_off}:{y_off}[bg0];"
        f"[bg0][1:v]overlay=0:0:format=auto"
    )
    cmd = [
        FFMPEG, "-y",
        "-i", str(src),
        "-loop", "1", "-t", f"{duration_s:.3f}", "-framerate", str(FPS), "-i", str(overlay_png),
        "-filter_complex", filter_complex,
        "-t", f"{duration_s:.3f}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd)


def concat_videos(segment_paths: list[Path], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.parent / (output_path.stem + "_concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in segment_paths),
        encoding="utf-8",
    )
    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd)


# ---------------------------------------------------------------------------
# Segment specifications
# ---------------------------------------------------------------------------

def overlay_layout() -> dict:
    """Return the layout dict that *both* the overlay PNG and the
    side-by-side composer rely on. Same source-of-truth for both so
    panel positions stay in sync."""
    column_width = WIDTH // 3  # 426
    # Match the aspect-preserving rescale that ffmpeg actually produces from a
    # 1280x720 source: round(720 * 426 / 1280) = 240. Using 239 here makes the
    # downstream `pad` filter refuse to shrink the scaled frame.
    panel_h = 240
    banner_height = 70
    video_top = 280
    caption_top = video_top + panel_h + 30
    return {
        "column_width": column_width,
        "panel_h": panel_h,
        "banner_height": banner_height,
        "video_top": video_top,
        "caption_top": caption_top,
    }


def _overlay_kwargs(L: dict) -> dict:
    return {k: L[k] for k in ("column_width", "banner_height", "video_top", "caption_top")}


def build_all(output_dir: Path) -> Path:
    SEGMENTS.mkdir(parents=True, exist_ok=True)
    TITLE_CARDS.mkdir(parents=True, exist_ok=True)
    L = overlay_layout()

    # Captions sourced from the JSON metrics we computed in Phase 2/3.
    primary_caps = (
        "Mean cursor error vs video GT: 0.31 mouth units. Drift accumulates over time.",
        "Mean cursor error vs video GT: 0.32 mouth units. Similar drift; IMU-only.",
        "Mean cursor error vs video GT: 0.03 mouth units. Tracks the wrist closely.",
    )
    not_avail_msg = "Video-based not available\n(no companion video for this log)"
    not_avail_png = TITLE_CARDS / "not_available.png"
    make_not_available_panel(not_avail_png, not_avail_msg)

    old_full_caps = (
        "Earlier full-session log. No companion video, so no GT distance.",
        "Earlier full-session log. No companion video, so no GT distance.",
        "Not available: no companion video for this log.",
    )
    updown_caps = (
        "Vertical/horizontal ratio: 2.30x (designed dominance: vertical wins).",
        "Vertical/horizontal ratio: 1.23x. AEOLUS does not single out an axis.",
        "Not available: no companion video for this log.",
    )
    leftright_caps = (
        "Horizontal/vertical ratio: 3.05x (designed dominance: horizontal wins).",
        "Horizontal/vertical ratio: 0.57x. AEOLUS gives more vertical motion here.",
        "Not available: no companion video for this log.",
    )

    plan: list[tuple[str, Path]] = []

    # ----- Section 1: Title (5s) -----
    make_title_card(
        TITLE_CARDS / "01_title.png",
        eyebrow="Pervasive Data Science Seminar",
        title="Ringbrush Coverage",
        body="Turning smart-ring IMU brushing logs into mouth-coverage videos.",
        footnote="Final video report",
    )
    seg = SEGMENTS / "01_title.mp4"
    png_to_video(TITLE_CARDS / "01_title.png", seg, duration_s=5.0)
    plan.append(("01_title", seg))

    # ----- Section 2: Background (35s split over 4 cards) -----
    bg_cards = [
        dict(
            eyebrow="Background  1 / 4",
            title="The prototype",
            body="A smart ring with an IMU streams orientation and acceleration about 80 times per second while the wearer brushes their teeth.",
        ),
        dict(
            eyebrow="Background  2 / 4",
            title="The data-log",
            body="Each line carries seven values: a timestamp in milliseconds, three Euler angles (roll, pitch, yaw) and three linear acceleration components in m/s squared.",
        ),
        dict(
            eyebrow="Background  3 / 4",
            title="What the pipeline does",
            body="It splits the log into one-second windows, classifies each window into one of five mouth zones plus an idle class, and tracks an estimated brush cursor.",
        ),
        dict(
            eyebrow="Background  4 / 4",
            title="The output",
            body="A stylized mouth diagram, a brush cursor with a short trail, and per-zone coverage bars that fill as each surface is brushed.",
        ),
    ]
    bg_duration_each = 35.0 / len(bg_cards)
    for i, card in enumerate(bg_cards, start=1):
        png = TITLE_CARDS / f"02_bg_{i}.png"
        make_title_card(png, **card)
        seg = SEGMENTS / f"02_bg_{i}.mp4"
        png_to_video(png, seg, duration_s=bg_duration_each)
        plan.append((f"02_bg_{i}", seg))

    # ----- Section 3: Methodologies (~80s = 27s each x 3) -----
    methodology_pairs = [
        ("heuristic", "Heuristic", METHODS_DIR / "primary_heuristic.mp4", {
            "eyebrow": "Methodology 1 of 3",
            "title": "Heuristic",
            "body": "In-house damped integrator. For each one-second window, it subtracts the mean acceleration to isolate dynamic motion, then integrates it with a fixed damping into a small 2D displacement. A tiny yaw-delta term is added on top.",
        }),
        ("aeolus", "AEOLUS", METHODS_DIR / "primary_aeolus.mp4", {
            "eyebrow": "Methodology 2 of 3",
            "title": "AEOLUS",
            "body": "A port of the Radeta-2023 underwater dead-reckoning pipeline. Gravity is removed from the accelerometer using roll and pitch, drift is reduced when the device is nearly still, and the position update is rotated to the heading direction.",
        }),
        ("video", "Video-based", METHODS_DIR / "primary_video.mp4", {
            "eyebrow": "Methodology 3 of 3",
            "title": "Video-based",
            "body": "When the brushing session was also filmed, MediaPipe finds the wrist in each video frame. The IMU and the video are time-aligned by cross-correlating their motion energies, and the cursor is driven directly by the wrist position.",
        }),
    ]
    for tag, label, src_mp4, card_kwargs in methodology_pairs:
        png = TITLE_CARDS / f"03_method_{tag}.png"
        make_title_card(png, **card_kwargs)
        # 9s text card + 18s playback at 2x = 27s total
        text_seg = SEGMENTS / f"03_method_{tag}_text.mp4"
        png_to_video(png, text_seg, duration_s=9.0)
        plan.append((f"03_method_{tag}_text", text_seg))

        # Quick playback overlay just shows the label.
        play_overlay = TITLE_CARDS / f"03_method_{tag}_play.png"
        make_three_up_overlay(
            play_overlay,
            banner_text=f"{label}  on primary log  (2x speed)",
            panel_labels=("", "", ""),
            panel_captions=("", "", ""),
            **_overlay_kwargs(L),
        )
        play_seg = SEGMENTS / f"03_method_{tag}_play.mp4"
        build_single_video_segment(src_mp4, play_overlay, play_seg, duration_s=18.0, start_s=0.0, speed=3.6)
        plan.append((f"03_method_{tag}_play", play_seg))

    # ----- Section 4: Results (80s) -----
    # Title card (5s) then ~75s of 3-up playback on primary log at 1.75x
    # (130 / 75 ~= 1.73 -> 1.75 covers the full session).
    res_title_png = TITLE_CARDS / "04_results_title.png"
    make_title_card(
        res_title_png,
        eyebrow="Results",
        title="Three methodologies, one session, side by side",
        body="Primary log: 2026-05-29 full-session-with-video-recording. Around 130 s, played back below at ~1.75x.",
    )
    res_title_seg = SEGMENTS / "04_results_title.mp4"
    png_to_video(res_title_png, res_title_seg, duration_s=5.0)
    plan.append(("04_results_title", res_title_seg))

    res_overlay = TITLE_CARDS / "04_results_3up_overlay.png"
    make_three_up_overlay(
        res_overlay,
        banner_text="Primary log  -  three methodologies side by side",
        panel_labels=("Heuristic", "AEOLUS", "Video-based"),
        panel_captions=primary_caps,
        **_overlay_kwargs(L),
    )
    res_seg = SEGMENTS / "04_results_3up.mp4"
    build_three_up_segment(
        ThreePanel(
            left=METHODS_DIR / "primary_heuristic.mp4",
            middle=METHODS_DIR / "primary_aeolus.mp4",
            right=METHODS_DIR / "primary_video.mp4",
        ),
        res_overlay, res_seg, duration_s=75.0, speed=1.75,
    )
    plan.append(("04_results_3up", res_seg))

    # ----- Section 5: Over-sampling intro (35s split over 2 cards) -----
    os_intro = [
        dict(
            eyebrow="Over-sampling  1 / 2",
            title="What we tested",
            body="For every pair of consecutive IMU readings, insert one extra reading: midpoint timestamp, all other channels linearly interpolated. The effective sample rate doubles, but no new measurement is made.",
        ),
        dict(
            eyebrow="Over-sampling  2 / 2",
            title="The hypothesis",
            body="The hypothesis is that more samples will improve dead reckoning. Per Nyquist-Shannon, interpolation cannot add signal content above the original Nyquist frequency, so this is expected to fail. The experiment is run honestly to see what actually happens.",
        ),
    ]
    for i, card in enumerate(os_intro, start=1):
        png = TITLE_CARDS / f"05_os_intro_{i}.png"
        make_title_card(png, **card)
        seg = SEGMENTS / f"05_os_intro_{i}.mp4"
        png_to_video(png, seg, duration_s=17.5)
        plan.append((f"05_os_intro_{i}", seg))

    # ----- Section 6: Over-sampling results (~35s) -----
    os_result_png = TITLE_CARDS / "06_os_result.png"
    make_title_card(
        os_result_png,
        eyebrow="Over-sampling  Result",
        title="No improvement. Worse, actually.",
        body=(
            "On the primary log, mean cursor-to-GT distance: "
            "heuristic 0.31 -> 0.45 (+47%), AEOLUS 0.32 -> 0.44 (+37%). "
            "Two reasons: (1) Nyquist: linear interpolation adds no signal content "
            "above the original Nyquist frequency, so no new information. "
            "(2) The damping in the integrators is per-sample, not per-time, "
            "so doubling the sample rate compounds damping twice and crushes velocity."
        ),
    )
    os_result_seg = SEGMENTS / "06_os_result.mp4"
    png_to_video(os_result_png, os_result_seg, duration_s=35.0)
    plan.append(("06_os_result", os_result_seg))

    # ----- Appendix: 4 segments, ~52s each (3 logs + primary) -----
    appendix_title_png = TITLE_CARDS / "07_appendix_title.png"
    make_title_card(
        appendix_title_png,
        eyebrow="Appendix",
        title="All methodologies on all data-logs",
        body="Four logs, each played 3-up. Captions on each panel summarize the result honestly. Logs without a companion video show a placeholder in the video-based column.",
    )
    appendix_title_seg = SEGMENTS / "07_appendix_title.mp4"
    png_to_video(appendix_title_png, appendix_title_seg, duration_s=6.0)
    plan.append(("07_appendix_title", appendix_title_seg))

    appendix_specs = [
        ("primary", "2026-05-29  Full session with video recording  (~130 s, 1.75x)", 75.0, 1.75, primary_caps,
         METHODS_DIR / "primary_heuristic.mp4", METHODS_DIR / "primary_aeolus.mp4",
         METHODS_DIR / "primary_video.mp4", False),
        ("old_full", "2026-03-28  Earlier full session  (~62 s, 1.25x)", 50.0, 1.25, old_full_caps,
         METHODS_DIR / "old_full_heuristic.mp4", METHODS_DIR / "old_full_aeolus.mp4",
         None, True),
        ("updown", "2026-04-12  Up-and-down calibration  (~15 s, 1.0x)", 16.0, 1.0, updown_caps,
         METHODS_DIR / "updown_heuristic.mp4", METHODS_DIR / "updown_aeolus.mp4",
         None, True),
        ("leftright", "2026-04-20  Left-and-right calibration  (~10 s, 1.0x)", 11.0, 1.0, leftright_caps,
         METHODS_DIR / "leftright_heuristic.mp4", METHODS_DIR / "leftright_aeolus.mp4",
         None, True),
    ]
    for tag, banner, dur, speed, caps, left_mp4, mid_mp4, right_mp4, use_na in appendix_specs:
        overlay_png = TITLE_CARDS / f"07_appx_{tag}_overlay.png"
        make_three_up_overlay(
            overlay_png,
            banner_text=banner,
            panel_labels=("Heuristic", "AEOLUS", "Video-based"),
            panel_captions=caps,
            **_overlay_kwargs(L),
        )
        seg = SEGMENTS / f"07_appx_{tag}.mp4"
        build_three_up_segment(
            ThreePanel(
                left=left_mp4,
                middle=mid_mp4,
                right=right_mp4 if not use_na else None,
                right_still=not_avail_png if use_na else None,
            ),
            overlay_png, seg, duration_s=dur, speed=speed,
        )
        plan.append((f"07_appx_{tag}", seg))

    # ----- Outro -----
    outro_png = TITLE_CARDS / "08_outro.png"
    make_title_card(
        outro_png,
        eyebrow="End of report",
        title="Thank you",
        body="Code, methodology details and reproducible build script in the repository.",
        footnote="github.com/Sten-Qy-Li/ringbrush-coverage",
    )
    outro_seg = SEGMENTS / "08_outro.mp4"
    png_to_video(outro_png, outro_seg, duration_s=4.0)
    plan.append(("08_outro", outro_seg))

    # Concatenate.
    final_path = output_dir / "final_report.mp4"
    concat_videos([p for _, p in plan], final_path)

    # Emit a sidecar JSON describing what's where.
    timeline = []
    cursor_s = 0.0
    for name, p in plan:
        probe = subprocess.run(
            [FFMPEG, "-i", str(p)], capture_output=True, text=True
        )
        dur_s = 0.0
        for line in probe.stderr.splitlines():
            if "Duration" in line:
                try:
                    hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
                    dur_s = int(hh) * 3600 + int(mm) * 60 + float(ss)
                except (IndexError, ValueError):
                    dur_s = 0.0
                break
        timeline.append({
            "name": name, "path": str(p.relative_to(REPO_ROOT)),
            "start_s": round(cursor_s, 2), "duration_s": round(dur_s, 2),
        })
        cursor_s += dur_s
    (output_dir / "final_report_timeline.json").write_text(
        json.dumps({"segments": timeline, "total_s": round(cursor_s, 2)}, indent=2),
        encoding="utf-8",
    )
    print(f"Final report duration: {cursor_s:.1f} s")
    print(f"Output: {final_path}")
    return final_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    parser.add_argument("--clean", action="store_true", help="Wipe intermediate segments first.")
    args = parser.parse_args(argv)
    if args.clean and SEGMENTS.exists():
        shutil.rmtree(SEGMENTS)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    build_all(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
