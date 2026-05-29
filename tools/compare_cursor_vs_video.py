"""Compare the rendered cursor trajectory across DR methods against video ground truth.

The cursor is the visualization-time entity the user actually sees moving over the
mouth diagram. It's derived from a blend of (a) the zone-classifier weighted
anchor and (b) a clamped, scaled per-window DR nudge, with low-pass follow
damping. So the cursor trajectory is more informative than the raw per-sample
DR trajectory we compared earlier.

For each method we extract the per-window cursor (x, y) emitted by analyze_session,
compare against the video wrist position interpolated to the same window
centers, and report RMSE in the shared mouth-normalized [0, 1] frame.

Outputs (under `--output-dir`):
  * `cursor_comparison.json` — per-method RMSE summary.
  * `cursor_comparison.png`  — overlay of all cursor trajectories + video GT.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import analyze_session, parse_sensor_log
from ringbrush_coverage.video_anchor import compute_video_dr_per_window


def load_video_xy(sync_csv_path: Path, landmark: str = "wrist") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ts: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    with sync_csv_path.open("r", encoding="utf-8") as fh:
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
    return np.array(ts), np.array(xs), np.array(ys)


def _normalize_video_to_mouth_frame(video_xs: np.ndarray, video_ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map raw normalized image coords to the cursor's mouth-normalized [0.08, 0.92] × [0.10, 0.90] frame.

    A per-session linear stretch of the wrist bbox into the cursor's clamp
    range. This is the simplest sensible affine map. It won't recover the
    user's true mouth box in image coords but it gives a shape-comparable
    baseline.
    """
    if video_xs.size == 0:
        return video_xs, video_ys
    x_lo, x_hi = np.percentile(video_xs, 2.0), np.percentile(video_xs, 98.0)
    y_lo, y_hi = np.percentile(video_ys, 2.0), np.percentile(video_ys, 98.0)
    x_span = max(x_hi - x_lo, 1e-6)
    y_span = max(y_hi - y_lo, 1e-6)
    nx = 0.08 + (np.clip(video_xs, x_lo, x_hi) - x_lo) / x_span * (0.92 - 0.08)
    ny = 0.10 + (np.clip(video_ys, y_lo, y_hi) - y_lo) / y_span * (0.90 - 0.10)
    return nx, ny


def cursor_series(analysis) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (window_center_s, cursor_x, cursor_y) arrays."""
    centers = np.array([w.center_s for w in analysis.windows], dtype=float)
    xs = np.array([w.cursor[0] for w in analysis.windows], dtype=float)
    ys = np.array([w.cursor[1] for w in analysis.windows], dtype=float)
    return centers, xs, ys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("imu", type=Path)
    parser.add_argument("sync_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--landmark", default="wrist")
    args = parser.parse_args(argv)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Running heuristic DR ...", flush=True)
    a_h = analyze_session(args.imu.resolve(), dr_method="heuristic")
    print("Running AEOLUS DR ...", flush=True)
    a_a = analyze_session(args.imu.resolve(), dr_method="aeolus")
    print("Running video-anchored DR ...", flush=True)
    parsed = parse_sensor_log(args.imu.resolve())
    video_dr = compute_video_dr_per_window(parsed.samples, args.sync_csv.resolve())
    a_v = analyze_session(args.imu.resolve(), dr_method="video-anchored", video_dr_values=video_dr)

    t_h, hx, hy = cursor_series(a_h)
    t_a, ax, ay = cursor_series(a_a)
    t_v, vx, vy = cursor_series(a_v)

    raw_ts, raw_xs, raw_ys = load_video_xy(args.sync_csv.resolve(), landmark=args.landmark)
    gt_xs_norm, gt_ys_norm = _normalize_video_to_mouth_frame(raw_xs, raw_ys)

    def gt_at(centers: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mask = (centers >= raw_ts[0]) & (centers <= raw_ts[-1])
        gx = np.full_like(centers, np.nan, dtype=float)
        gy = np.full_like(centers, np.nan, dtype=float)
        gx[mask] = np.interp(centers[mask], raw_ts, gt_xs_norm)
        gy[mask] = np.interp(centers[mask], raw_ts, gt_ys_norm)
        return gx, gy, mask

    def rmse(cx, cy, gx, gy):
        mask = np.isfinite(gx) & np.isfinite(gy) & np.isfinite(cx) & np.isfinite(cy)
        if mask.sum() == 0:
            return float("nan"), 0
        d = np.sqrt((cx[mask] - gx[mask]) ** 2 + (cy[mask] - gy[mask]) ** 2)
        return float(d.mean()), int(mask.sum())

    gx_h, gy_h, _ = gt_at(t_h)
    gx_a, gy_a, _ = gt_at(t_a)
    gx_v, gy_v, _ = gt_at(t_v)

    rmse_h, n_h = rmse(hx, hy, gx_h, gy_h)
    rmse_a, n_a = rmse(ax, ay, gx_a, gy_a)
    rmse_v, n_v = rmse(vx, vy, gx_v, gy_v)

    print()
    print(f"Heuristic       : mean cursor-to-GT distance = {rmse_h:.4f} on {n_h} windows")
    print(f"AEOLUS          : mean cursor-to-GT distance = {rmse_a:.4f} on {n_a} windows")
    print(f"Video-anchored  : mean cursor-to-GT distance = {rmse_v:.4f} on {n_v} windows")
    if rmse_h > 0:
        print(f"Improvement (video vs heuristic) = {100.0 * (1.0 - rmse_v / rmse_h):+.1f} %")
    if rmse_a > 0:
        print(f"Improvement (video vs AEOLUS   ) = {100.0 * (1.0 - rmse_v / rmse_a):+.1f} %")

    # Render comparison plot.
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=130)
    ax_xy, ax_xt, ax_yt, ax_err = axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]

    ax_xy.plot(gt_xs_norm, gt_ys_norm, color="#2ca02c", linewidth=0.8, alpha=0.55, label="video GT (wrist, normalized)")
    ax_xy.plot(hx, hy, color="#1f77b4", linewidth=1.0, alpha=0.85, label=f"heuristic cursor (err {rmse_h:.3f})")
    ax_xy.plot(ax, ay, color="#ff7f0e", linewidth=1.0, alpha=0.85, label=f"aeolus cursor    (err {rmse_a:.3f})")
    ax_xy.plot(vx, vy, color="#d62728", linewidth=1.0, alpha=0.95, label=f"video-anchored  (err {rmse_v:.3f})")
    ax_xy.set_aspect("equal", adjustable="box")
    ax_xy.set_xlim(0.0, 1.0); ax_xy.set_ylim(1.0, 0.0)
    ax_xy.set_xlabel("mouth x"); ax_xy.set_ylabel("mouth y")
    ax_xy.set_title("Cursor trajectories vs video wrist GT (mouth-normalized)")
    ax_xy.grid(alpha=0.3); ax_xy.legend(fontsize=8, loc="lower left")

    ax_xt.plot(raw_ts, gt_xs_norm, color="#2ca02c", alpha=0.6, label="GT x")
    ax_xt.plot(t_h, hx, color="#1f77b4", alpha=0.85, label="heuristic x")
    ax_xt.plot(t_a, ax, color="#ff7f0e", alpha=0.85, label="aeolus x")
    ax_xt.plot(t_v, vx, color="#d62728", alpha=0.95, label="video-anchored x")
    ax_xt.set_xlabel("IMU time (s)"); ax_xt.set_title("cursor x(t)")
    ax_xt.grid(alpha=0.3); ax_xt.legend(fontsize=8)

    ax_yt.plot(raw_ts, gt_ys_norm, color="#2ca02c", alpha=0.6, label="GT y")
    ax_yt.plot(t_h, hy, color="#1f77b4", alpha=0.85, label="heuristic y")
    ax_yt.plot(t_a, ay, color="#ff7f0e", alpha=0.85, label="aeolus y")
    ax_yt.plot(t_v, vy, color="#d62728", alpha=0.95, label="video-anchored y")
    ax_yt.set_xlabel("IMU time (s)"); ax_yt.set_title("cursor y(t)")
    ax_yt.grid(alpha=0.3); ax_yt.legend(fontsize=8)

    err_h = np.sqrt((hx - gx_h) ** 2 + (hy - gy_h) ** 2)
    err_a = np.sqrt((ax - gx_a) ** 2 + (ay - gy_a) ** 2)
    err_v = np.sqrt((vx - gx_v) ** 2 + (vy - gy_v) ** 2)
    ax_err.hist(
        [err_h[np.isfinite(err_h)], err_a[np.isfinite(err_a)], err_v[np.isfinite(err_v)]],
        bins=30, color=["#1f77b4", "#ff7f0e", "#d62728"],
        label=["heuristic", "aeolus", "video-anchored"],
    )
    ax_err.set_xlabel("cursor-to-GT distance (mouth units)")
    ax_err.set_title("Per-window error histogram")
    ax_err.grid(alpha=0.3); ax_err.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(output_dir / "cursor_comparison.png")
    plt.close(fig)
    print(f"Plot: {output_dir / 'cursor_comparison.png'}")

    report = {
        "inputs": {"imu_log": str(args.imu.resolve()), "sync_csv": str(args.sync_csv.resolve())},
        "rmse_cursor_vs_video_gt_mouth_units": {
            "heuristic": {"rmse": rmse_h, "n_windows": n_h},
            "aeolus": {"rmse": rmse_a, "n_windows": n_a},
            "video_anchored": {"rmse": rmse_v, "n_windows": n_v},
        },
        "improvement_video_vs_heuristic_pct": 100.0 * (1.0 - rmse_v / rmse_h) if rmse_h > 0 else None,
        "improvement_video_vs_aeolus_pct": 100.0 * (1.0 - rmse_v / rmse_a) if rmse_a > 0 else None,
    }
    (output_dir / "cursor_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"JSON: {output_dir / 'cursor_comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
