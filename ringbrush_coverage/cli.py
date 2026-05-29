from __future__ import annotations

import argparse
import json
from pathlib import Path

from ringbrush_coverage.core import (
    DISPLAY_NAMES,
    DR_METHODS,
    SURFACE_LABELS,
    analysis_report,
    analyze_session,
    parse_sensor_log,
)
from ringbrush_coverage.render import render_mp4
from ringbrush_coverage.video_anchor import compute_video_dr_per_window


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ringbrush-coverage",
        description="Convert smart ring sensor logs into a mouth-coverage MP4 visualization.",
    )
    parser.add_argument("input", type=Path, help="Path to a .txt sensor log.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output MP4 path. Defaults to <input-stem>-coverage.mp4 in the current directory.",
    )
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        help="Directory containing labeled *-only.txt calibration sessions.",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Optional path for a JSON summary report.",
    )
    parser.add_argument("--fps", type=int, default=30, help="Output video frame rate. Default: 30")
    parser.add_argument("--width", type=int, default=1280, help="Video width in pixels. Default: 1280")
    parser.add_argument("--height", type=int, default=720, help="Video height in pixels. Default: 720")
    parser.add_argument(
        "--target-zone-seconds",
        type=float,
        default=12.0,
        help="Seconds of accumulated brushing needed for a fully-covered zone. Default: 12.0",
    )
    parser.add_argument("--window-size", type=int, default=80, help="Samples per analysis window. Default: 80")
    parser.add_argument("--window-step", type=int, default=20, help="Samples between windows. Default: 20")
    parser.add_argument(
        "--dr-method",
        choices=DR_METHODS,
        default="heuristic",
        help=(
            "Dead-reckoning method: 'heuristic' (in-house mean-subtraction "
            "integrator) or 'aeolus' (Radeta 2023 pipeline). Default: heuristic."
        ),
    )
    parser.add_argument(
        "--heuristic-params",
        type=Path,
        help=(
            "Optional JSON file with overrides for the heuristic DR constants "
            "{damping, accel_scale, yaw_contrib, pitch_contrib}. Produced by "
            "tools/calibrate_dr_from_video.py."
        ),
    )
    parser.add_argument(
        "--video-sync-csv",
        type=Path,
        help=(
            "Synchronized format-3 CSV (output of tools/sync_video_imu.py). "
            "Required when --dr-method=video-anchored; ignored otherwise. "
            "Per-window Δwrist replaces the IMU DR estimate where the video "
            "has coverage; windows without coverage fall back to the heuristic."
        ),
    )
    parser.add_argument(
        "--video-scale",
        type=float,
        default=1.0,
        help=(
            "User-side multiplier on top of the per-session auto-rescale that already "
            "matches the AEOLUS P90 target. Default 1.0."
        ),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Run the analysis and optional JSON export without rendering MP4.",
    )
    return parser


def _default_output_path(input_path: Path) -> Path:
    return Path.cwd() / f"{input_path.stem}-coverage.mp4"


def _print_report(report: dict[str, object], output_path: Path | None) -> None:
    print(f"Input file: {report['input_file']}")
    print(f"Calibration: {report['calibration_source']}")
    print(
        f"Parsed rows: {report['parsed_rows']} "
        f"(skipped {report['skipped_rows']})"
    )
    print(f"Duration: {report['duration_seconds']} s")
    print("Coverage:")
    for label in SURFACE_LABELS:
        zone = report["zones"][label]
        print(
            f"  - {DISPLAY_NAMES[label]}: "
            f"{zone['coverage_percent']:>5.1f}% "
            f"({zone['coverage_seconds']:.2f} weighted s)"
        )
    if output_path is not None:
        print(f"MP4: {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = args.input.resolve()

    heuristic_params: dict[str, float] | None = None
    if args.heuristic_params is not None:
        heuristic_params = json.loads(args.heuristic_params.read_text(encoding="utf-8"))
        heuristic_params = {k: v for k, v in heuristic_params.items() if not k.startswith("_")}

    video_dr_values = None
    if args.dr_method == "video-anchored":
        if args.video_sync_csv is None:
            parser.error("--video-sync-csv is required when --dr-method=video-anchored")
        parsed = parse_sensor_log(input_path)
        video_dr_values = compute_video_dr_per_window(
            parsed.samples,
            args.video_sync_csv.resolve(),
            window_size=args.window_size,
            window_step=args.window_step,
            scale=args.video_scale,
        )

    analysis = analyze_session(
        input_path,
        calibration_dir=args.calibration_dir.resolve() if args.calibration_dir else None,
        window_size=args.window_size,
        window_step=args.window_step,
        target_zone_seconds=args.target_zone_seconds,
        dr_method=args.dr_method,
        heuristic_params=heuristic_params,
        video_dr_values=video_dr_values,
    )
    report = analysis_report(analysis)

    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    output_path: Path | None = None
    if not args.report_only:
        output_path = args.output.resolve() if args.output else _default_output_path(input_path)
        render_mp4(
            analysis,
            output_path,
            fps=args.fps,
            width=args.width,
            height=args.height,
        )

    _print_report(report, output_path)
    return 0
