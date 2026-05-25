"""Recalibrate the heuristic dead reckoning parameters using labeled motion logs.

Three labeled motion-type logs (up-down, left-right, inside-outside) are read,
and the four hardcoded parameters of `estimate_dead_reckoning` are retuned via
grid search so that:
  - "left-right" brushing produces dominant X-axis displacement.
  - "up-down" brushing produces dominant Y-axis displacement.
  - "inside-outside" brushing produces moderate, bounded displacement.

Signal analysis shows that the ring's acceleration components discriminate
motion direction far better than yaw/pitch deltas (e.g. the up-down log has
MORE yaw change than the left-right log, because the hand pivots while
making vertical strokes). The retuned parameters therefore promote the
acceleration path and shrink the yaw/pitch contributions.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import (
    SensorSample,
    circular_delta,
    iter_windows,
    parse_sensor_log,
)

LOGS = {
    "up-down": Path("C:/MSc-Computer-Science/Semester-2/pdss/recordings/2026-04-12_2127_up-and-down-and-up-and-down-and.txt"),
    "left-right": Path("C:/MSc-Computer-Science/Semester-2/pdss/recordings/2026-04-20_0958_left-and-right-and-left-and-right-and.txt"),
    "inside-outside": Path("C:/MSc-Computer-Science/Semester-2/pdss/recordings/2026-04-20_1000_inside-and-outside-and-inside-and-outside-and.txt"),
}

WINDOW_SIZE = 80
WINDOW_STEP = 20

CURRENT_PARAMS = dict(damping=0.92, accel_scale=2.00, yaw_contrib=0.0015, pitch_contrib=0.0000)


def dead_reckoning(
    samples: list[SensorSample],
    *,
    damping: float,
    accel_scale: float,
    yaw_contrib: float,
    pitch_contrib: float,
) -> tuple[float, float]:
    """Parameterised version of estimate_dead_reckoning."""
    if len(samples) < 2:
        return 0.0, 0.0

    mean_ax = float(np.mean([s.ax for s in samples]))
    mean_ay = float(np.mean([s.ay for s in samples]))

    velocity_x = 0.0
    velocity_y = 0.0
    pos_x = 0.0
    pos_y = 0.0
    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)
        dyn_ax = curr.ax - mean_ax
        dyn_ay = curr.ay - mean_ay
        velocity_x = (velocity_x * damping) + (dyn_ax * dt * accel_scale)
        velocity_y = (velocity_y * damping) - (dyn_ay * dt * accel_scale)
        pos_x += velocity_x * dt
        pos_y += velocity_y * dt
        pos_x += circular_delta(curr.yaw, prev.yaw) * yaw_contrib
        pos_y -= (curr.pitch - prev.pitch) * pitch_contrib
    return float(pos_x), float(pos_y)


def collect_windows(path: Path) -> list[list[SensorSample]]:
    parsed = parse_sensor_log(path)
    return list(iter_windows(parsed.samples, WINDOW_SIZE, WINDOW_STEP))


def run(windows: list[list[SensorSample]], params: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for w in windows:
        dx, dy = dead_reckoning(w, **params)
        xs.append(dx)
        ys.append(dy)
    return np.array(xs), np.array(ys)


def p90(arr: np.ndarray) -> float:
    return float(np.percentile(np.abs(arr), 90)) if len(arr) else 0.0


def pmax(arr: np.ndarray) -> float:
    return float(np.max(np.abs(arr))) if len(arr) else 0.0


def evaluate(
    params: dict[str, float],
    windows_by_label: dict[str, list[list[SensorSample]]],
) -> tuple[float, dict[str, dict[str, float]]]:
    """Return (loss, metrics).

    Loss encodes four goals (with stronger emphasis on the two pure-axis logs):
      * up-down       -> dr_y P90 near 0.35, dr_x P90 small, dr_y/dr_x >= 3.5
      * left-right    -> dr_x P90 near 0.35, dr_y P90 small, dr_x/dr_y >= 3.5
      * inside-outside-> both bounded (around 0.15), no wild excursions
      * no file's max should exceed 0.55 (clamp is 0.47/0.42 after 0.38 scale)
    """
    metrics: dict[str, dict[str, float]] = {}
    for label, windows in windows_by_label.items():
        xs, ys = run(windows, params)
        metrics[label] = dict(
            x_p90=p90(xs), y_p90=p90(ys), x_max=pmax(xs), y_max=pmax(ys)
        )

    target = 0.35
    cross_target = 0.10
    moderate = 0.15
    dominance_target = 3.5

    ud = metrics["up-down"]
    lr = metrics["left-right"]
    io = metrics["inside-outside"]

    loss = 0.0
    # Up-down and left-right carry double the weight now (8.0 / 4.0 instead of
    # 4.0 / 2.0). Inside-outside is the moderate-motion check and stays at 1.0.
    loss += (ud["y_p90"] - target) ** 2 * 8.0
    loss += (ud["x_p90"] - cross_target) ** 2 * 4.0
    loss += (lr["x_p90"] - target) ** 2 * 8.0
    loss += (lr["y_p90"] - cross_target) ** 2 * 4.0
    loss += (io["x_p90"] - moderate) ** 2 * 1.0
    loss += (io["y_p90"] - moderate) ** 2 * 1.0

    # Dominance ratio: penalize when the principal axis isn't at least 3.5x
    # the cross axis. Hinge loss, so well-separated parameter sets pay nothing.
    ud_ratio = ud["y_p90"] / max(ud["x_p90"], 1e-6)
    lr_ratio = lr["x_p90"] / max(lr["y_p90"], 1e-6)
    loss += max(0.0, dominance_target - ud_ratio) ** 2 * 0.5
    loss += max(0.0, dominance_target - lr_ratio) ** 2 * 0.5

    for m in metrics.values():
        for key in ("x_max", "y_max"):
            overflow = max(0.0, m[key] - 0.55)
            loss += overflow * overflow * 10.0

    return loss, metrics


def grid_search(windows_by_label: dict[str, list[list[SensorSample]]]) -> tuple[dict[str, float], dict]:
    best = (math.inf, None, None)

    damping_grid = [0.86, 0.88, 0.90, 0.91, 0.92, 0.93, 0.94, 0.95]
    accel_grid = [1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.5, 4.0]
    yaw_grid = [0.0005, 0.0008, 0.0010, 0.0012, 0.0015, 0.0018, 0.0022, 0.0028, 0.0035]
    pitch_grid = [0.0, 0.0003, 0.0006, 0.0010, 0.0015, 0.0020, 0.0030]

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
                    loss, metrics = evaluate(params, windows_by_label)
                    if loss < best[0]:
                        best = (loss, params, metrics)
    return best[1], best[2]


def print_metrics(title: str, metrics: dict[str, dict[str, float]]) -> None:
    print(f"\n{title}")
    print(f"  {'file':17s} {'dr_x P90':>9s} {'dr_y P90':>9s} {'dr_x max':>9s} {'dr_y max':>9s}")
    for label, m in metrics.items():
        print(
            f"  {label:17s} "
            f"{m['x_p90']:9.3f} {m['y_p90']:9.3f} "
            f"{m['x_max']:9.3f} {m['y_max']:9.3f}"
        )


def main() -> None:
    windows_by_label = {label: collect_windows(path) for label, path in LOGS.items()}

    _, current_metrics = evaluate(CURRENT_PARAMS, windows_by_label)
    print("Current hardcoded parameters:")
    for k, v in CURRENT_PARAMS.items():
        print(f"  {k:15s} = {v}")
    print_metrics("Current behaviour (P90 and max of |pos_x|, |pos_y|):", current_metrics)

    print("\nSearching parameter grid ...", flush=True)
    new_params, new_metrics = grid_search(windows_by_label)

    print("\n" + "=" * 78)
    print("Recalibrated parameters (grid-search minimum of weighted loss):")
    print("-" * 78)
    for key in CURRENT_PARAMS:
        old = CURRENT_PARAMS[key]
        new = new_params[key]
        arrow = "(unchanged)" if abs(new - old) < 1e-9 else ""
        print(f"  {key:15s}: {old:>10.6f}  ->  {new:>10.6f}   {arrow}")
    print("=" * 78)
    print_metrics("New behaviour (P90 and max of |pos_x|, |pos_y|):", new_metrics)

    print("\nDesign targets:")
    print("  * up-down       -> dr_y P90 near 0.35, dr_x P90 near 0.10, dr_y/dr_x >= 3.5")
    print("  * left-right    -> dr_x P90 near 0.35, dr_y P90 near 0.10, dr_x/dr_y >= 3.5")
    print("  * inside-outside-> both P90 near 0.15")
    print("  * no max above 0.55 (downstream clamps are +/-0.47 on X, +/-0.42 on Y)")

    ud = new_metrics["up-down"]
    lr = new_metrics["left-right"]
    ud_ratio = ud["y_p90"] / max(ud["x_p90"], 1e-6)
    lr_ratio = lr["x_p90"] / max(lr["y_p90"], 1e-6)
    print("\nDominance ratios on retuned params:")
    print(f"  up-down    dr_y / dr_x = {ud_ratio:5.2f}")
    print(f"  left-right dr_x / dr_y = {lr_ratio:5.2f}")


if __name__ == "__main__":
    main()
