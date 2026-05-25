# ringbrush-coverage

**ringbrush-coverage** is a Python-based project for visualizing toothbrushing coverage from smart ring motion data. The app reads sensor logs in the format `t_ms, roll, pitch, yaw, ax, ay, az`, applies dead reckoning and motion analysis, and generates an MP4 video of a mouth outline. Areas that are brushed correctly are highlighted in green, with stronger color showing better coverage. This project is developed for the University of Tartu course *Pervasive Data Science Seminar*.

## What the app does

- Parses noisy smart-ring log files that may include boot messages, repeated headers, malformed lines, or truncated trailing rows.
- Learns region prototypes from labeled `*-only.txt` sessions such as `outer-front-only`, `inner-upper-only`, and `no-movement-idle`.
- Uses windowed motion features plus a damped dead-reckoning path estimate to infer where brushing is happening over time.
- Accumulates weighted coverage for each mouth region and renders an MP4 with a moving brush cursor, trail, and per-zone progress bars.
- Falls back to bundled default calibration values that were derived from the provided sample recordings.

## Project layout

```text
ringbrush_coverage/
  __main__.py
  cli.py
  core.py
  render.py
tests/
  test_core.py
```

## Installation

From the repository root:

```powershell
python -m pip install -e .
```

That installs the CLI entry point `ringbrush-coverage` and the MP4 dependency `imageio-ffmpeg`.

The default export settings render at `1280x720` and `30 FPS`. The layout is a fixed grid (header band, mouth visualization, sidebar with per-zone coverage bars, status pill, timeline) that scales to whatever `--width` and `--height` you pass. Lower `--fps` if you want a faster render, at the cost of smoothness.

## Usage

### Analyze a full brushing session and render MP4

From [ringbrush-coverage](C:/MSc-Computer-Science/Semester-2/pdss/ringbrush-coverage), using the labeled sample recordings in the parent `pdss` folder:

```powershell
python -m ringbrush_coverage "..\2026-03-28_0946_full-session.txt" `
  --calibration-dir ".." `
  --output ".\outputs\full-session-coverage.mp4" `
  --summary-json ".\outputs\full-session-coverage.json"
```

### Analyze without rendering video

```powershell
python -m ringbrush_coverage "..\2026-03-28_0946_full-session.txt" `
  --calibration-dir ".." `
  --report-only
```

### Use the installed console script

```powershell
ringbrush-coverage "..\2026-03-28_0946_full-session.txt" --calibration-dir ".."
```

### Choose a dead-reckoning method

The cursor's motion comes from a dead-reckoning pass over the accelerometer
stream. Two methods ship in the box:

- `heuristic` (default) — in-house damped integrator that subtracts each window's
  mean accelerometer reading as a gravity proxy and produces a small per-window
  nudge in normalized [0, 1] visualization space.
- `aeolus` — a faithful port of the Radeta-2023 AEOLUS pipeline (Earth-frame
  gravity removal from roll/pitch, Algorithm 1 ZVU drift reduction, and the
  heading-projected position update from equation 9). Returns metres, then is
  rescaled per-session to a comparable visualization magnitude.

```powershell
ringbrush-coverage "..\2026-03-28_0946_full-session.txt" --dr-method aeolus
```

### Compare both dead-reckoning methods side-by-side

`tools/compare_dead_reckoning.py` runs both methods on the same log and emits a
JSON stats summary, a side-by-side PNG, and an animated MP4:

```powershell
python tools\compare_dead_reckoning.py `
  "..\recordings\2026-03-28_0946_full-session.txt" `
  --output-dir .\outputs\2026-05-11_dr-comparison
```

Add `--skip-mp4` to get just the JSON + PNG (much faster on long sessions).

## Calibration data

If `--calibration-dir` is supplied, the app looks for these labeled file patterns in that directory:

- `*outer-front-only*.txt`
- `*outer-left-only*.txt`
- `*outer-right-only*.txt`
- `*inner-upper-only*.txt`
- `*inner-lower-only*.txt`
- `*no-movement-idle*.txt`

If every label is present, the region model is rebuilt from those sessions. Otherwise the app uses bundled defaults derived from the sample logs you provided.

## Output

The JSON summary includes:

- parsed row count and skipped noisy rows
- session duration
- calibration source
- weighted coverage seconds per mouth region
- coverage percentages per mouth region

The MP4 shows:

- a stylized mouth map
- green intensity increasing with accumulated coverage
- a dead-reckoned brush cursor and short motion trail
- live coverage bars for each toothbrushing region

## Notes and limitations

- This is a practical first version tuned to the sample logs in `C:\MSc-Computer-Science\Semester-2\pdss`.
- The dead reckoning is intentionally damped to control drift, so it should be read as a visual aid rather than a medically precise trajectory.
- Better accuracy will come from collecting more labeled region sessions and retraining with `--calibration-dir`.
