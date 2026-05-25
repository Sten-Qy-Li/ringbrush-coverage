from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

SURFACE_LABELS: tuple[str, ...] = (
    "outer-left",
    "outer-front",
    "outer-right",
    "inner-upper",
    "inner-lower",
)
ALL_LABELS: tuple[str, ...] = SURFACE_LABELS + ("idle",)

DISPLAY_NAMES = {
    "outer-left": "Outer left",
    "outer-front": "Outer front",
    "outer-right": "Outer right",
    "inner-upper": "Inner upper",
    "inner-lower": "Inner lower",
    "idle": "Idle",
}

ZONE_ANCHORS = {
    "outer-left": (0.20, 0.50),
    "outer-front": (0.50, 0.40),
    "outer-right": (0.80, 0.50),
    "inner-upper": (0.50, 0.22),
    "inner-lower": (0.50, 0.78),
    "idle": (0.50, 0.50),
}

FEATURE_NAMES = (
    "roll_sin",
    "roll_cos",
    "pitch_mean",
    "yaw_sin",
    "yaw_cos",
    "ax_mean",
    "ay_mean",
    "az_mean",
    "ax_std",
    "ay_std",
    "az_std",
    "accel_mean",
    "accel_std",
    "angular_speed_mean",
    "angular_speed_std",
)

DISCOVERY_PATTERNS = {
    "outer-front": "outer-front-only",
    "outer-left": "outer-left-only",
    "outer-right": "outer-right-only",
    "inner-upper": "inner-upper-only",
    "inner-lower": "inner-lower-only",
    "idle": "no-movement-idle",
}

DEFAULT_CALIBRATION = {
    "outer-front": {
        "mean": [-0.3679, 0.1218, -18.3812, 0.6876, 0.4627, -2.4937, -6.0699, 4.4578, 5.1161, 4.3591, 2.9357, 12.3230, 3.1525, 117.2478, 88.8263],
        "std": [0.4500, 0.7881, 15.5954, 0.3140, 0.4351, 2.1436, 3.2311, 3.8401, 2.7394, 2.9562, 0.8455, 1.1205, 0.6467, 47.0314, 44.4761],
    },
    "outer-left": {
        "mean": [-0.2971, 0.7203, -17.7414, 0.0679, 0.9500, -3.1447, -0.8621, 8.4486, 3.4449, 7.0581, 2.0829, 12.4342, 3.0879, 160.7184, 163.4641],
        "std": [0.3588, 0.4773, 17.6141, 0.1208, 0.2015, 2.6177, 0.9900, 1.6337, 0.7591, 2.6297, 0.5327, 1.3498, 0.5869, 98.7975, 327.2543],
    },
    "outer-right": {
        "mean": [-0.4860, 0.8054, -16.1733, 0.0622, 0.5746, -2.3061, -0.7620, 5.6348, 6.9961, 4.0641, 3.1906, 12.1322, 4.6008, 190.8399, 325.4408],
        "std": [0.2554, 0.1479, 26.5193, 0.5258, 0.5163, 3.5289, 4.7889, 3.7679, 2.3905, 1.2711, 1.3063, 1.2451, 1.4158, 206.7980, 643.1315],
    },
    "inner-upper": {
        "mean": [-0.7027, 0.6044, -0.5374, -0.0480, 0.8434, 0.6286, 0.6444, 7.8924, 5.1973, 3.9237, 1.6249, 11.4719, 2.2246, 118.6575, 133.5116],
        "std": [0.2566, 0.2135, 23.8043, 0.2265, 0.4085, 3.4555, 1.7342, 3.0222, 1.9319, 1.3560, 0.8551, 1.1408, 0.8230, 141.4881, 316.9143],
    },
    "inner-lower": {
        "mean": [0.0869, 0.2705, -10.8941, 0.3198, 0.8018, -1.6917, -3.0082, 7.5749, 4.2770, 1.9245, 2.0552, 10.7482, 2.2568, 106.6487, 85.9308],
        "std": [0.6730, 0.6678, 20.4210, 0.2475, 0.3839, 3.1459, 1.9092, 3.1357, 1.1465, 0.5114, 0.9619, 0.4480, 0.7543, 67.7011, 118.6657],
    },
    "idle": {
        "mean": [-0.7186, -0.6953, 31.3957, 0.3526, 0.9357, 5.0879, -2.8709, 7.8232, 0.0740, 0.0452, 0.0504, 9.7667, 0.0243, 4.9656, 6.6539],
        "std": [0.0069, 0.0072, 1.0233, 0.0100, 0.0037, 0.1419, 0.0536, 0.1113, 0.0968, 0.0448, 0.0461, 0.0058, 0.0185, 7.3698, 8.9082],
    },
}


@dataclass(frozen=True)
class SensorSample:
    t_ms: float
    roll: float
    pitch: float
    yaw: float
    ax: float
    ay: float
    az: float


@dataclass(frozen=True)
class ParseMetadata:
    total_lines: int
    parsed_rows: int
    skipped_rows: int


@dataclass(frozen=True)
class ParsedSession:
    samples: list[SensorSample]
    metadata: ParseMetadata

    @property
    def duration_s(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return max(self.samples[-1].t_ms - self.samples[0].t_ms, 0.0) / 1000.0


@dataclass(frozen=True)
class WindowPrediction:
    start_s: float
    end_s: float
    center_s: float
    activity: float
    confidence: float
    dominant_label: str
    probabilities: dict[str, float]
    cursor: tuple[float, float]
    dead_reckoning: tuple[float, float]
    coverage_seconds: dict[str, float]


@dataclass(frozen=True)
class SessionAnalysis:
    source_path: Path
    calibration_source: str
    parsed_session: ParsedSession
    windows: list[WindowPrediction]
    coverage_seconds: dict[str, float]
    coverage_ratio: dict[str, float]
    target_zone_seconds: float
    dr_method: str = "heuristic"


@dataclass(frozen=True)
class CalibrationPrototype:
    mean: np.ndarray
    std: np.ndarray


@dataclass(frozen=True)
class CalibrationModel:
    prototypes: dict[str, CalibrationPrototype]
    source: str

    def classify(self, feature: np.ndarray, activity: float) -> tuple[dict[str, float], float]:
        raw_scores: dict[str, float] = {}
        for label, prototype in self.prototypes.items():
            scale = np.maximum(prototype.std, 0.08)
            distance = float(np.sqrt(np.mean(np.square((feature - prototype.mean) / scale))))
            raw_scores[label] = -distance

        if activity < 0.18:
            raw_scores["idle"] += 1.6
        else:
            raw_scores["idle"] -= 0.5 + activity

        labels = list(raw_scores)
        logits = np.array([raw_scores[label] * 1.35 for label in labels], dtype=float)
        logits -= np.max(logits)
        exp_logits = np.exp(logits)
        probs_arr = exp_logits / np.sum(exp_logits)
        probabilities = {label: float(prob) for label, prob in zip(labels, probs_arr)}

        if activity > 0.28:
            probabilities["idle"] *= 0.30
            probabilities = normalize_probabilities(probabilities)

        ranked = sorted(probabilities.values(), reverse=True)
        confidence = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
        return probabilities, float(confidence)


def normalize_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    total = sum(probabilities.values()) or 1.0
    return {label: value / total for label, value in probabilities.items()}


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def circular_delta(a_deg: float, b_deg: float) -> float:
    return ((a_deg - b_deg + 180.0) % 360.0) - 180.0


def circular_mean_components(values: Iterable[float]) -> tuple[float, float]:
    angles = [math.radians(value) for value in values]
    sin_mean = sum(math.sin(angle) for angle in angles) / len(angles)
    cos_mean = sum(math.cos(angle) for angle in angles) / len(angles)
    return sin_mean, cos_mean


def parse_sensor_log(path: Path) -> ParsedSession:
    total_lines = 0
    skipped_rows = 0
    samples: list[SensorSample] = []
    previous_timestamp: float | None = None

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            total_lines += 1
            line = raw_line.strip()
            if not line:
                skipped_rows += 1
                continue

            parts = [part.strip() for part in line.split(",")]
            if parts[:7] == ["t_ms", "roll", "pitch", "yaw", "ax", "ay", "az"]:
                continue
            if len(parts) != 7:
                skipped_rows += 1
                continue

            try:
                values = [float(part) for part in parts]
            except ValueError:
                skipped_rows += 1
                continue

            t_ms = values[0]
            if t_ms < 0:
                skipped_rows += 1
                continue
            if previous_timestamp is not None and t_ms <= previous_timestamp:
                skipped_rows += 1
                continue
            if previous_timestamp is not None and (t_ms - previous_timestamp) > 1000.0:
                skipped_rows += 1
                continue

            sample = SensorSample(
                t_ms=t_ms,
                roll=values[1],
                pitch=values[2],
                yaw=values[3],
                ax=values[4],
                ay=values[5],
                az=values[6],
            )
            samples.append(sample)
            previous_timestamp = t_ms

    metadata = ParseMetadata(
        total_lines=total_lines,
        parsed_rows=len(samples),
        skipped_rows=skipped_rows,
    )
    return ParsedSession(samples=samples, metadata=metadata)


def discover_labeled_sessions(calibration_dir: Path) -> dict[str, list[Path]]:
    discovered: dict[str, list[Path]] = {label: [] for label in ALL_LABELS}
    for file_path in sorted(calibration_dir.glob("*.txt")):
        lower_name = file_path.name.lower()
        for label, marker in DISCOVERY_PATTERNS.items():
            if marker in lower_name:
                discovered[label].append(file_path)
                break
    return {label: paths for label, paths in discovered.items() if paths}


def feature_vector(samples: list[SensorSample]) -> tuple[np.ndarray, dict[str, float]]:
    if len(samples) < 2:
        raise ValueError("At least two samples are required to compute features.")

    roll = [sample.roll for sample in samples]
    pitch = [sample.pitch for sample in samples]
    yaw = [sample.yaw for sample in samples]
    ax = [sample.ax for sample in samples]
    ay = [sample.ay for sample in samples]
    az = [sample.az for sample in samples]
    accel_mag = [math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2) for sample in samples]

    angular_speed: list[float] = []
    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)
        delta_roll = abs(circular_delta(curr.roll, prev.roll))
        delta_pitch = abs(curr.pitch - prev.pitch)
        delta_yaw = abs(circular_delta(curr.yaw, prev.yaw))
        angular_speed.append((delta_roll + delta_pitch + delta_yaw) / dt)

    roll_sin, roll_cos = circular_mean_components(roll)
    yaw_sin, yaw_cos = circular_mean_components(yaw)

    vector = np.array(
        [
            roll_sin,
            roll_cos,
            float(np.mean(pitch)),
            yaw_sin,
            yaw_cos,
            float(np.mean(ax)),
            float(np.mean(ay)),
            float(np.mean(az)),
            float(np.std(ax)),
            float(np.std(ay)),
            float(np.std(az)),
            float(np.mean(accel_mag)),
            float(np.std(accel_mag)),
            float(np.mean(angular_speed)),
            float(np.std(angular_speed)),
        ],
        dtype=float,
    )

    activity = (
        0.62 * sigmoid((vector[13] - 18.0) / 24.0)
        + 0.38 * sigmoid((vector[12] - 0.32) / 0.55)
    )

    metrics = {
        "activity": float(min(max(activity, 0.0), 1.0)),
        "accel_std": float(vector[12]),
        "angular_speed_mean": float(vector[13]),
        "angular_speed_std": float(vector[14]),
    }
    return vector, metrics


def estimate_dead_reckoning(samples: list[SensorSample]) -> tuple[float, float]:
    if len(samples) < 2:
        return 0.0, 0.0

    mean_ax = float(np.mean([sample.ax for sample in samples]))
    mean_ay = float(np.mean([sample.ay for sample in samples]))
    velocity_x = 0.0
    velocity_y = 0.0
    pos_x = 0.0
    pos_y = 0.0

    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)
        dyn_ax = curr.ax - mean_ax
        dyn_ay = curr.ay - mean_ay
        velocity_x = (velocity_x * 0.92) + (dyn_ax * dt * 2.00)
        velocity_y = (velocity_y * 0.92) - (dyn_ay * dt * 2.00)
        pos_x += velocity_x * dt
        pos_y += velocity_y * dt
        pos_x += circular_delta(curr.yaw, prev.yaw) * 0.0015
        pos_y -= (curr.pitch - prev.pitch) * 0.0000

    return float(pos_x), float(pos_y)


# ---------------------------------------------------------------------------
# Radeta 2023 ("AEOLUS") dead reckoning, ported from §3.3–3.6 of
# "Lost in the Deep? Performance Evaluation of Dead Reckoning Techniques in
#  Underwater Environments" (Radeta et al., ACM IMWUT 2023).
# ---------------------------------------------------------------------------

AEOLUS_GRAVITY = 9.81
AEOLUS_ZVU_THRESHOLD = 0.05
AEOLUS_ZVU_DECAY_XY = 0.55
AEOLUS_ZVU_DECAY_Z = 0.80


def _gravity_in_body_frame(roll_deg: float, pitch_deg: float, gravity: float = AEOLUS_GRAVITY) -> np.ndarray:
    # Assumes ZYX intrinsic Tait-Bryan Euler convention with world +z up. If
    # the device uses a different convention there will be residual gravity
    # after subtraction, which manifests as faster drift in the integrated
    # trajectory.
    r = math.radians(roll_deg)
    p = math.radians(pitch_deg)
    return np.array(
        [-math.sin(p), math.cos(p) * math.sin(r), math.cos(p) * math.cos(r)],
        dtype=float,
    ) * gravity


def estimate_dead_reckoning_aeolus(
    samples: list[SensorSample],
    *,
    gravity: float = AEOLUS_GRAVITY,
    zvu_threshold: float = AEOLUS_ZVU_THRESHOLD,
    zvu_decay_xy: float = AEOLUS_ZVU_DECAY_XY,
    zvu_decay_z: float = AEOLUS_ZVU_DECAY_Z,
) -> tuple[float, float]:
    """Per-window dead reckoning via the Radeta-2023 AEOLUS pipeline.

    Pipeline (faithful to §3.3–3.6 of the paper):
      1. Linear acceleration in body frame: subtract the expected gravity
         vector (derived from roll & pitch under the ZYX convention) from
         the raw accelerometer reading.
      2. Drift reduction (Algorithm 1, transcribed literally as printed in
         the paper): if |a_axis| < threshold then v_axis *= decay, else
         v_axis = a_axis * dt. The else branch is a replacement, not an
         accumulation; this is almost certainly a typo in the paper but the
         caller explicitly asked for a faithful port.
      3. Position via equation 9: x_i = dx*cos(yaw) + x_{i-1},
                                  y_i = dy*sin(yaw) + y_{i-1}.

    Returns the cumulative (pos_x, pos_y) in metres relative to the first
    sample of the window.
    """
    if len(samples) < 2:
        return 0.0, 0.0

    vel_x = 0.0
    vel_y = 0.0
    vel_z = 0.0
    pos_x = 0.0
    pos_y = 0.0

    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)

        g_body = _gravity_in_body_frame(curr.roll, curr.pitch, gravity)
        a_lin_x = curr.ax - g_body[0]
        a_lin_y = curr.ay - g_body[1]
        a_lin_z = curr.az - g_body[2]

        if abs(a_lin_x) < zvu_threshold:
            vel_x = vel_x * zvu_decay_xy
        else:
            vel_x = a_lin_x * dt
        if abs(a_lin_y) < zvu_threshold:
            vel_y = vel_y * zvu_decay_xy
        else:
            vel_y = a_lin_y * dt
        if abs(a_lin_z) < zvu_threshold:
            vel_z = vel_z * zvu_decay_z
        else:
            vel_z = a_lin_z * dt

        dx_body = vel_x * dt
        dy_body = vel_y * dt

        yaw_rad = math.radians(curr.yaw)
        pos_x += dx_body * math.cos(yaw_rad)
        pos_y += dy_body * math.sin(yaw_rad)

    return float(pos_x), float(pos_y)


@dataclass(frozen=True)
class TrajectoryPoint:
    t_s: float
    x: float
    y: float


def trajectory_heuristic(samples: list[SensorSample]) -> list[TrajectoryPoint]:
    """Full-session trajectory using the heuristic DR per-step logic.

    Unlike `estimate_dead_reckoning` (which is windowed and resets state per
    window), this runs once across all samples and emits a point per sample.
    Used by the DR comparison tool, where we want a continuous path rather
    than a sequence of independent window displacements.
    """
    if len(samples) < 2:
        return [TrajectoryPoint(0.0, 0.0, 0.0)] if samples else []

    mean_ax = float(np.mean([sample.ax for sample in samples]))
    mean_ay = float(np.mean([sample.ay for sample in samples]))
    velocity_x = 0.0
    velocity_y = 0.0
    pos_x = 0.0
    pos_y = 0.0
    t0_ms = samples[0].t_ms
    points: list[TrajectoryPoint] = [TrajectoryPoint(0.0, 0.0, 0.0)]

    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)
        dyn_ax = curr.ax - mean_ax
        dyn_ay = curr.ay - mean_ay
        velocity_x = (velocity_x * 0.92) + (dyn_ax * dt * 2.00)
        velocity_y = (velocity_y * 0.92) - (dyn_ay * dt * 2.00)
        pos_x += velocity_x * dt
        pos_y += velocity_y * dt
        pos_x += circular_delta(curr.yaw, prev.yaw) * 0.0015
        points.append(TrajectoryPoint((curr.t_ms - t0_ms) / 1000.0, float(pos_x), float(pos_y)))

    return points


def trajectory_aeolus(
    samples: list[SensorSample],
    *,
    gravity: float = AEOLUS_GRAVITY,
    zvu_threshold: float = AEOLUS_ZVU_THRESHOLD,
    zvu_decay_xy: float = AEOLUS_ZVU_DECAY_XY,
    zvu_decay_z: float = AEOLUS_ZVU_DECAY_Z,
) -> list[TrajectoryPoint]:
    """Full-session trajectory using the AEOLUS DR per-step logic."""
    if len(samples) < 2:
        return [TrajectoryPoint(0.0, 0.0, 0.0)] if samples else []

    vel_x = 0.0
    vel_y = 0.0
    vel_z = 0.0
    pos_x = 0.0
    pos_y = 0.0
    t0_ms = samples[0].t_ms
    points: list[TrajectoryPoint] = [TrajectoryPoint(0.0, 0.0, 0.0)]

    for prev, curr in zip(samples, samples[1:]):
        dt = max((curr.t_ms - prev.t_ms) / 1000.0, 1e-3)

        g_body = _gravity_in_body_frame(curr.roll, curr.pitch, gravity)
        a_lin_x = curr.ax - g_body[0]
        a_lin_y = curr.ay - g_body[1]
        a_lin_z = curr.az - g_body[2]

        if abs(a_lin_x) < zvu_threshold:
            vel_x = vel_x * zvu_decay_xy
        else:
            vel_x = a_lin_x * dt
        if abs(a_lin_y) < zvu_threshold:
            vel_y = vel_y * zvu_decay_xy
        else:
            vel_y = a_lin_y * dt
        if abs(a_lin_z) < zvu_threshold:
            vel_z = vel_z * zvu_decay_z
        else:
            vel_z = a_lin_z * dt

        dx_body = vel_x * dt
        dy_body = vel_y * dt

        yaw_rad = math.radians(curr.yaw)
        pos_x += dx_body * math.cos(yaw_rad)
        pos_y += dy_body * math.sin(yaw_rad)
        points.append(TrajectoryPoint((curr.t_ms - t0_ms) / 1000.0, float(pos_x), float(pos_y)))

    return points


def build_calibration_from_directory(
    calibration_dir: Path,
    window_size: int,
    window_step: int,
) -> CalibrationModel:
    discovered = discover_labeled_sessions(calibration_dir)
    missing = [label for label in ALL_LABELS if label not in discovered]
    if missing:
        raise ValueError(
            "Calibration directory is missing labeled sessions for: "
            + ", ".join(missing)
        )

    prototypes: dict[str, CalibrationPrototype] = {}
    for label, paths in discovered.items():
        features = []
        for path in paths:
            parsed = parse_sensor_log(path)
            for chunk in iter_windows(parsed.samples, window_size, window_step):
                vector, _ = feature_vector(chunk)
                features.append(vector)
        if not features:
            raise ValueError(f"No usable calibration windows found for {label}.")
        matrix = np.vstack(features)
        prototypes[label] = CalibrationPrototype(
            mean=np.mean(matrix, axis=0),
            std=np.maximum(np.std(matrix, axis=0), 1e-3),
        )

    return CalibrationModel(
        prototypes=prototypes,
        source=f"calibrated from {calibration_dir}",
    )


def load_default_calibration() -> CalibrationModel:
    prototypes = {
        label: CalibrationPrototype(
            mean=np.array(values["mean"], dtype=float),
            std=np.array(values["std"], dtype=float),
        )
        for label, values in DEFAULT_CALIBRATION.items()
    }
    return CalibrationModel(prototypes=prototypes, source="bundled sample-derived defaults")


def iter_windows(
    samples: list[SensorSample],
    window_size: int,
    window_step: int,
) -> Iterable[list[SensorSample]]:
    if not samples:
        return []
    if len(samples) <= window_size:
        return [samples]

    windows: list[list[SensorSample]] = []
    last_start = max(len(samples) - window_size, 0)
    for start in range(0, last_start + 1, window_step):
        windows.append(samples[start : start + window_size])
    if windows and windows[-1][-1] != samples[-1]:
        windows.append(samples[-window_size:])
    return windows


def choose_calibration(
    input_path: Path,
    calibration_dir: Path | None,
    window_size: int,
    window_step: int,
) -> CalibrationModel:
    if calibration_dir is not None:
        return build_calibration_from_directory(calibration_dir, window_size, window_step)

    try:
        discovered = discover_labeled_sessions(input_path.parent)
        if all(label in discovered for label in ALL_LABELS):
            return build_calibration_from_directory(input_path.parent, window_size, window_step)
    except ValueError:
        pass

    return load_default_calibration()


def smooth_probabilities(
    previous: dict[str, float] | None,
    current: dict[str, float],
    activity: float,
) -> dict[str, float]:
    if previous is None:
        smoothed = dict(current)
    else:
        blend = 0.28 + (0.18 * activity)
        smoothed = {
            label: ((1.0 - blend) * previous[label]) + (blend * current[label])
            for label in ALL_LABELS
        }

    if activity > 0.30:
        smoothed["idle"] *= 0.35
    return normalize_probabilities(smoothed)


def weighted_anchor(probabilities: dict[str, float]) -> tuple[float, float]:
    total = sum(probabilities[label] for label in SURFACE_LABELS)
    if total <= 1e-9:
        return ZONE_ANCHORS["idle"]

    x = 0.0
    y = 0.0
    for label in SURFACE_LABELS:
        weight = probabilities[label]
        anchor_x, anchor_y = ZONE_ANCHORS[label]
        x += anchor_x * weight
        y += anchor_y * weight
    return x / total, y / total


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def coverage_ratio(coverage_seconds: dict[str, float], target_zone_seconds: float) -> dict[str, float]:
    return {
        label: 1.0 - math.exp(-(coverage_seconds[label] / max(target_zone_seconds, 1e-6)))
        for label in SURFACE_LABELS
    }


DR_METHODS = ("heuristic", "aeolus")
AEOLUS_NORMALIZATION_TARGET_P90 = 0.35


def _select_dr_function(dr_method: str):
    if dr_method == "heuristic":
        return estimate_dead_reckoning
    if dr_method == "aeolus":
        return estimate_dead_reckoning_aeolus
    raise ValueError(
        f"Unknown dr_method {dr_method!r}; expected one of {DR_METHODS}."
    )


def analyze_session(
    input_path: Path,
    calibration_dir: Path | None = None,
    *,
    window_size: int = 80,
    window_step: int = 20,
    target_zone_seconds: float = 12.0,
    dr_method: str = "heuristic",
) -> SessionAnalysis:
    parsed = parse_sensor_log(input_path)
    if len(parsed.samples) < 2:
        raise ValueError(f"Not enough sensor rows were parsed from {input_path}.")

    calibration = choose_calibration(input_path, calibration_dir, window_size, window_step)
    windows = iter_windows(parsed.samples, window_size, window_step)
    dr_func = _select_dr_function(dr_method)

    # AEOLUS returns metric meters, the heuristic returns visualization-space
    # units calibrated to a P90 around 0.35. Rescale AEOLUS per-session so the
    # downstream cursor perturbation lives on the same scale and the +/-0.18
    # clamps don't permanently saturate. The heuristic path stays untouched.
    raw_dr_values = [dr_func(window) for window in windows]
    if dr_method == "aeolus":
        magnitudes = [abs(component) for pair in raw_dr_values for component in pair]
        scale = float(np.percentile(magnitudes, 90)) if magnitudes else 0.0
        factor = (AEOLUS_NORMALIZATION_TARGET_P90 / scale) if scale > 1e-6 else 0.0
        dr_values = [(dx * factor, dy * factor) for dx, dy in raw_dr_values]
    else:
        dr_values = raw_dr_values

    smoothed_probabilities: dict[str, float] | None = None
    cumulative_coverage = {label: 0.0 for label in SURFACE_LABELS}
    cursor = ZONE_ANCHORS["idle"]
    prediction_windows: list[WindowPrediction] = []

    for window_samples, (dead_x, dead_y) in zip(windows, dr_values):
        vector, metrics = feature_vector(window_samples)
        raw_probabilities, confidence = calibration.classify(vector, metrics["activity"])
        smoothed_probabilities = smooth_probabilities(
            smoothed_probabilities,
            raw_probabilities,
            metrics["activity"],
        )

        anchor_x, anchor_y = weighted_anchor(smoothed_probabilities)
        target_x = clamp(anchor_x + clamp(dead_x * 0.38, -0.18, 0.18), 0.08, 0.92)
        target_y = clamp(anchor_y + clamp(dead_y * 0.38, -0.16, 0.16), 0.10, 0.90)
        follow = 0.40 + (0.35 * metrics["activity"])
        cursor = (
            clamp((cursor[0] * (1.0 - follow)) + (target_x * follow), 0.08, 0.92),
            clamp((cursor[1] * (1.0 - follow)) + (target_y * follow), 0.10, 0.90),
        )

        duration_s = max(window_samples[-1].t_ms - window_samples[0].t_ms, 0.0) / 1000.0
        update_weight = duration_s * metrics["activity"] * (0.55 + (0.45 * confidence))
        for label in SURFACE_LABELS:
            cumulative_coverage[label] += update_weight * smoothed_probabilities[label]

        dominant_label = max(smoothed_probabilities, key=smoothed_probabilities.get)
        prediction_windows.append(
            WindowPrediction(
                start_s=(window_samples[0].t_ms - parsed.samples[0].t_ms) / 1000.0,
                end_s=(window_samples[-1].t_ms - parsed.samples[0].t_ms) / 1000.0,
                center_s=((window_samples[0].t_ms + window_samples[-1].t_ms) / 2.0 - parsed.samples[0].t_ms) / 1000.0,
                activity=metrics["activity"],
                confidence=confidence,
                dominant_label=dominant_label,
                probabilities=dict(smoothed_probabilities),
                cursor=cursor,
                dead_reckoning=(dead_x, dead_y),
                coverage_seconds=dict(cumulative_coverage),
            )
        )

    return SessionAnalysis(
        source_path=input_path,
        calibration_source=calibration.source,
        parsed_session=parsed,
        windows=prediction_windows,
        coverage_seconds=cumulative_coverage,
        coverage_ratio=coverage_ratio(cumulative_coverage, target_zone_seconds),
        target_zone_seconds=target_zone_seconds,
        dr_method=dr_method,
    )


def analysis_report(analysis: SessionAnalysis) -> dict[str, object]:
    return {
        "input_file": str(analysis.source_path),
        "calibration_source": analysis.calibration_source,
        "dr_method": analysis.dr_method,
        "duration_seconds": round(analysis.parsed_session.duration_s, 3),
        "parsed_rows": analysis.parsed_session.metadata.parsed_rows,
        "skipped_rows": analysis.parsed_session.metadata.skipped_rows,
        "target_zone_seconds": analysis.target_zone_seconds,
        "zones": {
            label: {
                "display_name": DISPLAY_NAMES[label],
                "coverage_seconds": round(analysis.coverage_seconds[label], 3),
                "coverage_ratio": round(analysis.coverage_ratio[label], 4),
                "coverage_percent": round(analysis.coverage_ratio[label] * 100.0, 1),
            }
            for label in SURFACE_LABELS
        },
        "window_count": len(analysis.windows),
    }
