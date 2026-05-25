"""Compare the heuristic dead-reckoning method against the Radeta-2023 AEOLUS method.

Runs both methods on the same sensor log, then produces three artefacts in the
chosen output directory:

  * <stem>_dr-compare.json  - per-method trajectory stats and ratios.
  * <stem>_dr-compare.png   - side-by-side trajectory plot.
  * <stem>_dr-compare.mp4   - side-by-side trajectory animation.

The two methods live on different scales (heuristic is dimensionless, AEOLUS is
in metres), so the plots use independent axes. Each is auto-fit to its own
data; that way the visible *shape* of the path is directly comparable even
though the units are not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ringbrush_coverage.core import (
    TrajectoryPoint,
    parse_sensor_log,
    trajectory_aeolus,
    trajectory_heuristic,
)


@dataclass
class TrajectoryStats:
    points: int
    duration_s: float
    path_length: float
    end_displacement: float
    x_range: tuple[float, float]
    y_range: tuple[float, float]
    bbox_diag: float


def _trajectory_stats(traj: list[TrajectoryPoint]) -> TrajectoryStats:
    xs = np.array([p.x for p in traj], dtype=float)
    ys = np.array([p.y for p in traj], dtype=float)
    ts = np.array([p.t_s for p in traj], dtype=float)
    if xs.size < 2:
        return TrajectoryStats(
            points=int(xs.size),
            duration_s=float(ts[-1] - ts[0]) if ts.size else 0.0,
            path_length=0.0,
            end_displacement=0.0,
            x_range=(0.0, 0.0),
            y_range=(0.0, 0.0),
            bbox_diag=0.0,
        )
    step_lengths = np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2)
    x_range = (float(xs.min()), float(xs.max()))
    y_range = (float(ys.min()), float(ys.max()))
    bbox_diag = float(np.hypot(x_range[1] - x_range[0], y_range[1] - y_range[0]))
    return TrajectoryStats(
        points=int(xs.size),
        duration_s=float(ts[-1] - ts[0]),
        path_length=float(step_lengths.sum()),
        end_displacement=float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0])),
        x_range=x_range,
        y_range=y_range,
        bbox_diag=bbox_diag,
    )


def _padded_limits(values: np.ndarray, padding_frac: float = 0.08) -> tuple[float, float]:
    if values.size == 0:
        return -1.0, 1.0
    lo = float(values.min())
    hi = float(values.max())
    span = hi - lo
    if span < 1e-9:
        span = max(abs(hi), 1.0)
        return lo - span * 0.5, hi + span * 0.5
    pad = span * padding_frac
    return lo - pad, hi + pad


def _plot_axes(
    ax: plt.Axes,
    xs: np.ndarray,
    ys: np.ndarray,
    title: str,
    units: str,
    color: str,
    *,
    cursor_idx: int | None = None,
) -> None:
    ax.plot(xs, ys, "-", color=color, linewidth=1.4, alpha=0.85)
    if xs.size:
        ax.scatter([xs[0]], [ys[0]], color="#2a9d8f", s=55, zorder=4, label="start")
    if cursor_idx is None and xs.size:
        ax.scatter([xs[-1]], [ys[-1]], color="#e63946", s=55, zorder=4, label="end")
    elif cursor_idx is not None and 0 <= cursor_idx < xs.size:
        ax.scatter([xs[cursor_idx]], [ys[cursor_idx]], color="#e63946", s=70, zorder=5)
    ax.set_xlim(*_padded_limits(xs))
    ax.set_ylim(*_padded_limits(ys))
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(f"x ({units})")
    ax.set_ylabel(f"y ({units})")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if cursor_idx is None:
        ax.legend(loc="best", fontsize=9)


def _render_png(
    out_path: Path,
    label: str,
    traj_h: list[TrajectoryPoint],
    traj_a: list[TrajectoryPoint],
) -> None:
    xs_h = np.array([p.x for p in traj_h])
    ys_h = np.array([p.y for p in traj_h])
    xs_a = np.array([p.x for p in traj_a])
    ys_a = np.array([p.y for p in traj_a])

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), dpi=140)
    _plot_axes(axes[0], xs_h, ys_h, "Heuristic DR", "viz", "#1f77b4")
    _plot_axes(axes[1], xs_a, ys_a, "AEOLUS (Radeta 2023) DR", "m", "#ff7f0e")
    fig.suptitle(f"Dead reckoning comparison — {label}", fontsize=14, fontweight="bold")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _render_mp4(
    out_path: Path,
    label: str,
    traj_h: list[TrajectoryPoint],
    traj_a: list[TrajectoryPoint],
    *,
    fps: int = 30,
    width: int = 1280,
    height: int = 720,
) -> None:
    import imageio_ffmpeg

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    xs_h = np.array([p.x for p in traj_h])
    ys_h = np.array([p.y for p in traj_h])
    ts_h = np.array([p.t_s for p in traj_h])
    xs_a = np.array([p.x for p in traj_a])
    ys_a = np.array([p.y for p in traj_a])
    ts_a = np.array([p.t_s for p in traj_a])

    duration_s = max(
        float(ts_h[-1]) if ts_h.size else 0.0,
        float(ts_a[-1]) if ts_a.size else 0.0,
    )
    total_frames = max(1, int(np.ceil(duration_s * fps)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-an",
        "-vcodec", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None

    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    canvas = FigureCanvasAgg(fig)

    h_xlim = _padded_limits(xs_h)
    h_ylim = _padded_limits(ys_h)
    a_xlim = _padded_limits(xs_a)
    a_ylim = _padded_limits(ys_a)

    try:
        for frame_idx in range(total_frames):
            time_s = frame_idx / fps
            idx_h = int(np.searchsorted(ts_h, time_s, side="right"))
            idx_a = int(np.searchsorted(ts_a, time_s, side="right"))

            fig.clear()
            ax1 = fig.add_subplot(1, 2, 1)
            ax2 = fig.add_subplot(1, 2, 2)

            cursor_h = min(idx_h - 1, xs_h.size - 1) if idx_h > 0 else None
            cursor_a = min(idx_a - 1, xs_a.size - 1) if idx_a > 0 else None

            _plot_axes(
                ax1,
                xs_h[:max(idx_h, 1)],
                ys_h[:max(idx_h, 1)],
                f"Heuristic DR  t={time_s:5.2f}s",
                "viz",
                "#1f77b4",
                cursor_idx=cursor_h,
            )
            ax1.set_xlim(*h_xlim)
            ax1.set_ylim(*h_ylim)

            _plot_axes(
                ax2,
                xs_a[:max(idx_a, 1)],
                ys_a[:max(idx_a, 1)],
                f"AEOLUS DR  t={time_s:5.2f}s",
                "m",
                "#ff7f0e",
                cursor_idx=cursor_a,
            )
            ax2.set_xlim(*a_xlim)
            ax2.set_ylim(*a_ylim)

            fig.suptitle(f"Dead reckoning comparison — {label}", fontsize=13, fontweight="bold")
            fig.tight_layout(rect=(0, 0, 1, 0.96))
            canvas.draw()
            buf = np.asarray(canvas.buffer_rgba())[..., :3].astype(np.uint8)
            if buf.shape[0] != height or buf.shape[1] != width:
                raise RuntimeError(
                    f"matplotlib canvas produced {buf.shape[1]}x{buf.shape[0]}, expected {width}x{height}"
                )
            process.stdin.write(buf.tobytes())
    finally:
        plt.close(fig)
        process.stdin.close()

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg exited with status {return_code}.")


def compare_one(
    input_path: Path,
    output_dir: Path,
    *,
    fps: int,
    width: int,
    height: int,
    skip_mp4: bool,
) -> dict:
    parsed = parse_sensor_log(input_path)
    if len(parsed.samples) < 2:
        raise ValueError(f"Not enough sensor rows were parsed from {input_path}.")

    traj_h = trajectory_heuristic(parsed.samples)
    traj_a = trajectory_aeolus(parsed.samples)
    stats_h = _trajectory_stats(traj_h)
    stats_a = _trajectory_stats(traj_a)

    stem = input_path.stem
    png_path = output_dir / f"{stem}_dr-compare.png"
    mp4_path = output_dir / f"{stem}_dr-compare.mp4"
    json_path = output_dir / f"{stem}_dr-compare.json"

    label = stem
    _render_png(png_path, label, traj_h, traj_a)
    if not skip_mp4:
        _render_mp4(mp4_path, label, traj_h, traj_a, fps=fps, width=width, height=height)

    summary = {
        "input_file": str(input_path),
        "duration_seconds": round(parsed.duration_s, 3),
        "samples": parsed.metadata.parsed_rows,
        "skipped_rows": parsed.metadata.skipped_rows,
        "heuristic": asdict(stats_h),
        "aeolus": asdict(stats_a),
        "ratios": {
            "path_length_aeolus_over_heuristic": (
                stats_a.path_length / stats_h.path_length
                if stats_h.path_length > 1e-9
                else None
            ),
            "bbox_diag_aeolus_over_heuristic": (
                stats_a.bbox_diag / stats_h.bbox_diag
                if stats_h.bbox_diag > 1e-9
                else None
            ),
        },
        "artifacts": {
            "png": str(png_path),
            "mp4": str(mp4_path) if not skip_mp4 else None,
            "json": str(json_path),
        },
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the heuristic and AEOLUS dead-reckoning methods on a sensor log."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Sensor log .txt files.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write JSON/PNG/MP4 artefacts into.",
    )
    parser.add_argument("--fps", type=int, default=30, help="MP4 frame rate. Default: 30")
    parser.add_argument("--width", type=int, default=1280, help="MP4 width. Default: 1280")
    parser.add_argument("--height", type=int, default=720, help="MP4 height. Default: 720")
    parser.add_argument(
        "--skip-mp4",
        action="store_true",
        help="Skip the MP4 render (PNG and JSON only).",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for input_path in args.inputs:
        input_path = input_path.resolve()
        print(f"==> {input_path.name}")
        summary = compare_one(
            input_path,
            args.output_dir,
            fps=args.fps,
            width=args.width,
            height=args.height,
            skip_mp4=args.skip_mp4,
        )
        h = summary["heuristic"]
        a = summary["aeolus"]
        print(
            f"    heuristic: path={h['path_length']:.3f} viz, "
            f"bbox-diag={h['bbox_diag']:.3f} viz, end-disp={h['end_displacement']:.3f} viz"
        )
        print(
            f"    aeolus   : path={a['path_length']:.3f} m,   "
            f"bbox-diag={a['bbox_diag']:.3f} m,   end-disp={a['end_displacement']:.3f} m"
        )
        for artefact, path in summary["artifacts"].items():
            if path:
                print(f"    {artefact}: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
