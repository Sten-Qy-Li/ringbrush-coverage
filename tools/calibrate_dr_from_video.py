"""Re-tune heuristic dead-reckoning constants using video-derived ground truth.

The existing `tools/calibrate_dead_reckoning.py` tunes the four heuristic
constants {damping, accel_scale, yaw_contrib, pitch_contrib} so that
labeled motion-direction logs produce the right *axis* dominance. The
video ground truth (from the format-3 CSV synchronized on IMU time)
gives something stronger: a per-window 2D wrist displacement we can
regress the DR output against directly.

Pipeline:
  1. Iterate the same windowed sweep the production analyzer uses
     (window_size=80 samples, window_step=20 samples).
  2. For each window, compute:
       * heuristic DR (dx_imu, dy_imu) under the candidate params;
       * video Δwrist over the same time span, where coverage exists.
  3. For each candidate, fit the best 2D rotation + scale that maps
     (dx_imu, dy_imu) → (Δwrist_x, Δwrist_y) in the least-squares sense
     (closed-form Procrustes). Loss = post-fit per-window RMSE.
  4. Grid-search over the same parameter ranges used by the existing
     calibrator. Emit the best params to JSON.

The JSON output drops straight into the CLI:
    ringbrush-coverage <log> --heuristic-params <emitted.json>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import (
    HEURISTIC_DR_DEFAULTS,
    SensorSample,
    estimate_dead_reckoning,
    iter_windows,
    parse_sensor_log,
)


WINDOW_SIZE = 80
WINDOW_STEP = 20


def load_video_xy(sync_csv_path: Path, landmark: str = "wrist") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (t_ms_imu_relative, x, y) arrays with valid rows only."""
    ts = []
    xs = []
    ys = []
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
                ts.append(float(t_raw))
                xs.append(float(x_raw))
                ys.append(float(y_raw))
            except ValueError:
                continue
    return np.array(ts), np.array(xs), np.array(ys)


def _interp_safe(target_ms: float, ts: np.ndarray, vals: np.ndarray) -> float | None:
    if target_ms < ts[0] or target_ms > ts[-1]:
        return None
    return float(np.interp(target_ms, ts, vals))


def collect_window_targets(
    samples: list[SensorSample],
    video_ts_ms: np.ndarray,
    video_xs: np.ndarray,
    video_ys: np.ndarray,
    *,
    window_size: int = WINDOW_SIZE,
    window_step: int = WINDOW_STEP,
) -> tuple[list[list[SensorSample]], np.ndarray]:
    """For each window, compute the video (Δx, Δy) over that span.

    Returns (windows kept, targets array of shape (N, 2)). Windows where
    the video has no coverage at either endpoint are dropped.
    """
    windows = list(iter_windows(samples, window_size, window_step))
    kept_windows: list[list[SensorSample]] = []
    deltas: list[tuple[float, float]] = []
    t0_ms = samples[0].t_ms

    for w in windows:
        start_ms = w[0].t_ms - t0_ms
        end_ms = w[-1].t_ms - t0_ms
        x_start = _interp_safe(start_ms, video_ts_ms, video_xs)
        x_end = _interp_safe(end_ms, video_ts_ms, video_xs)
        y_start = _interp_safe(start_ms, video_ts_ms, video_ys)
        y_end = _interp_safe(end_ms, video_ts_ms, video_ys)
        if None in (x_start, x_end, y_start, y_end):
            continue
        kept_windows.append(w)
        deltas.append((x_end - x_start, y_end - y_start))

    return kept_windows, np.array(deltas, dtype=float)


def procrustes_2d(
    source: np.ndarray,
    target: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    """Closed-form fit of best 2D rotation+uniform scale that maps source -> target.

    Both inputs are (N, 2). Returns (rotation_rad, scale, fitted_source).
    Uses the standard SVD-based Procrustes formulation, but on already-
    zero-mean Δ vectors so we skip the translation step.
    """
    if source.shape[0] == 0:
        return 0.0, 1.0, source
    # Cross-covariance.
    M = source.T @ target  # (2, 2)
    U, S, Vt = np.linalg.svd(M)
    # Ensure no reflection (we want a pure rotation, not a flip).
    det_uv = np.linalg.det(U @ Vt)
    D = np.diag([1.0, det_uv])
    R = U @ D @ Vt
    src_var = float(np.sum(source * source))
    if src_var < 1e-12:
        return 0.0, 1.0, source
    scale = float(np.sum(S * np.diag(D))) / src_var
    fitted = scale * (source @ R)
    theta = math.atan2(R[1, 0], R[0, 0])
    return theta, scale, fitted


def evaluate_params(
    params: dict[str, float],
    windows: list[list[SensorSample]],
    targets: np.ndarray,
) -> dict[str, float]:
    dr_xy = np.array(
        [estimate_dead_reckoning(w, params) for w in windows], dtype=float
    )
    if dr_xy.shape[0] == 0:
        return {"rmse": math.inf, "scale": 0.0, "rotation_deg": 0.0}
    theta, scale, fitted = procrustes_2d(dr_xy, targets)
    residual = targets - fitted
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    return {
        "rmse": rmse,
        "scale": scale,
        "rotation_deg": math.degrees(theta),
        "windows": int(dr_xy.shape[0]),
    }


def grid_search(
    windows: list[list[SensorSample]],
    targets: np.ndarray,
    *,
    coarse: bool = False,
) -> tuple[dict[str, float], dict[str, float]]:
    if coarse:
        damping_grid = [0.86, 0.90, 0.92, 0.94]
        accel_grid = [1.4, 1.8, 2.2, 2.6, 3.0]
        yaw_grid = [0.0005, 0.0015, 0.0025, 0.0035]
        pitch_grid = [0.0, 0.0010, 0.0020]
    else:
        damping_grid = [0.86, 0.88, 0.90, 0.91, 0.92, 0.93, 0.94, 0.95]
        accel_grid = [1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.5, 4.0]
        yaw_grid = [0.0003, 0.0005, 0.0008, 0.0010, 0.0012, 0.0015, 0.0018, 0.0022, 0.0028, 0.0035]
        pitch_grid = [0.0, 0.0003, 0.0006, 0.0010, 0.0015, 0.0020, 0.0030]

    best = (math.inf, None, None)
    total = len(damping_grid) * len(accel_grid) * len(yaw_grid) * len(pitch_grid)
    count = 0
    for damping in damping_grid:
        for accel in accel_grid:
            for yaw in yaw_grid:
                for pitch in pitch_grid:
                    params = dict(
                        damping=damping,
                        accel_scale=accel,
                        yaw_contrib=yaw,
                        pitch_contrib=pitch,
                    )
                    metrics = evaluate_params(params, windows, targets)
                    if metrics["rmse"] < best[0]:
                        best = (metrics["rmse"], params, metrics)
                    count += 1
                    if count % max(1, total // 20) == 0:
                        print(f"  grid {count:6d}/{total:6d}  best RMSE={best[0]:.5f}", flush=True)
    return best[1], best[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-tune heuristic DR constants against video ground truth."
    )
    parser.add_argument("imu", type=Path)
    parser.add_argument("sync_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--landmark", default="wrist")
    parser.add_argument("--coarse", action="store_true", help="Run a much smaller grid for a quick check.")
    args = parser.parse_args(argv)

    print(f"IMU log:  {args.imu}")
    print(f"Sync CSV: {args.sync_csv}")
    parsed = parse_sensor_log(args.imu.resolve())
    ts, xs, ys = load_video_xy(args.sync_csv.resolve(), landmark=args.landmark)
    if ts.size == 0:
        raise SystemExit("Sync CSV has no valid landmark rows.")

    windows, targets = collect_window_targets(parsed.samples, ts, xs, ys)
    print(f"Windows with video coverage: {len(windows)} / {len(list(iter_windows(parsed.samples, WINDOW_SIZE, WINDOW_STEP)))}")

    print("\nBaseline (current defaults):")
    base_metrics = evaluate_params(HEURISTIC_DR_DEFAULTS, windows, targets)
    for k, v in HEURISTIC_DR_DEFAULTS.items():
        print(f"  {k:15s} = {v}")
    print(f"  baseline RMSE = {base_metrics['rmse']:.5f}  (post-fit scale={base_metrics['scale']:.3f}, rot={base_metrics['rotation_deg']:+.1f}°)")

    print("\nGrid searching ...", flush=True)
    new_params, new_metrics = grid_search(windows, targets, coarse=args.coarse)
    print("\n" + "=" * 70)
    print("Re-tuned parameters:")
    for key in HEURISTIC_DR_DEFAULTS:
        old = HEURISTIC_DR_DEFAULTS[key]
        new = new_params[key]
        arrow = "(unchanged)" if abs(new - old) < 1e-9 else ""
        print(f"  {key:15s}: {old:>10.6f}  ->  {new:>10.6f}   {arrow}")
    print(f"  new RMSE      = {new_metrics['rmse']:.5f}  (post-fit scale={new_metrics['scale']:.3f}, rot={new_metrics['rotation_deg']:+.1f}°)")
    improvement = 1.0 - new_metrics["rmse"] / base_metrics["rmse"]
    print(f"  improvement   = {improvement * 100.0:+.1f} % vs baseline")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **new_params,
        "_meta": {
            "imu_log": str(args.imu.resolve()),
            "sync_csv": str(args.sync_csv.resolve()),
            "landmark": args.landmark,
            "baseline_rmse": base_metrics["rmse"],
            "new_rmse": new_metrics["rmse"],
            "improvement_fraction": float(improvement),
            "windows_used": int(targets.shape[0]),
            "post_fit_scale": new_metrics["scale"],
            "post_fit_rotation_deg": new_metrics["rotation_deg"],
        },
    }
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
