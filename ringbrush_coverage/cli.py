from __future__ import annotations

import argparse
import json
from pathlib import Path

from ringbrush_coverage.core import DISPLAY_NAMES, SURFACE_LABELS, analysis_report, analyze_session
from ringbrush_coverage.render import render_mp4


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
    parser.add_argument("--fps", type=int, default=2, help="Output video frame rate. Default: 2")
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

    analysis = analyze_session(
        input_path,
        calibration_dir=args.calibration_dir.resolve() if args.calibration_dir else None,
        window_size=args.window_size,
        window_step=args.window_step,
        target_zone_seconds=args.target_zone_seconds,
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
