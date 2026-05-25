from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from ringbrush_coverage.core import (
    ALL_LABELS,
    SensorSample,
    analyze_session,
    discover_labeled_sessions,
    estimate_dead_reckoning,
    estimate_dead_reckoning_aeolus,
    load_default_calibration,
    parse_sensor_log,
    trajectory_aeolus,
    trajectory_heuristic,
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

    def test_analyze_session_accepts_aeolus_dr_method(self) -> None:
        rows = ["t_ms,roll,pitch,yaw,ax,ay,az"]
        for index in range(90):
            rows.append(
                f"{1000 + index * 12},"
                f"{210 + index * 0.1:.2f},-10.0,{20 + index * 0.5:.2f},"
                f"{-2.0 + 0.05 * index:.3f},{-4.0:.3f},{8.5:.3f}"
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mini_outer-front-only.txt"
            path.write_text("\n".join(rows), encoding="utf-8")
            analysis = analyze_session(
                path, window_size=40, window_step=20, dr_method="aeolus"
            )
        self.assertEqual(analysis.dr_method, "aeolus")
        for window in analysis.windows:
            for component in window.dead_reckoning:
                self.assertTrue(np.isfinite(component))


class DeadReckoningTests(unittest.TestCase):
    @staticmethod
    def _stationary_samples(n: int = 20) -> list[SensorSample]:
        # IMU at rest, level: gravity reads on +z, roll=pitch=yaw=0.
        return [
            SensorSample(t_ms=i * 50.0, roll=0.0, pitch=0.0, yaw=0.0, ax=0.0, ay=0.0, az=9.81)
            for i in range(n)
        ]

    def test_aeolus_stationary_gives_negligible_motion(self) -> None:
        # With perfect orientation and gravity removal, a level stationary IMU
        # should integrate to (near-)zero displacement.
        samples = self._stationary_samples()
        x, y = estimate_dead_reckoning_aeolus(samples)
        self.assertAlmostEqual(x, 0.0, places=4)
        self.assertAlmostEqual(y, 0.0, places=4)

    def test_aeolus_short_sample_returns_zero(self) -> None:
        self.assertEqual(estimate_dead_reckoning_aeolus([]), (0.0, 0.0))

    def test_trajectory_lengths_match_sample_count(self) -> None:
        samples = self._stationary_samples(n=15)
        traj_h = trajectory_heuristic(samples)
        traj_a = trajectory_aeolus(samples)
        self.assertEqual(len(traj_h), len(samples))
        self.assertEqual(len(traj_a), len(samples))

    def test_aeolus_responds_to_body_x_acceleration(self) -> None:
        # Yaw=0 (so cos(yaw)=1, sin(yaw)=0), pitch=0, roll=0. A sustained
        # body-x acceleration above the ZVU threshold should produce net
        # motion along world x.
        samples = []
        for i in range(40):
            samples.append(
                SensorSample(
                    t_ms=i * 50.0,
                    roll=0.0,
                    pitch=0.0,
                    yaw=0.0,
                    ax=0.6,  # well above zvu_threshold=0.05
                    ay=0.0,
                    az=9.81,
                )
            )
        x, y = estimate_dead_reckoning_aeolus(samples)
        self.assertGreater(x, 0.0)
        self.assertAlmostEqual(y, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
