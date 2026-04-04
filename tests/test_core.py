from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from ringbrush_coverage.core import (
    ALL_LABELS,
    analyze_session,
    discover_labeled_sessions,
    load_default_calibration,
    parse_sensor_log,
)


class ParseSensorLogTests(unittest.TestCase):
    def test_parse_sensor_log_skips_noise_and_non_monotonic_rows(self) -> None:
        sample_text = "\n".join(
            [
                "Boot message",
                "t_ms,roll,pitch,yaw,ax,ay,az",
                "1000,1,2,3,4,5,6",
                "1000,7,8,9,1,2,3",
                "oops",
                "6000,2,3,4,5,6,7",
                "1012,2,3,4,5,6,7",
                "-1,0,0,0,0,0,0",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.txt"
            path.write_text(sample_text, encoding="utf-8")
            parsed = parse_sensor_log(path)

        self.assertEqual(parsed.metadata.parsed_rows, 2)
        self.assertEqual(parsed.metadata.skipped_rows, 5)
        self.assertEqual(parsed.samples[0].t_ms, 1000)
        self.assertEqual(parsed.samples[1].t_ms, 1012)


class CalibrationTests(unittest.TestCase):
    def test_discover_labeled_sessions_matches_expected_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in (
                "a_outer-front-only.txt",
                "b_outer-left-only.txt",
                "c_outer-right-only.txt",
                "d_inner-upper-only.txt",
                "e_inner-lower-only.txt",
                "f_no-movement-idle.txt",
                "ignored.txt",
            ):
                (root / name).write_text("", encoding="utf-8")

            discovered = discover_labeled_sessions(root)

        self.assertEqual(set(discovered), set(ALL_LABELS))

    def test_default_calibration_prefers_matching_label(self) -> None:
        calibration = load_default_calibration()
        prototype = calibration.prototypes["outer-front"]
        probabilities, _ = calibration.classify(prototype.mean.copy(), activity=0.9)
        self.assertEqual(max(probabilities, key=probabilities.get), "outer-front")


class AnalysisTests(unittest.TestCase):
    def test_analyze_session_builds_windows_for_minimal_log(self) -> None:
        rows = [
            "t_ms,roll,pitch,yaw,ax,ay,az",
        ]
        for index in range(90):
            rows.append(f"{1000 + index * 12},{210 + index * 0.1:.2f},-10.0,20.0,-2.0,-4.0,8.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mini_outer-front-only.txt"
            path.write_text("\n".join(rows), encoding="utf-8")
            analysis = analyze_session(path, window_size=40, window_step=20)

        self.assertGreaterEqual(len(analysis.windows), 1)
        self.assertTrue(all(np.isfinite(value) for value in analysis.coverage_ratio.values()))


if __name__ == "__main__":
    unittest.main()
