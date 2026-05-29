"""Per-window video Δwrist lookup for the `video-anchored` DR method.

The format-3 CSV emitted by `tools/extract_video_motion.py` carries the
wrist landmark position per video frame; `tools/sync_video_imu.py`
extends it with an `t_ms_imu_relative` column. This module converts that
into a list of per-window (Δx, Δy, has_coverage) tuples that
`core.analyze_session` can swap in for the heuristic DR output.

Coordinate convention: the video CSV holds normalized image positions
`[0, 1]` (x increases right, y increases down). The cursor logic in
`analyze_session` treats DR x/y in viz-space units calibrated so the
heuristic's per-window P90 magnitude is ~0.35. A single user-tunable
`scale` multiplies the raw video deltas before they reach the cursor;
the default of 8.0 puts the video signal in the same neighborhood as the
heuristic so the existing ±0.18 / ±0.16 clamps and follow-blend stay
meaningful.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from ringbrush_coverage.core import (
    AEOLUS_NORMALIZATION_TARGET_P90,
    SensorSample,
    iter_windows,
)


def _load_landmark_series(
    sync_csv_path: Path,
    landmark: str = "wrist",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ts: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    with sync_csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        x_key = f"{landmark}_x"
        y_key = f"{landmark}_y"
        for row in reader:
            t_raw = row.get("t_ms_imu_relative", "")
            x_raw = row.get(x_key, "")
            y_raw = row.get(y_key, "")
            if not t_raw or not x_raw or not y_raw:
                continue
            try:
                ts.append(float(t_raw))
                xs.append(float(x_raw))
                ys.append(float(y_raw))
            except ValueError:
                continue
    return np.array(ts), np.array(xs), np.array(ys)


MOUTH_X_MIN = 0.08
MOUTH_X_MAX = 0.92
MOUTH_Y_MIN = 0.10
MOUTH_Y_MAX = 0.90


def _stretch_bbox(values: np.ndarray, target_lo: float, target_hi: float) -> tuple[np.ndarray, float, float]:
    """Map per-session p2-p98 wrist range linearly into the mouth viz range."""
    lo = float(np.percentile(values, 2.0))
    hi = float(np.percentile(values, 98.0))
    span = max(hi - lo, 1e-6)
    clipped = np.clip(values, lo, hi)
    return target_lo + (clipped - lo) / span * (target_hi - target_lo), lo, hi


def compute_video_dr_per_window(
    samples: list[SensorSample],
    sync_csv_path: Path,
    *,
    window_size: int = 80,
    window_step: int = 20,
    scale: float = 1.0,
    landmark: str = "wrist",
) -> list[tuple[float, float, bool, float, float]]:
    """Build the per-window video signal for `dr_method='video-anchored'`.

    Returns one tuple per analysis window:
        (dx, dy, has_coverage, target_cursor_x, target_cursor_y)

    * `dx, dy` are the rescaled-Δwrist values fed into the existing
      cursor-nudge logic so the per-window DR field on the returned
      WindowPrediction stays populated and the motion gate sees a
      realistic per-window magnitude. The rescale mirrors the AEOLUS
      path: P90 of |component| -> AEOLUS_NORMALIZATION_TARGET_P90,
      times the user `scale`.
    * `target_cursor_x, target_cursor_y` are the wrist position at the
      window center, mapped from per-session p2-p98 wrist bbox into the
      cursor's mouth-normalized [0.08, 0.92] × [0.10, 0.90] frame.
      analyze_session uses this as the cursor target whenever video
      coverage exists, bypassing the zone-anchor + DR nudge blend so the
      cursor follows the actual hand.
    """
    ts, xs, ys = _load_landmark_series(sync_csv_path, landmark=landmark)
    windows = list(iter_windows(samples, window_size, window_step))
    if ts.size < 2:
        return [(0.0, 0.0, False, 0.5, 0.5)] * len(windows)

    # Pre-compute the mouth-frame target series for every video frame.
    mouth_xs, _, _ = _stretch_bbox(xs, MOUTH_X_MIN, MOUTH_X_MAX)
    mouth_ys, _, _ = _stretch_bbox(ys, MOUTH_Y_MIN, MOUTH_Y_MAX)

    t_first_ms = samples[0].t_ms
    raw: list[tuple[float, float, bool, float, float]] = []
    for window in windows:
        start_ms = window[0].t_ms - t_first_ms
        end_ms = window[-1].t_ms - t_first_ms
        center_ms = (start_ms + end_ms) * 0.5
        if start_ms < ts[0] or end_ms > ts[-1]:
            raw.append((0.0, 0.0, False, 0.5, 0.5))
            continue
        x_start = float(np.interp(start_ms, ts, xs))
        x_end = float(np.interp(end_ms, ts, xs))
        y_start = float(np.interp(start_ms, ts, ys))
        y_end = float(np.interp(end_ms, ts, ys))
        tx = float(np.interp(center_ms, ts, mouth_xs))
        ty = float(np.interp(center_ms, ts, mouth_ys))
        raw.append((x_end - x_start, y_end - y_start, True, tx, ty))

    magnitudes = [abs(c) for dx, dy, ok, *_ in raw if ok for c in (dx, dy)]
    p90 = float(np.percentile(magnitudes, 90)) if magnitudes else 0.0
    factor = (AEOLUS_NORMALIZATION_TARGET_P90 / p90) if p90 > 1e-6 else 0.0
    factor *= scale
    return [(dx * factor, dy * factor, ok, tx, ty) for dx, dy, ok, tx, ty in raw]
