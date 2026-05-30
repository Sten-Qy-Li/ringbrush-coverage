"""Linearly interpolate one extra sample between every consecutive pair of IMU rows.

For each consecutive pair (i, i+1) in the input log, emit:
  * the original row i, then
  * a new row whose t_ms is the midpoint and whose other 6 channels are the
    arithmetic mean of the two endpoints' values.

The angular channels (roll, pitch, yaw) are interpolated by the shortest
circular path so a 359°-to-1° wrap does not produce a phantom 180° spike.

Effective sample rate doubles from ~80 Hz to ~160 Hz. The last original row
is appended at the end without a trailing midpoint.

This is the standard "linear interpolation oversampling" used to test
whether the dead-reckoning pipeline is sample-rate-limited. Per Nyquist–
Shannon, interpolation introduces no new signal content above the original
Nyquist frequency (~40 Hz) — so this experiment is expected to leave the
pipeline outputs essentially unchanged. Run it and verify rather than
assume.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


HEADER = "t_ms,roll,pitch,yaw,ax,ay,az"


def _circular_midpoint(a_deg: float, b_deg: float) -> float:
    """Interpolate two angles in degrees along the shorter arc."""
    diff = ((b_deg - a_deg + 180.0) % 360.0) - 180.0
    return (a_deg + 0.5 * diff) % 360.0


def oversample(input_path: Path, output_path: Path) -> dict:
    rows: list[list[float]] = []
    skipped = 0
    total = 0
    with input_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for raw_line in fh:
            total += 1
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if parts[:7] == HEADER.split(","):
                continue
            if len(parts) != 7:
                skipped += 1
                continue
            try:
                row = [float(p) for p in parts]
            except ValueError:
                skipped += 1
                continue
            rows.append(row)

    if len(rows) < 2:
        raise ValueError(f"Not enough usable rows in {input_path}.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        for i in range(len(rows) - 1):
            a = rows[i]
            b = rows[i + 1]
            fh.write(",".join(_format(v) for v in a) + "\n")
            mid = [
                0.5 * (a[0] + b[0]),
                _circular_midpoint(a[1], b[1]),
                _circular_midpoint(a[2], b[2]),
                _circular_midpoint(a[3], b[3]),
                0.5 * (a[4] + b[4]),
                0.5 * (a[5] + b[5]),
                0.5 * (a[6] + b[6]),
            ]
            fh.write(",".join(_format(v) for v in mid) + "\n")
        fh.write(",".join(_format(v) for v in rows[-1]) + "\n")

    return {
        "input": str(input_path),
        "output": str(output_path),
        "input_rows": len(rows),
        "skipped_rows": skipped,
        "output_rows": 2 * len(rows) - 1,
    }


def _format(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    summary = oversample(args.input.resolve(), args.output.resolve())
    print(f"in : {summary['input_rows']:>6d} rows ({summary['skipped_rows']} skipped)")
    print(f"out: {summary['output_rows']:>6d} rows -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
