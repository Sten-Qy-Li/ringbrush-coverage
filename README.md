# ringbrush-coverage

Submitted to the Institute of Computer Science in fulfillment of the requirements for the courses:
 * Pervasive Data Science Seminar (3 ECTS, LTAT.06.010); and
 * Distributed Systems Project (3 ECTS, LTAT.00.010)

 at the University of Tartu, in the spring of 2026.

https://github.com/user-attachments/assets/13e0ac8a-ce0f-4071-a721-43946dbc97ab

---

Turn a smart-ring sensor log into an MP4 that shows where someone brushed their teeth. The video renders a stylized mouth, a dead-reckoned brush cursor with a short motion trail, and per-zone coverage bars that fill as each surface is brushed.

## Install

From the repository root:

```powershell
python -m pip install -e .
```

This installs the `ringbrush-coverage` CLI and its `imageio-ffmpeg` dependency.

## Generate a video from a sensor log

### 1. Have a sensor log ready

A log is plain-text CSV with seven columns: `t_ms, roll, pitch, yaw, ax, ay, az`. Header rows, boot messages, malformed lines, and non-monotonic timestamps are tolerated and skipped. Angles are degrees, accelerations are m/s², and the expected ring sample rate is ~80 Hz.

### 2. (Optional) Point at labeled calibration logs

If you have one-region-at-a-time recordings, pass `--calibration-dir <folder>`. The region classifier is rebuilt when these six filename patterns are all present in that folder:

```
*outer-front-only*.txt   *inner-upper-only*.txt
*outer-left-only*.txt    *inner-lower-only*.txt
*outer-right-only*.txt   *no-movement-idle*.txt
```

If any are missing, the app falls back to bundled defaults derived from the original sample recordings.

### 3. Run the CLI

```powershell
ringbrush-coverage "C:/path/to/session.txt" `
  --calibration-dir "C:/path/to/labeled-logs" `
  --output ".\outputs\session.mp4" `
  --summary-json ".\outputs\session.json"
```

Or equivalently via the module entry point:

```powershell
python -m ringbrush_coverage "C:/path/to/session.txt" `
  --output ".\outputs\session.mp4"
```

Default render: **1280x720 at 30 FPS**. Override with `--width`, `--height`, `--fps`. Lower `--fps` for faster renders at the cost of smoothness.

### 4. Read the outputs

- **`session.mp4`** — mouth map with green intensity rising with accumulated coverage, a brush cursor, a short motion trail, and live per-zone coverage bars.
- **`session.json`** — parsed-row and skipped-row counts, session duration, calibration source, weighted coverage seconds and 0–100% coverage per zone.

## Other useful flags

- `--report-only` — skip the MP4 and just write the JSON + print the per-zone summary. Much faster for sanity checks.
- `--dr-method aeolus` — replace the default in-house heuristic dead reckoning with a port of the Radeta-2023 AEOLUS pipeline (Earth-frame gravity removal from roll/pitch, Algorithm 1 ZVU drift reduction, heading-projected position update). Returns metres internally and is rescaled per-session to the same visualization range as the heuristic.
- `--dr-method video-anchored --video-sync-csv <path>` — use a synchronized front-camera recording as ground truth. See "Video-anchored dead reckoning" below.
- `--heuristic-params <path>` — load JSON overrides for the heuristic DR constants (produced by `tools/calibrate_dr_from_video.py`).

## Video-anchored dead reckoning

If you also recorded a front-camera video of the session, the wrist position from each frame is a much stronger ground truth than IMU integration alone. The pipeline is:

```powershell
# Install the optional video deps
python -m pip install -e ".[video]"

# 1. Extract per-frame hand landmarks ("format-3" CSV)
python tools\extract_video_motion.py "C:/path/to/session.mp4" -o "C:/path/to/session_format-3.csv"

# 2. Cross-correlate with the IMU log to recover the time offset and produce a synchronized CSV
python tools\sync_video_imu.py "C:/path/to/session.txt" "C:/path/to/session_format-3.csv" --output-dir .\outputs\session-sync

# 3. Render with the new DR method
ringbrush-coverage "C:/path/to/session.txt" `
  --dr-method video-anchored `
  --video-sync-csv .\outputs\session-sync\synchronized_video_on_imu_time.csv `
  --output .\outputs\session-video-anchored.mp4
```

For windows where the video has hand-landmark coverage, the cursor is driven directly by the per-session normalized wrist position; for the rest (start/end gaps, MediaPipe miss bursts), the existing heuristic DR fills in. On the bundled 2026-05-29 session this drops mean cursor-to-GT distance from 0.31 (heuristic) and 0.32 (AEOLUS) to 0.03 mouth units — a 90% reduction.

## Compare both dead-reckoning methods on one log

```powershell
python tools\compare_dead_reckoning.py "C:/path/to/session.txt" `
  --output-dir .\outputs\dr-comparison
```

Emits a side-by-side PNG, a JSON stats summary, and an animated MP4. Add `--skip-mp4` for just the PNG + JSON.

## How the coverage map is built

For each ~1-second window the app:

1. Extracts orientation, acceleration, and angular-speed features.
2. Classifies the window into a region (`outer-front`, `outer-left`, `outer-right`, `inner-upper`, `inner-lower`, or `idle`).
3. Dead-reckons a per-window displacement and nudges the cursor.
4. **Gates coverage accumulation** by the median per-window displacement and acceleration std over the last few windows. Sustained out-of-mouth motion (e.g. demonstration sweeps much wider than a real mouth) still moves the cursor visually but stops adding to the coverage bars. This prevents false-positive coverage when motion is too wild to be real brushing.
5. Adds the gated, weighted coverage seconds to the dominant zone(s) and converts the totals to 0–100% bars.

## Notes and limitations

- Defaults are tuned to the sample recordings under `C:\MSc-Computer-Science\Semester-2\pdss\recordings`. Different rings or unusual brushing styles likely need fresh calibration.
- Dead reckoning is damped to keep cursor drift bounded — read it as a visual cue, not a medically precise trajectory.
- Retrain the heuristic dead-reckoning constants with `python tools\calibrate_dead_reckoning.py` after collecting new labeled left-right / up-down / inside-outside motion logs.
