"""Quantify the effect of linear-interpolation oversampling on DR accuracy.

Runs the heuristic and AEOLUS analyzers on (original IMU log, oversampled
IMU log) and reports mean cursor-to-GT distance in mouth-normalized units
against the video wrist trajectory, plus the % change.

Nyquist–Shannon predicts ~no change: linear interpolation in the time
domain has the same Fourier spectrum below the original Nyquist frequency
and zero spectral content above it, so the integrator's reachable signal
content is unchanged. Run it and report the actual numbers anyway.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import analyze_session, parse_sensor_log
from ringbrush_coverage.video_anchor import (
    MOUTH_X_MAX,
    MOUTH_X_MIN,
    MOUTH_Y_MAX,
    MOUTH_Y_MIN,
)


def load_gt(sync_csv: Path, landmark: str = "wrist") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ts, xs, ys = [], [], []
    with sync_csv.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            t_raw = row.get("t_ms_imu_relative", "")
            x_raw = row.get(f"{landmark}_x", "")
            y_raw = row.get(f"{landmark}_y", "")
            if not t_raw or not x_raw or not y_raw:
                continue
            try:
                ts.append(float(t_raw) / 1000.0)
                xs.append(float(x_raw))
                ys.append(float(y_raw))
            except ValueError:
                continue
    ts_a = np.array(ts); xs_a = np.array(xs); ys_a = np.array(ys)
    x_lo, x_hi = np.percentile(xs_a, 2), np.percentile(xs_a, 98)
    y_lo, y_hi = np.percentile(ys_a, 2), np.percentile(ys_a, 98)
    nx = MOUTH_X_MIN + (np.clip(xs_a, x_lo, x_hi) - x_lo) / max(x_hi - x_lo, 1e-6) * (MOUTH_X_MAX - MOUTH_X_MIN)
    ny = MOUTH_Y_MIN + (np.clip(ys_a, y_lo, y_hi) - y_lo) / max(y_hi - y_lo, 1e-6) * (MOUTH_Y_MAX - MOUTH_Y_MIN)
    return ts_a, nx, ny


def mean_cursor_to_gt(analysis, gt_ts, gt_x, gt_y) -> tuple[float, int]:
    centers = np.array([w.center_s for w in analysis.windows])
    cx = np.array([w.cursor[0] for w in analysis.windows])
    cy = np.array([w.cursor[1] for w in analysis.windows])
    mask = (centers >= gt_ts[0]) & (centers <= gt_ts[-1])
    if mask.sum() == 0:
        return float("nan"), 0
    gx = np.interp(centers[mask], gt_ts, gt_x)
    gy = np.interp(centers[mask], gt_ts, gt_y)
    d = np.sqrt((cx[mask] - gx) ** 2 + (cy[mask] - gy) ** 2)
    return float(d.mean()), int(mask.sum())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("original_log", type=Path)
    parser.add_argument("oversampled_log", type=Path)
    parser.add_argument("sync_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    gt_ts, gt_x, gt_y = load_gt(args.sync_csv.resolve())

    rows: list[dict] = []
    for method in ("heuristic", "aeolus"):
        a_orig = analyze_session(args.original_log.resolve(), dr_method=method)
        a_over = analyze_session(args.oversampled_log.resolve(), dr_method=method)
        rmse_orig, n_orig = mean_cursor_to_gt(a_orig, gt_ts, gt_x, gt_y)
        rmse_over, n_over = mean_cursor_to_gt(a_over, gt_ts, gt_x, gt_y)
        delta_pct = ((rmse_over - rmse_orig) / rmse_orig * 100.0) if rmse_orig else float("nan")
        rows.append({
            "method": method,
            "original_rmse": rmse_orig, "original_windows": n_orig,
            "oversampled_rmse": rmse_over, "oversampled_windows": n_over,
            "delta_pct": delta_pct,
        })
        print(f"{method:10s}  orig={rmse_orig:.4f} ({n_orig}w)  over={rmse_over:.4f} ({n_over}w)  delta={delta_pct:+.1f}%")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"results": rows}, indent=2), encoding="utf-8")
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
