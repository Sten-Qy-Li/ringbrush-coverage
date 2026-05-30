# Final Video Report — Build Notes

Companion notes for `report_assets/final_report.mp4`. Single-source
build script: `tools/build_report.py`. Re-run end-to-end with
`python tools/build_report.py` (use `--clean` to wipe intermediate
segments first).

## Timeline

`report_assets/final_report_timeline.json` carries every segment's
start time and duration. Top-level structure:

| Section | Time | Content |
|---|---|---|
| 01_title             | 0:00 → 0:05  | "Ringbrush Coverage" title card. |
| 02_bg_1 .. 02_bg_4   | 0:05 → 0:40  | Four plain-language background cards (prototype, data-log format, pipeline, output). |
| 03_method_heuristic  | 0:40 → 1:07  | 9 s explainer card + 18 s sped-up heuristic playback on the primary log. |
| 03_method_aeolus     | 1:07 → 1:34  | Same shape: 9 s + 18 s for AEOLUS. |
| 03_method_video      | 1:34 → 2:01  | Same shape: 9 s + 18 s for Video-based. |
| 04_results_title     | 2:01 → 2:06  | "Three methodologies, one session" title card. |
| 04_results_3up       | 2:06 → 3:21  | 75 s side-by-side playback of all three on the primary log @ 1.75x. |
| 05_os_intro_1/2      | 3:21 → 3:56  | What over-sampling is + hypothesis (Nyquist). |
| 06_os_result         | 3:56 → 4:31  | Numbers + plain-language conclusion. |
| 07_appendix_title    | 4:31 → 4:37  | Appendix banner. |
| 07_appx_primary      | 4:37 → 5:52  | All 3 methods on the primary log (2026-05-29). 75 s @ 1.75x. |
| 07_appx_old_full     | 5:52 → 6:42  | All 3 methods on the earlier full session (2026-03-28). 50 s @ 1.25x. Video-based panel shows the "not available" card. |
| 07_appx_updown       | 6:42 → 6:58  | All 3 methods on the up-down calibration log. 16 s @ 1x. |
| 07_appx_leftright    | 6:58 → 7:09  | All 3 methods on the left-right calibration log. 11 s @ 1x. |
| 08_outro             | 7:09 → 7:13  | Thank-you / repo link. |

Total duration: **7:13** (base report 4:31 + appendix 2:38 + outro 4 s).

## Which methodology MP4 lives in which section

All methodology MP4s are pre-rendered by the standard CLI and live under
`report_assets/methodology_videos/`. Side-by-side composites letterbox
each input into a 426×240 panel inside a 1280×720 frame:

| Section | Panel | Source MP4 |
|---|---|---|
| 03_method_heuristic_play | (single) | `primary_heuristic.mp4` |
| 03_method_aeolus_play    | (single) | `primary_aeolus.mp4` |
| 03_method_video_play     | (single) | `primary_video.mp4` |
| 04_results_3up           | left / mid / right | `primary_heuristic.mp4` / `primary_aeolus.mp4` / `primary_video.mp4` |
| 07_appx_primary          | left / mid / right | same three |
| 07_appx_old_full         | left / mid / "not available" | `old_full_heuristic.mp4` / `old_full_aeolus.mp4` / placeholder |
| 07_appx_updown           | left / mid / "not available" | `updown_heuristic.mp4` / `updown_aeolus.mp4` / placeholder |
| 07_appx_leftright        | left / mid / "not available" | `leftright_heuristic.mp4` / `leftright_aeolus.mp4` / placeholder |

## Accuracy comments — sources

| Log | Heuristic / AEOLUS captions | Video-based caption |
|---|---|---|
| Primary (2026-05-29) | Mean cursor-to-GT distance in mouth-normalized units, computed by `tools/compare_cursor_vs_video.py` and read from `outputs/2026-05-29_video-anchored/cursor_comparison.json`. Heuristic 0.31; AEOLUS 0.32; video-based 0.03 (~90 % reduction). | Same metric. |
| Earlier full (2026-03-28) | "No companion video, so no GT distance." | "Not available: no companion video for this log." |
| Up-down (2026-04-12) | Axis-dominance ratio `dr_y_P90 / dr_x_P90` computed from `estimate_dead_reckoning` per analysis window. Heuristic 2.30× (vertical dominates, as designed); AEOLUS 1.23× (no clear axis). | "Not available: no companion video for this log." |
| Left-right (2026-04-20) | Same metric, swapped axis: `dr_x_P90 / dr_y_P90`. Heuristic 3.05× (horizontal dominates, as designed); AEOLUS 0.57× (AEOLUS actually gives more vertical here). | "Not available: no companion video for this log." |

## Over-sampling experiment — quantitative result

Linear interpolation between consecutive samples (`tools/oversample_log.py`),
doubling sample count from 10117 to 20233 on the primary log. For a fair
comparison, the windowed analyzer was given `--window-size 160 --window-step 40`
on the oversampled log so each analysis window still covers ~1 s.

Cursor-to-GT in mouth units, primary log, against the synchronized
video wrist:

| Method | Original | Oversampled | Δ |
|---|---|---|---|
| Heuristic | 0.3095 | 0.4538 | **+46.6 %** worse |
| AEOLUS    | 0.3211 | 0.4412 | **+37.4 %** worse |

Logged to `report_assets/oversampling/comparison_matched_windows.json`.

**Plain-language explanation.** Two reasons the result lands where it does:

1. **Nyquist–Shannon.** A linearly interpolated midpoint is fully determined
   by its two neighbors: it carries no information that wasn't already in
   the original samples. Equivalently, in the frequency domain the
   interpolation has no spectral content above the original Nyquist
   frequency (~40 Hz), so the integrator's reachable signal content is
   unchanged. Over-sampling cannot help on a hypothesis-of-information
   basis.
2. **Sample-rate-coupled damping** (an implementation detail that the
   experiment incidentally exposed). The line `velocity *= damping`
   runs once per sample, not once per time unit. Doubling the sample
   rate compounds damping twice over the same one-second window, so
   velocities get crushed and the per-window DR magnitudes shrink. This
   is why the *measured* numbers come out worse, not just unchanged.

The honest conclusion: the experiment does not support the hypothesis.
If a future change wanted oversampling to be neutral rather than
actively harmful, the damping constant in `estimate_dead_reckoning`
would need to be re-expressed in per-time form (e.g. `damping = exp(-k * dt)`).

## Design decisions

* **Normalization of side-by-side panels.** Each input is scaled to fit a
  426×240 box preserving 16:9 aspect (so the mouth diagram stays circular)
  and then padded with black above and below to fill its 1280/3-wide
  column. The accuracy caption sits in the black space *below* the video
  so the source visualization is never covered.
* **Panel duration.** When a source MP4 is shorter than the composite
  duration, ffmpeg lets it end and the column shows black for the
  remainder. For the calibration logs this is intentional: appendix
  segments are sized to roughly match each log's natural duration at 1x
  (16 s for the 15 s up-down log, 11 s for the 10 s left-right log), so
  the black-tail is at most a fraction of a second.
* **Playback speeds.** The primary 130 s session is played at ~1.75x in
  the Results and Appendix segments so the full session fits in 75 s.
  The earlier 62 s session uses 1.25x for the same reason. Calibration
  logs play at 1x because they are short already.
* **Video-based on non-video logs.** Rather than running it as a silent
  fallback to heuristic (which would produce a panel identical to the
  heuristic one — visually misleading), the third column is replaced
  with a "Not available" card and the caption explicitly states why.
* **Font choice.** Segoe UI (and Segoe UI Bold for headings) when
  available, with Arial and DejaVu Sans as fallbacks for portability.

## Known caveats

* The methodology playback panels at 426×240 are small. Detail in the
  per-zone coverage bars on the right side of each panel is hard to
  read at this scale. The full-resolution single-panel snippets in the
  Methodologies section (Heuristic / AEOLUS / Video-based each played at
  1280×720 with light letterboxing) are the place to read those bars.
* No audio anywhere — the report is captions-only by design.
* The "Earlier full session" (2026-03-28) is played at 1.25x and runs
  out before the 50 s composite ends; the column briefly goes black at
  the very end of that segment.
* The video-based methodology has a real dependency on a synchronized
  companion video. For the three appendix logs that lack one, this is
  shown as a placeholder rather than a fake "result".

## Reproducing the build

```powershell
# 1. Render the methodology MP4s (idempotent — overwrites if re-run)
python -m ringbrush_coverage "<primary log>" --output report_assets/methodology_videos/primary_heuristic.mp4
python -m ringbrush_coverage "<primary log>" --dr-method aeolus --output report_assets/methodology_videos/primary_aeolus.mp4
python -m ringbrush_coverage "<primary log>" --dr-method video-anchored `
  --video-sync-csv outputs/2026-05-29_video-sync/synchronized_video_on_imu_time.csv `
  --output report_assets/methodology_videos/primary_video.mp4
# (and so on for the other three logs)

# 2. Oversampling experiment
python tools/oversample_log.py "<primary log>" report_assets/oversampling/primary_oversampled.txt
python tools/compare_oversampling.py "<primary log>" report_assets/oversampling/primary_oversampled.txt `
  outputs/2026-05-29_video-sync/synchronized_video_on_imu_time.csv `
  --output report_assets/oversampling/comparison.json

# 3. Assemble the final report
python tools/build_report.py --clean
# -> report_assets/final_report.mp4
```
