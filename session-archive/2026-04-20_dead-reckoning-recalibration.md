# Session — 2026-04-20: Heuristic Dead Reckoning Recalibration

## Summary

The four hardcoded parameters of `estimate_dead_reckoning` in
[`ringbrush_coverage/core.py`](../ringbrush_coverage/core.py) were retuned
using three labeled motion-type logs collected outside the repository. A
reusable grid-search calibration tool was added at
[`tools/calibrate_dead_reckoning.py`](../tools/calibrate_dead_reckoning.py),
and the three reference MP4s in `outputs/` were regenerated with the new
parameters. Older artefacts that were rendered with the previous parameter
set have been renamed to carry the suffix `_bb0a4b4` so the two generations
remain distinguishable on disk.

## SHAs bracketing this change

| Role | Short SHA | Branch | Notes |
|---|---|---|---|
| Old-model baseline | `bb0a4b4` | `dev` | Last commit before recalibration. All `outputs/*_bb0a4b4.*` files correspond to this state. |
| New-model commit | *(this commit)* | `dev` | Contains the recalibration, the grid-search tool, and the regenerated outputs. |

## Data sources used for the calibration

Three labeled motion logs located outside the repository:

| Motion type | Log file |
|---|---|
| up / down | `C:/MSc-Computer-Science/Semester-2/pdss/2026-04-12_2127_up-and-down-and-up-and-down-and.txt` |
| left / right | `C:/MSc-Computer-Science/Semester-2/pdss/2026-04-20_0958_left-and-right-and-left-and-right-and.txt` |
| inside / outside | `C:/MSc-Computer-Science/Semester-2/pdss/2026-04-20_1000_inside-and-outside-and-inside-and-outside-and.txt` |

## Key finding

The ring's gyroscope-derived **yaw and pitch deltas do not discriminate
brushing direction cleanly** on this dataset. The "up-and-down" log contained
**more** yaw change (85° P90 per window) than the "left-and-right" log (25°),
because the wrist pivots while making vertical strokes. Linear acceleration
(`ax`, `ay`) discriminates motion type much more reliably, so the
recalibration promoted the acceleration path to be the dominant signal and
effectively zeroed the pitch contribution.

## Parameter changes

| Parameter | Before | After | Rationale |
|---|---|---|---|
| velocity damping | `0.84` | `0.92` | Longer memory so repeated strokes integrate smoothly. |
| acceleration scale | `0.10` | `2.00` | 20x boost — makes the acceleration path the primary signal. |
| yaw contribution | `0.0012` | `0.0015` | Small refinement; yaw still contributes secondary correction. |
| pitch contribution | `0.0015` | `0.0000` | Pitch deltas were noise-like across all three motion types. |

### Verification (P90 of per-window `|pos_x|` / `|pos_y|`)

| Log | Before `dr_x` / `dr_y` | After `dr_x` / `dr_y` | Dominant axis |
|---|---|---|---|
| up-down | 0.102 / **0.016** | 0.145 / **0.333** | Y (correct; was X — wrong) |
| left-right | **0.030** / 0.017 | **0.360** / 0.118 | X (correct magnitude and axis) |
| inside-outside | 0.016 / 0.012 | 0.208 / 0.125 | both bounded |

## Files added, modified, renamed

**Modified**

- `ringbrush_coverage/core.py` — new constants in
  `estimate_dead_reckoning` at lines 348–353.

**Added**

- `tools/calibrate_dead_reckoning.py` — reproducible grid-search calibration.
  Re-run it if new labeled logs are collected and new parameters are wanted.
- `session-archive/README.md`
- `session-archive/2026-04-20_dead-reckoning-recalibration.md` (this file)
- `outputs/up-and-down-and-up-and-down-and.mp4` (new-model render)
- `outputs/left-and-right-and-left-and-right-and.mp4` (new-model render)
- `outputs/inside-and-outside-and-inside-and-outside-and.mp4` (new-model render)

**Renamed to `_bb0a4b4` (old-model artefacts)**

- `outputs/default-smoke.{json,mp4}` → `outputs/default-smoke_bb0a4b4.{json,mp4}`
- `outputs/full-session-coverage.{json,mp4}` → `outputs/full-session-coverage_bb0a4b4.{json,mp4}`
- `outputs/smoke-640x360.{json,mp4}` → `outputs/smoke-640x360_bb0a4b4.{json,mp4}`
- `outputs/smoke.json` → `outputs/smoke_bb0a4b4.json`
- `outputs/verification-small.{json,mp4}` → `outputs/verification-small_bb0a4b4.{json,mp4}`

## Files intentionally excluded from version control

These three old-model renders are large (21-38 MB each) and would bloat the
repository permanently. They remain on local disk but are listed in
`.gitignore`:

- `outputs/full-session-coverage_bb0a4b4.mp4` (~38 MB)
- `outputs/smoke-640x360_bb0a4b4.mp4` (~21 MB)
- `outputs/verification-small_bb0a4b4.mp4` (~38 MB)

Their accompanying JSON summaries **are** committed, so the coverage numbers
they encode are preserved in the repository history.

## How to reproduce

```bash
# Recompute parameters from scratch:
python tools/calibrate_dead_reckoning.py

# Regenerate any reference MP4:
python -m ringbrush_coverage <path-to-log.txt> -o outputs/<name>.mp4
```

## Follow-ups for future sessions

- The `inside-outside` motion produced a slightly elevated `dr_x` P90 (0.208)
  versus the 0.15 target. This is acceptable but could be revisited if more
  labeled inside/outside logs become available.
- The downstream `analyze_session` cursor blend still uses the original
  scaling (`0.38`), clamps (`±0.18` / `±0.16`), and follow-speed formula.
  These were not recalibrated in this session; they may benefit from their
  own tuning pass once the dead reckoning output is trusted.
