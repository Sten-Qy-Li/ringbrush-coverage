# Developer's README

This document is a map of the repository for engineers and developers who want to read, extend, or repair the codebase **without** relying on an AI coding agent to crawl it for them. The user-facing [README.md](README.md) explains *what* the project does and *how to run it*. This file explains *where each piece lives* and *what to change* when you want to evolve it.

## 1. Project at a glance

The pipeline turns a 7-column smart-ring sensor log (`t_ms, roll, pitch, yaw, ax, ay, az`) into:

1. A per-zone tooth-coverage summary (JSON).
2. A stylized MP4 visualization (mouth diagram + brush cursor + coverage bars).

End-to-end data flow inside the Python package:

```
.txt sensor log
     │
     ▼
core.parse_sensor_log  ───────────────►  ParsedSession (list[SensorSample])
     │
     ▼
core.iter_windows                       ~1 s sliding windows
     │
     ├──► core.feature_vector           15-D per-window features + activity
     │       │
     │       ▼
     │   core.CalibrationModel.classify zone probabilities (5 + idle)
     │
     ├──► dead reckoning
     │       • core.estimate_dead_reckoning              ("heuristic")
     │       • core.estimate_dead_reckoning_aeolus       ("aeolus")
     │       • video_anchor.compute_video_dr_per_window  ("video-anchored")
     │
     ▼
core.analyze_session                    cursor + motion gate + coverage accum.
     │
     ├──► core.analysis_report          → JSON
     └──► render.render_mp4             → MP4
```

The CLI ([ringbrush_coverage/cli.py](ringbrush_coverage/cli.py)) wires those steps together. Everything under [tools/](tools/) is an *offline* helper — calibration, comparison plots, report assembly — that builds on top of `ringbrush_coverage` but is not imported at runtime by the CLI.

## 2. Repository layout

```
ringbrush-coverage/
├── ringbrush_coverage/        ← installable Python package (the CLI)
│   ├── __init__.py
│   ├── __main__.py            ← enables `python -m ringbrush_coverage`
│   ├── cli.py                 ← argparse CLI, entry point of `ringbrush-coverage`
│   ├── core.py                ← parsing, calibration, DR, coverage logic
│   ├── render.py              ← MP4 frame rendering (PIL + imageio-ffmpeg)
│   └── video_anchor.py        ← per-window Δwrist from a synced MediaPipe CSV
│
├── tools/                     ← offline scripts (NOT installed; run with `python tools/<name>.py`)
│   ├── extract_video_motion.py
│   ├── sync_video_imu.py
│   ├── calibrate_dead_reckoning.py
│   ├── calibrate_dr_from_video.py
│   ├── compare_dead_reckoning.py
│   ├── compare_dr_vs_video.py
│   ├── compare_cursor_vs_video.py
│   ├── oversample_log.py
│   ├── compare_oversampling.py
│   └── build_report.py
│
├── firmware/                  ← M5StickC Plus + BNO055 Arduino sketch
│   └── bno055_udp_streamer/
│       └── bno055_udp_streamer.ino
│
├── recordings/                ← bundled sample sessions + labeled calibration logs
├── models/hand_landmarker.task← MediaPipe HandLandmarker model used by extract_video_motion.py
├── outputs/                   ← frozen artefacts from past runs (cited by the report)
├── report_assets/             ← JSON/PNG inputs used by tools/build_report.py
├── docs/                      ← LaTeX source for the dead-reckoning comparison appendix
├── tests/test_core.py         ← pytest suite for the core pipeline
├── pyproject.toml             ← package metadata, deps, console-script entry
├── README.md                  ← User's README
└── DEVELOPERS.md              ← (this file)
```

## 3. Functionality map

The tables below answer: *"feature X lives where, and if I want to change its behavior, what do I edit?"*

### 3.1 Runtime pipeline (the `ringbrush-coverage` CLI)

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| CLI surface (flags, defaults, `--dr-method` choices) | [ringbrush_coverage/cli.py](ringbrush_coverage/cli.py) — `build_parser()` | Add or rename `parser.add_argument(...)` calls. Wire new args into `analyze_session(...)` / `render_mp4(...)`. |
| `python -m ringbrush_coverage` entry | [ringbrush_coverage/__main__.py](ringbrush_coverage/__main__.py) | Rarely changes; delegates to `cli.main`. |
| `ringbrush-coverage` console script | [pyproject.toml](pyproject.toml) `[project.scripts]` | Edit `project.scripts` and reinstall (`pip install -e .`). |
| Parsing the 7-column `.txt` log (tolerates headers, junk lines, non-monotonic `t_ms`, >1 s gaps) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `parse_sensor_log()` | Change column expectations, validation rules, or `SensorSample` schema. |
| Zone labels & their anchor points on the mouth diagram | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `SURFACE_LABELS`, `ALL_LABELS`, `DISPLAY_NAMES`, `ZONE_ANCHORS` | Add/rename zones here, then update polygons in `render.py` (see §3.4) and calibration discovery patterns. |
| 15-D feature vector + activity scalar | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `FEATURE_NAMES`, `feature_vector()` | Add/remove features here. If you change the feature length you must also retrain calibration prototypes. |
| Window construction (size, step, edge handling) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `iter_windows()` (defaults `window_size=80`, `window_step=20` set in `analyze_session`) | Change defaults in `analyze_session` and/or the CLI `--window-size` / `--window-step` flags. |
| Region classifier (prototype-distance + softmax + idle bias) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `CalibrationModel.classify()` | Tweak the idle bias terms, softmax temperature `1.35`, or activity gates inside `classify`. |
| Calibration discovery from `*-only.txt` filenames | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `DISCOVERY_PATTERNS`, `discover_labeled_sessions()`, `build_calibration_from_directory()`, `choose_calibration()` | Edit `DISCOVERY_PATTERNS` to recognise new filename conventions. |
| Bundled fallback calibration (when no `--calibration-dir`) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `DEFAULT_CALIBRATION` dict | Replace with new mean/std vectors. Use `tools/calibrate_dead_reckoning.py` workflows to regenerate. |
| Heuristic dead reckoning (windowed) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `HEURISTIC_DR_DEFAULTS`, `estimate_dead_reckoning()` | Change defaults here. Per-run overrides come from `--heuristic-params <json>`. |
| Heuristic dead reckoning (full-session trajectory) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `trajectory_heuristic()` | Used by the comparison tools, not the live CLI. |
| AEOLUS dead reckoning (Radeta 2023 port) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `AEOLUS_*` constants, `_gravity_in_body_frame()`, `estimate_dead_reckoning_aeolus()`, `trajectory_aeolus()` | Tune `AEOLUS_ZVU_THRESHOLD`, `AEOLUS_ZVU_DECAY_XY`, `AEOLUS_ZVU_DECAY_Z`, or the Euler convention assumed in `_gravity_in_body_frame`. |
| AEOLUS → vis-space rescale (P90 target) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `AEOLUS_NORMALIZATION_TARGET_P90`, in-line rescaling inside `analyze_session()` | Change the target to harmonize with a different cursor scale. |
| Video-anchored DR (per-window Δwrist from synchronized MediaPipe CSV) | [ringbrush_coverage/video_anchor.py](ringbrush_coverage/video_anchor.py) — `compute_video_dr_per_window()` (plus `MOUTH_X_MIN/MAX` constants) | Add new landmark choices, change the bbox mapping, or alter the coverage-fallback rules. |
| DR-method dispatch inside `analyze_session` | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `DR_METHODS`, `_select_dr_function()`, the `dr_method` branches in `analyze_session()` | Add a new method by extending `DR_METHODS`, adding a branch in `_select_dr_function`, and (if it needs special handling) a branch in the rescale block. |
| Cursor positioning & smoothing (zone-anchor blend + DR nudge + follow damping) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — inside `analyze_session()`; helper `weighted_anchor()` | Edit the `target_x`/`target_y` clamps, `follow` factor, or the video-target override branch. |
| Probability smoothing across windows | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `smooth_probabilities()` | Change the activity-dependent blend factor and post-blend idle suppression. |
| Motion gate (vetoes coverage accumulation during demo-style wild motion) | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `MOTION_GATE_*` constants and gate logic inside `analyze_session()` | Adjust window length, midpoints, and scales. Two orthogonal signals (sustained |dr| and sustained accel_std) are combined with `min()`. |
| Coverage seconds → 0–100% conversion | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `coverage_ratio()` (parametrized by `target_zone_seconds`) | Change `target_zone_seconds` default in `analyze_session` or via `--target-zone-seconds`. |
| Per-window result records | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `WindowPrediction`, `SessionAnalysis` dataclasses | Add fields here, then propagate to `analysis_report()` and the renderer. |
| JSON summary report | [ringbrush_coverage/core.py](ringbrush_coverage/core.py) — `analysis_report()` and CLI text output `_print_report()` in `cli.py` | Add new keys in `analysis_report` and surface them in `_print_report`. |

### 3.2 MP4 rendering

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Mouth polygon shapes | [ringbrush_coverage/render.py](ringbrush_coverage/render.py) — `ZONE_POLYGONS` | Adjust per-zone shape; keep keys consistent with `SURFACE_LABELS`. |
| Frame composition (background, polygons, cursor, trail, bars, legend) | [ringbrush_coverage/render.py](ringbrush_coverage/render.py) — `render_mp4()` and its helpers (`_render_frame`, `_draw_zone_bars`, `_draw_cursor`, `_gradient_background`, etc.) | All visual changes live here. |
| Font selection (Windows-first, DejaVu fallback) | [ringbrush_coverage/render.py](ringbrush_coverage/render.py) — `_load_font()` | Add alternative font paths for cross-platform builds. |
| Video encoding (ffmpeg pipe via imageio-ffmpeg) | [ringbrush_coverage/render.py](ringbrush_coverage/render.py) — bottom of `render_mp4()` | Change codec args, pixel format, or output container. |

### 3.3 Video ground-truth pipeline (optional install: `pip install -e .[video]`)

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Per-frame hand landmarks → `format-3` CSV | [tools/extract_video_motion.py](tools/extract_video_motion.py); uses [models/hand_landmarker.task](models/hand_landmarker.task) | Edit the MediaPipe model path, landmark selection, or output columns. |
| IMU↔video time-offset recovery via motion-energy cross-correlation | [tools/sync_video_imu.py](tools/sync_video_imu.py) | Edit the motion-energy definition or the offset search range. |
| `format-3` CSV → per-window Δwrist consumed by the CLI | [ringbrush_coverage/video_anchor.py](ringbrush_coverage/video_anchor.py) — `compute_video_dr_per_window()` | Change the coverage-detection logic or the bbox mapping. |

### 3.4 Calibration & tuning tools

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Recompute calibration prototypes from labeled `*-only.txt` recordings | Done automatically by the CLI when `--calibration-dir` is passed; the underlying code is `build_calibration_from_directory()` in [ringbrush_coverage/core.py](ringbrush_coverage/core.py) | Edit `DEFAULT_CALIBRATION` to bake in new prototypes, or re-run with `--calibration-dir`. |
| Re-tune the four heuristic DR constants using labeled motion logs (up-down / left-right / inside-outside) | [tools/calibrate_dead_reckoning.py](tools/calibrate_dead_reckoning.py) | Edit the grid-search ranges, scoring function, or input log expectations. |
| Re-tune the heuristic DR constants using video-derived ground truth | [tools/calibrate_dr_from_video.py](tools/calibrate_dr_from_video.py) | Same kind of grid search but graded against `video_anchor` ground truth. |

### 3.5 Analysis & comparison tools

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Side-by-side heuristic vs. AEOLUS DR (PNG + JSON stats + animated MP4) | [tools/compare_dead_reckoning.py](tools/compare_dead_reckoning.py) | Add new DR methods or new plot types. |
| Quantify DR error vs. video ground truth | [tools/compare_dr_vs_video.py](tools/compare_dr_vs_video.py) | Add new methods or new error metrics. |
| Compare *rendered cursor trajectory* (post-blend) vs. video | [tools/compare_cursor_vs_video.py](tools/compare_cursor_vs_video.py) | Operates on `WindowPrediction.cursor`, not raw DR — useful when tuning the cursor blend in §3.1. |
| Linear-interpolation oversampling of an IMU log | [tools/oversample_log.py](tools/oversample_log.py) | Change the interpolation rule or output column order. |
| Quantify oversampling's effect on DR accuracy | [tools/compare_oversampling.py](tools/compare_oversampling.py) | Edit which DR methods or metrics are compared. |

### 3.6 Report assembly

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Final video report (title cards, methodology panels, captions, etc.) | [tools/build_report.py](tools/build_report.py) consuming JSON/PNG under [report_assets/](report_assets/) and pre-rendered MP4s | Edit panel layout, overlays, or input lists in `build_report.py`. Add new assets under `report_assets/`. |
| LaTeX appendix on DR comparison | [docs/dr_comparison.tex](docs/dr_comparison.tex) (`docs/dr_comparison.pdf` is the compiled artifact) | Edit the `.tex` source; recompile with your TeX toolchain. |

### 3.7 Firmware (sensor → laptop link)

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| M5StickC Plus + BNO055 → CSV-over-UDP streamer | [firmware/bno055_udp_streamer/bno055_udp_streamer.ino](firmware/bno055_udp_streamer/bno055_udp_streamer.ino) | Edit hotspot SSID/password, target laptop IP, UDP port, I²C pins, sample rate (`delay(10)` ≈ 100 Hz), and on-screen battery readout. |

### 3.8 Tests

| Functionality | Where it comes from | Where to modify |
|---|---|---|
| Unit + integration tests covering parsing, windowing, classification, DR, motion gate, and coverage accumulation | [tests/test_core.py](tests/test_core.py) | Add new tests alongside the existing ones. Run with `pytest`. |

## 4. How to extend (common cases)

### Add a new mouth zone (e.g. "tongue")
1. Add the label to `SURFACE_LABELS` (or `ALL_LABELS`) and `DISPLAY_NAMES` in [ringbrush_coverage/core.py](ringbrush_coverage/core.py).
2. Add an entry to `ZONE_ANCHORS` (centroid for the cursor) and `DISCOVERY_PATTERNS` (filename marker for calibration).
3. Add the polygon to `ZONE_POLYGONS` in [ringbrush_coverage/render.py](ringbrush_coverage/render.py).
4. Re-record a `*-tongue-only.txt` log and re-run with `--calibration-dir`, or bake new mean/std into `DEFAULT_CALIBRATION`.
5. Add a test in [tests/test_core.py](tests/test_core.py).

### Add a new dead-reckoning method
1. Implement the windowed estimator in [ringbrush_coverage/core.py](ringbrush_coverage/core.py) (and optionally a full-session `trajectory_*` companion).
2. Add the method name to `DR_METHODS` and dispatch it in `_select_dr_function()`.
3. If the method emits an unusual scale (like AEOLUS), add a rescale branch in `analyze_session()`.
4. Expose it via the `--dr-method` choices (already auto-populated from `DR_METHODS`).
5. Hook it into the comparison tools under [tools/](tools/) if you want plots.

### Change the motion gate (e.g. demo-vs-real brushing threshold)
- All gate parameters live near the top of [ringbrush_coverage/core.py](ringbrush_coverage/core.py) as `MOTION_GATE_*` constants. The gate combines two sigmoids (`sustained_dr`, `sustained_accel`) via `min()` inside `analyze_session()`.

### Change MP4 visual style
- Everything visual is in [ringbrush_coverage/render.py](ringbrush_coverage/render.py). The frame composition pipeline lives inside `render_mp4()`; per-element helpers (`_draw_*`) live below it.

## 5. Development install & tests

```powershell
# Editable install + video extras
python -m pip install -e ".[video]"

# Run the test suite
pytest

# Lint / format — none configured; follow the existing style.
```

## 6. Dependencies & where they're declared

- Runtime deps: [pyproject.toml](pyproject.toml) → `[project] dependencies` (numpy, Pillow, imageio-ffmpeg).
- Optional video deps: [pyproject.toml](pyproject.toml) → `[project.optional-dependencies] video` (opencv-python, mediapipe, matplotlib, scipy).
- Console script `ringbrush-coverage` → [pyproject.toml](pyproject.toml) → `[project.scripts]`.

If you add a new third-party import to anything under `ringbrush_coverage/`, declare it in `[project] dependencies`. If you add it only under `tools/`, prefer adding it to the `video` optional group (or a new optional group) so the base install stays lean.
