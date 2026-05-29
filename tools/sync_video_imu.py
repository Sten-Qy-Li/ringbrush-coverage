"""Synchronize a video-derived format-3 CSV with an IMU log.

The IMU log and the format-3 CSV have independent time origins. To recover
the offset, both recordings start (and end) with a deliberate hand shake.
This tool builds a 1-D "motion energy" signal from each stream, cross-
correlates them, and reports the best-fit constant offset
`imu_minus_video_ms` such that

    video_t_ms + imu_minus_video_ms ≈ IMU t_ms

Two motion-energy signals:

* IMU: a 30 Hz angular speed envelope from circular deltas of
  (roll, pitch, yaw) — already the discriminator used by the existing
  feature extractor.
* Video: the per-frame Euclidean speed of the wrist landmark (with gaps
  bridged by linear interpolation through missing frames).

Both signals are resampled onto a 100 Hz common grid, z-scored, and
cross-correlated via FFT (`scipy.signal.correlate`).

Outputs (under `--output-dir`):
* `sync_report.json` — offset, peak correlation, signal stats.
* `sync_overlay.png` — full-session overlay plus zooms on the first
  ~12 s (start shake) and the last ~12 s (end shake) for visual QA.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import correlate, correlation_lags

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import circular_delta, parse_sensor_log


@dataclass
class SignalStats:
    samples: int
    duration_s: float
    mean: float
    std: float
    min: float
    max: float


def _stats(values: np.ndarray) -> SignalStats:
    if values.size == 0:
        return SignalStats(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return SignalStats(
        samples=int(values.size),
        duration_s=float(values.size - 1) / 100.0,  # informational; caller knows the rate
        mean=float(values.mean()),
        std=float(values.std()),
        min=float(values.min()),
        max=float(values.max()),
    )


def imu_motion_energy(log_path: Path, resample_hz: float = 100.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (t_s relative to first sample, motion_energy) on a uniform grid."""
    parsed = parse_sensor_log(log_path)
    if len(parsed.samples) < 4:
        raise ValueError(f"IMU log {log_path} has too few samples.")
    t0_ms = parsed.samples[0].t_ms
    raw_t = np.array([s.t_ms - t0_ms for s in parsed.samples], dtype=float) / 1000.0
    rolls = np.array([s.roll for s in parsed.samples], dtype=float)
    pitches = np.array([s.pitch for s in parsed.samples], dtype=float)
    yaws = np.array([s.yaw for s in parsed.samples], dtype=float)

    # Angular speed = |Δroll|+|Δpitch|+|Δyaw| per second, attributed to the
    # later of the two samples. Same magnitude that already drives the
    # heuristic activity score in core.feature_vector.
    dt = np.diff(raw_t)
    dt = np.where(dt <= 0, 1e-3, dt)
    d_roll = np.array([abs(circular_delta(rolls[i + 1], rolls[i])) for i in range(rolls.size - 1)])
    d_pitch = np.abs(np.diff(pitches))
    d_yaw = np.array([abs(circular_delta(yaws[i + 1], yaws[i])) for i in range(yaws.size - 1)])
    angular = (d_roll + d_pitch + d_yaw) / dt
    angular_t = raw_t[1:]

    # Resample onto a uniform 100 Hz grid via linear interpolation.
    grid_t = np.arange(angular_t[0], angular_t[-1], 1.0 / resample_hz)
    grid_energy = np.interp(grid_t, angular_t, angular)
    return grid_t, _shape_signal(grid_energy, resample_hz)


def video_motion_energy(
    csv_path: Path,
    *,
    resample_hz: float = 100.0,
    landmark: str = "wrist",
) -> tuple[np.ndarray, np.ndarray]:
    xs = []
    ys = []
    ts = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        x_key = f"{landmark}_x"
        y_key = f"{landmark}_y"
        for row in reader:
            try:
                t_ms = float(row["t_ms"])
            except (TypeError, ValueError):
                continue
            x = row.get(x_key, "")
            y = row.get(y_key, "")
            xs.append(float(x) if x else math.nan)
            ys.append(float(y) if y else math.nan)
            ts.append(t_ms / 1000.0)

    xs_arr = np.array(xs, dtype=float)
    ys_arr = np.array(ys, dtype=float)
    ts_arr = np.array(ts, dtype=float)

    if xs_arr.size < 4:
        raise ValueError(f"Video CSV {csv_path} has too few frames.")

    # Linear-interpolate through missing detections.
    valid = ~np.isnan(xs_arr) & ~np.isnan(ys_arr)
    if not valid.any():
        raise ValueError(f"No detected hand frames in {csv_path}.")
    xs_arr = np.interp(ts_arr, ts_arr[valid], xs_arr[valid])
    ys_arr = np.interp(ts_arr, ts_arr[valid], ys_arr[valid])

    dt = np.diff(ts_arr)
    dt = np.where(dt <= 0, 1.0 / 30.0, dt)
    speed = np.sqrt(np.diff(xs_arr) ** 2 + np.diff(ys_arr) ** 2) / dt
    speed_t = ts_arr[1:]

    grid_t = np.arange(speed_t[0], speed_t[-1], 1.0 / resample_hz)
    grid_energy = np.interp(grid_t, speed_t, speed)
    return grid_t, _shape_signal(grid_energy, resample_hz)


def _moving_average(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or x.size <= 1:
        return x
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def _shape_signal(energy: np.ndarray, resample_hz: float) -> np.ndarray:
    """Robustify a motion-energy signal for cross-correlation.

    Two cleanups, both essential to get a stable peak on this dataset:

    1. Clip to the 99th percentile. A single missing-IMU-sample produced
       a ~14 000 deg/s spike in the new full-session log, two orders of
       magnitude above the brushing envelope. Z-scoring after clipping
       restores the brushing/shake amplitude balance.
    2. log1p compression after clipping. Brushing-motion and shake-motion
       differ by ~5x in amplitude; the log compresses that gap so both
       drive the correlation, rather than only the loudest segment.
    3. 0.5 s moving average. The calibration shake periodicity is ~1 Hz
       and brushing is ~3-5 Hz; 0.5 s preserves both as broad envelopes
       and kills MediaPipe / IMU jitter.
    """
    if energy.size == 0:
        return energy
    clip_value = float(np.percentile(energy, 99.0))
    if clip_value > 0:
        energy = np.minimum(energy, clip_value)
    energy = np.log1p(energy)
    energy = _moving_average(energy, window=int(resample_hz * 0.5))
    return energy


def _zscore(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    mu = x.mean()
    sd = x.std()
    if sd < 1e-9:
        return x - mu
    return (x - mu) / sd


def cross_correlate(
    imu_t: np.ndarray,
    imu_e: np.ndarray,
    vid_t: np.ndarray,
    vid_e: np.ndarray,
    *,
    resample_hz: float = 100.0,
) -> dict:
    """Cross-correlate the two z-scored signals; return offset in ms."""
    a = _zscore(imu_e)
    b = _zscore(vid_e)
    # `correlate` returns a series of size 2N-1 (after we pad to a common
    # length); use `correlation_lags` to map indices back to integer sample
    # shifts.
    corr = correlate(a, b, mode="full", method="fft")
    lags = correlation_lags(a.size, b.size, mode="full")
    norm = math.sqrt(float(np.sum(a * a)) * float(np.sum(b * b)))
    if norm > 1e-9:
        corr = corr / norm

    best_idx = int(np.argmax(corr))
    best_lag_samples = int(lags[best_idx])
    # When IMU = imu_t[0] + k/Hz and video = vid_t[0] + j/Hz, peak at lag
    # L means imu_t[L+j/Hz] aligns with vid_t[j/Hz], so the IMU-relative
    # time of a given video-relative time is shifted by L/Hz seconds.
    # IMU absolute t_ms = imu_t0_ms + relative imu_t * 1000; same for
    # video. The constant offset we return is in absolute-time terms.
    best_offset_video_to_imu_s = best_lag_samples / resample_hz
    peak_corr = float(corr[best_idx])

    return {
        "best_lag_samples": best_lag_samples,
        "best_lag_seconds": best_offset_video_to_imu_s,
        "peak_corr": peak_corr,
        "corr_curve": corr.tolist(),
        "lags_samples": lags.tolist(),
    }


def _render_overlay(
    output_path: Path,
    imu_t: np.ndarray,
    imu_e: np.ndarray,
    vid_t: np.ndarray,
    vid_e: np.ndarray,
    shift_seconds: float,
    peak_corr: float,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), dpi=130)

    imu_norm = _zscore(imu_e)
    vid_norm = _zscore(vid_e)
    vid_shifted_t = vid_t + shift_seconds

    axes[0].plot(imu_t, imu_norm, color="#1f77b4", label="IMU |angular speed| (z)", alpha=0.85)
    axes[0].plot(vid_shifted_t, vid_norm, color="#ff7f0e", label="Video wrist speed (z, shifted)", alpha=0.85)
    axes[0].set_title(f"Full session  (shift = {shift_seconds:+.3f} s, peak r = {peak_corr:.3f})")
    axes[0].set_xlabel("IMU time (s, from first sample)")
    axes[0].legend(loc="upper right")
    axes[0].grid(alpha=0.3)

    common_lo = max(imu_t[0], vid_shifted_t[0])
    common_hi = min(imu_t[-1], vid_shifted_t[-1])
    head_hi = common_lo + 14.0
    tail_lo = common_hi - 14.0

    axes[1].plot(imu_t, imu_norm, color="#1f77b4", alpha=0.85)
    axes[1].plot(vid_shifted_t, vid_norm, color="#ff7f0e", alpha=0.85)
    axes[1].set_xlim(common_lo, head_hi)
    axes[1].set_title("Start calibration zoom (first 14 s of overlap)")
    axes[1].set_xlabel("IMU time (s)")
    axes[1].grid(alpha=0.3)

    axes[2].plot(imu_t, imu_norm, color="#1f77b4", alpha=0.85)
    axes[2].plot(vid_shifted_t, vid_norm, color="#ff7f0e", alpha=0.85)
    axes[2].set_xlim(tail_lo, common_hi)
    axes[2].set_title("End calibration zoom (last 14 s of overlap)")
    axes[2].set_xlabel("IMU time (s)")
    axes[2].grid(alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)


def _emit_synchronized_csv(
    video_csv: Path,
    imu_log: Path,
    shift_seconds: float,
    output_csv: Path,
) -> None:
    """Re-emit the format-3 CSV with an extra column for IMU-frame t_ms.

    The format-3 CSV has its own time origin (video t=0 at first frame).
    The IMU log has its own (t_ms of first parsed sample, e.g. 136966).
    For downstream code we want one coherent timeline. We choose the
    *IMU absolute t_ms* axis (so any IMU-side calculation can index in
    directly) and add `t_ms_imu` = video_t_ms + shift_ms + imu_first_t_ms.
    """
    parsed = parse_sensor_log(imu_log)
    imu_t0_ms = float(parsed.samples[0].t_ms)
    shift_ms = shift_seconds * 1000.0

    with video_csv.open("r", encoding="utf-8") as fh_in, output_csv.open("w", newline="", encoding="utf-8") as fh_out:
        reader = csv.reader(fh_in)
        writer = csv.writer(fh_out)
        header = next(reader)
        writer.writerow(header + ["t_ms_imu_relative", "t_ms_imu_absolute"])
        for row in reader:
            if not row:
                continue
            try:
                t_ms_video = float(row[1])
            except (ValueError, IndexError):
                writer.writerow(row + ["", ""])
                continue
            t_ms_imu_rel = t_ms_video + shift_ms
            t_ms_imu_abs = t_ms_imu_rel + imu_t0_ms
            writer.writerow(row + [f"{t_ms_imu_rel:.3f}", f"{t_ms_imu_abs:.3f}"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-correlate an IMU log against a format-3 video CSV to recover their time offset."
    )
    parser.add_argument("imu", type=Path, help="IMU .txt sensor log")
    parser.add_argument("video_csv", type=Path, help="format-3 .csv from extract_video_motion.py")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write sync_report.json + sync_overlay.png")
    parser.add_argument("--landmark", default="wrist", choices=("wrist", "index_mcp", "middle_mcp", "ring_mcp"))
    parser.add_argument("--resample-hz", type=float, default=100.0)
    args = parser.parse_args(argv)

    imu_path = args.imu.resolve()
    csv_path = args.video_csv.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"IMU log:   {imu_path}")
    print(f"Video CSV: {csv_path}")
    imu_t, imu_e = imu_motion_energy(imu_path, resample_hz=args.resample_hz)
    vid_t, vid_e = video_motion_energy(csv_path, resample_hz=args.resample_hz, landmark=args.landmark)
    print(f"IMU signal:   {imu_t.size} samples, {imu_t[-1] - imu_t[0]:.2f} s")
    print(f"Video signal: {vid_t.size} samples, {vid_t[-1] - vid_t[0]:.2f} s")

    result = cross_correlate(imu_t, imu_e, vid_t, vid_e, resample_hz=args.resample_hz)
    shift_seconds = result["best_lag_seconds"]
    peak_corr = result["peak_corr"]
    print(f"Best lag: {result['best_lag_samples']} samples -> shift = {shift_seconds:+.3f} s")
    print(f"Peak correlation: r = {peak_corr:.4f}")

    overlay_path = output_dir / "sync_overlay.png"
    _render_overlay(overlay_path, imu_t, imu_e, vid_t, vid_e, shift_seconds, peak_corr)
    print(f"Overlay: {overlay_path}")

    sync_csv_path = output_dir / "synchronized_video_on_imu_time.csv"
    _emit_synchronized_csv(csv_path, imu_path, shift_seconds, sync_csv_path)
    print(f"Sync CSV: {sync_csv_path}")

    report = {
        "inputs": {
            "imu_log": str(imu_path),
            "video_csv": str(csv_path),
            "landmark": args.landmark,
            "resample_hz": args.resample_hz,
        },
        "imu_signal": asdict(_stats(imu_e)),
        "video_signal": asdict(_stats(vid_e)),
        "imu_duration_s": float(imu_t[-1] - imu_t[0]),
        "video_duration_s": float(vid_t[-1] - vid_t[0]),
        # Apply: video_t_ms + offset_ms  -> IMU relative t in ms
        "video_to_imu_offset_seconds": shift_seconds,
        "video_to_imu_offset_ms": shift_seconds * 1000.0,
        "peak_correlation": peak_corr,
        "overlay_png": str(overlay_path),
    }
    report_path = output_dir / "sync_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report:  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
