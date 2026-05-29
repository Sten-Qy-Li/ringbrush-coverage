"""Quantify dead-reckoning error against video-derived ground truth.

Builds three trajectories on the synchronized IMU timeline:
  * heuristic DR (existing)
  * AEOLUS DR    (existing)
  * format-3 wrist (ground truth from MediaPipe HandLandmarker)

The three signals live on different scales (viz units, metres, normalized
image coords). To make them comparable we shift each to zero mean and
scale to unit RMS, then report RMSE on the shape-normalized frame. We
also report path-length ratios and bbox-diagonal ratios, which are
scale-free.

Outputs (under `--output-dir`):
  * `comparison.json` — per-method error metrics.
  * `comparison.png`  — overlay of all three trajectories (shape-normalized)
                         plus per-axis time series.
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

from ringbrush_coverage.core import (
    TrajectoryPoint,
    parse_sensor_log,
    trajectory_aeolus,
    trajectory_heuristic,
)


def load_video_trajectory(
    sync_csv_path: Path,
    *,
    landmark: str = "wrist",
) -> list[TrajectoryPoint]:
    """Load (t_s on IMU frame, x, y) from the synchronized format-3 CSV."""
    points: list[TrajectoryPoint] = []
    with sync_csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        x_key = f"{landmark}_x"
        y_key = f"{landmark}_y"
        for row in reader:
            x_raw = row.get(x_key, "")
            y_raw = row.get(y_key, "")
            t_raw = row.get("t_ms_imu_relative", "")
            if not x_raw or not y_raw or not t_raw:
                continue
            try:
                t_s = float(t_raw) / 1000.0
                x = float(x_raw)
                y = float(y_raw)
            except ValueError:
                continue
            points.append(TrajectoryPoint(t_s=t_s, x=x, y=y))
    return points


def _shape_normalize(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Zero-mean and scale by combined RMS so two paths can be compared."""
    if xs.size == 0:
        return xs, ys, 1.0
    xs = xs - xs.mean()
    ys = ys - ys.mean()
    rms = float(np.sqrt(np.mean(xs * xs + ys * ys)))
    if rms < 1e-9:
        rms = 1.0
    return xs / rms, ys / rms, rms


def _resample(times: np.ndarray, xs: np.ndarray, ys: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linear interpolate (xs, ys) onto a uniform time grid, clipping outside coverage."""
    mask = (grid >= times[0]) & (grid <= times[-1])
    out_x = np.full_like(grid, np.nan, dtype=float)
    out_y = np.full_like(grid, np.nan, dtype=float)
    out_x[mask] = np.interp(grid[mask], times, xs)
    out_y[mask] = np.interp(grid[mask], times, ys)
    return out_x, out_y


def _path_length(xs: np.ndarray, ys: np.ndarray) -> float:
    if xs.size < 2:
        return 0.0
    return float(np.sum(np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2)))


def _bbox_diag(xs: np.ndarray, ys: np.ndarray) -> float:
    if xs.size == 0:
        return 0.0
    return float(math.hypot(xs.max() - xs.min(), ys.max() - ys.min()))


def _metric_block(label: str, traj: list[TrajectoryPoint]) -> dict:
    xs = np.array([p.x for p in traj], dtype=float)
    ys = np.array([p.y for p in traj], dtype=float)
    return {
        "label": label,
        "points": int(xs.size),
        "path_length": _path_length(xs, ys),
        "bbox_diag": _bbox_diag(xs, ys),
        "duration_s": float(traj[-1].t_s - traj[0].t_s) if len(traj) >= 2 else 0.0,
    }


def _rmse_against_gt(
    method_xs: np.ndarray,
    method_ys: np.ndarray,
    gt_xs: np.ndarray,
    gt_ys: np.ndarray,
) -> dict:
    """Both inputs are already shape-normalized (zero-mean, RMS=1).

    Find the rotation+sign that minimizes Procrustes-style RMSE against
    the ground truth. The IMU body frame is not guaranteed to align with
    the camera image frame (the user may have held the camera tilted),
    so we let the optimizer find the best rigid 2D rotation. Reflections
    are also allowed because the front-facing camera may mirror x.
    """
    mask = np.isfinite(method_xs) & np.isfinite(method_ys) & np.isfinite(gt_xs) & np.isfinite(gt_ys)
    if mask.sum() < 8:
        return {"rmse": float("nan"), "rotation_deg": float("nan"), "reflected": False, "samples": int(mask.sum())}
    mx = method_xs[mask]; my = method_ys[mask]
    gx = gt_xs[mask]; gy = gt_ys[mask]
    best = {"rmse": float("inf"), "rotation_deg": 0.0, "reflected": False, "samples": int(mask.sum())}
    for reflect in (False, True):
        gx_r = -gx if reflect else gx
        for theta_deg in np.arange(0.0, 360.0, 2.0):
            theta = math.radians(theta_deg)
            cos_t = math.cos(theta); sin_t = math.sin(theta)
            mx_rot = cos_t * mx - sin_t * my
            my_rot = sin_t * mx + cos_t * my
            rmse = float(np.sqrt(np.mean((mx_rot - gx_r) ** 2 + (my_rot - gy) ** 2)))
            if rmse < best["rmse"]:
                best = {
                    "rmse": rmse,
                    "rotation_deg": theta_deg,
                    "reflected": reflect,
                    "samples": int(mask.sum()),
                }
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare existing DR methods against the video-derived ground truth."
    )
    parser.add_argument("imu", type=Path, help="IMU .txt sensor log")
    parser.add_argument("sync_csv", type=Path, help="synchronized_video_on_imu_time.csv from sync_video_imu.py")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--landmark", default="wrist")
    parser.add_argument("--resample-hz", type=float, default=20.0,
                        help="Common-grid sample rate for shape comparison. Default: 20 Hz")
    args = parser.parse_args(argv)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = parse_sensor_log(args.imu.resolve())
    t_first_imu_ms = float(parsed.samples[0].t_ms)
    # The trajectory_* functions emit t_s = (curr.t_ms - first.t_ms) / 1000,
    # i.e. IMU-relative time. That's also what the sync CSV's
    # t_ms_imu_relative column uses, so the two streams share a frame.
    traj_h = trajectory_heuristic(parsed.samples)
    traj_a = trajectory_aeolus(parsed.samples)
    traj_v = load_video_trajectory(args.sync_csv.resolve(), landmark=args.landmark)

    if not traj_v:
        raise ValueError("Synchronized video CSV produced no usable points.")

    print(f"IMU first t_ms: {t_first_imu_ms:.1f}")
    print(f"heuristic DR : {_metric_block('heuristic', traj_h)}")
    print(f"AEOLUS DR    : {_metric_block('aeolus', traj_a)}")
    print(f"video GT     : {_metric_block('video', traj_v)}")

    grid_start = max(traj_h[0].t_s, traj_a[0].t_s, traj_v[0].t_s)
    grid_end = min(traj_h[-1].t_s, traj_a[-1].t_s, traj_v[-1].t_s)
    grid = np.arange(grid_start, grid_end, 1.0 / args.resample_hz)

    h_x, h_y = _resample(
        np.array([p.t_s for p in traj_h]),
        np.array([p.x for p in traj_h]),
        np.array([p.y for p in traj_h]),
        grid,
    )
    a_x, a_y = _resample(
        np.array([p.t_s for p in traj_a]),
        np.array([p.x for p in traj_a]),
        np.array([p.y for p in traj_a]),
        grid,
    )
    v_x, v_y = _resample(
        np.array([p.t_s for p in traj_v]),
        np.array([p.x for p in traj_v]),
        np.array([p.y for p in traj_v]),
        grid,
    )

    # Shape-normalize each before computing rotation-invariant RMSE.
    hx_n, hy_n, h_rms = _shape_normalize(np.nan_to_num(h_x), np.nan_to_num(h_y))
    ax_n, ay_n, a_rms = _shape_normalize(np.nan_to_num(a_x), np.nan_to_num(a_y))
    vx_n, vy_n, v_rms = _shape_normalize(np.nan_to_num(v_x), np.nan_to_num(v_y))

    rmse_h = _rmse_against_gt(hx_n, hy_n, vx_n, vy_n)
    rmse_a = _rmse_against_gt(ax_n, ay_n, vx_n, vy_n)
    print(f"heuristic vs video: RMSE={rmse_h['rmse']:.3f} (rot={rmse_h['rotation_deg']:.0f}° refl={rmse_h['reflected']})")
    print(f"aeolus    vs video: RMSE={rmse_a['rmse']:.3f} (rot={rmse_a['rotation_deg']:.0f}° refl={rmse_a['reflected']})")

    # Render overlay PNG. Apply the best rotation/reflection so the user
    # can see shapes side-by-side.
    def _apply(xs, ys, fit):
        if fit["reflected"]:
            xs_p = -xs
        else:
            xs_p = xs
        theta = math.radians(fit["rotation_deg"])
        cos_t = math.cos(theta); sin_t = math.sin(theta)
        # invert what we did to (mx, my) so the method matches GT;
        # rotation we applied was R(theta) on the method, so to display the
        # method aligned to the GT frame we apply R(theta) to (xs, ys).
        # But wait: in RMSE search we rotated *method* toward GT, so for
        # plotting we want the rotated method.
        return cos_t * xs - sin_t * ys, sin_t * xs + cos_t * ys, xs_p

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=130)
    ax_xy, ax_corr, ax_xt, ax_yt = axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]

    # XY shape overlay
    theta_h = math.radians(rmse_h["rotation_deg"])
    ch, sh = math.cos(theta_h), math.sin(theta_h)
    hxr = ch * hx_n - sh * hy_n
    hyr = sh * hx_n + ch * hy_n
    theta_a = math.radians(rmse_a["rotation_deg"])
    ca, sa = math.cos(theta_a), math.sin(theta_a)
    axr = ca * ax_n - sa * ay_n
    ayr = sa * ax_n + ca * ay_n
    vxg = -vx_n if rmse_h["reflected"] else vx_n
    ax_xy.plot(vxg, vy_n, color="#2ca02c", label="video GT (shape-norm)", alpha=0.7, linewidth=1.5)
    ax_xy.plot(hxr, hyr, color="#1f77b4", label=f"heuristic (RMSE={rmse_h['rmse']:.3f})", alpha=0.7, linewidth=1.0)
    ax_xy.plot(axr, ayr, color="#ff7f0e", label=f"aeolus    (RMSE={rmse_a['rmse']:.3f})", alpha=0.7, linewidth=1.0)
    ax_xy.set_title("Shape-normalized XY trajectories (best 2D rotation each)")
    ax_xy.set_aspect("equal", adjustable="datalim")
    ax_xy.grid(alpha=0.3)
    ax_xy.legend(loc="best", fontsize=9)

    # Time series x(t) and y(t)
    ax_xt.plot(grid, vxg, color="#2ca02c", label="video x", alpha=0.7)
    ax_xt.plot(grid, hxr, color="#1f77b4", label="heuristic x (aligned)", alpha=0.7)
    ax_xt.plot(grid, axr, color="#ff7f0e", label="aeolus x (aligned)", alpha=0.7)
    ax_xt.set_title("x(t) shape-normalized")
    ax_xt.set_xlabel("IMU time (s)")
    ax_xt.legend(fontsize=8, loc="upper right")
    ax_xt.grid(alpha=0.3)

    ax_yt.plot(grid, vy_n, color="#2ca02c", label="video y", alpha=0.7)
    ax_yt.plot(grid, hyr, color="#1f77b4", label="heuristic y (aligned)", alpha=0.7)
    ax_yt.plot(grid, ayr, color="#ff7f0e", label="aeolus y (aligned)", alpha=0.7)
    ax_yt.set_title("y(t) shape-normalized")
    ax_yt.set_xlabel("IMU time (s)")
    ax_yt.legend(fontsize=8, loc="upper right")
    ax_yt.grid(alpha=0.3)

    # Histogram of pointwise distance
    err_h = np.sqrt((hxr - vxg) ** 2 + (hyr - vy_n) ** 2)
    err_a = np.sqrt((axr - vxg) ** 2 + (ayr - vy_n) ** 2)
    ax_corr.hist([err_h, err_a], bins=30, label=["heuristic", "aeolus"], color=["#1f77b4", "#ff7f0e"])
    ax_corr.set_title("Pointwise distance to GT (shape-norm units)")
    ax_corr.set_xlabel("distance")
    ax_corr.legend(fontsize=9)
    ax_corr.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_dir / "comparison.png")
    plt.close(fig)
    print(f"Overlay: {output_dir / 'comparison.png'}")

    report = {
        "inputs": {
            "imu_log": str(args.imu.resolve()),
            "sync_csv": str(args.sync_csv.resolve()),
            "landmark": args.landmark,
            "resample_hz": args.resample_hz,
        },
        "trajectories": {
            "heuristic": _metric_block("heuristic", traj_h),
            "aeolus": _metric_block("aeolus", traj_a),
            "video": _metric_block("video", traj_v),
        },
        "common_grid": {
            "t_start_s": float(grid_start),
            "t_end_s": float(grid_end),
            "samples": int(grid.size),
        },
        "shape_normalized_rmse_vs_video": {
            "heuristic": rmse_h,
            "aeolus": rmse_a,
        },
        "scale_factors": {
            "heuristic_rms": h_rms,
            "aeolus_rms": a_rms,
            "video_rms": v_rms,
        },
    }
    (output_dir / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report:  {output_dir / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
