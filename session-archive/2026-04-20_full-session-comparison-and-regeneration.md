# Session — 2026-04-20: Full-Session Comparison and MP4 Regeneration

## Summary

Following the recalibration session documented in
[`2026-04-20_dead-reckoning-recalibration.md`](2026-04-20_dead-reckoning-recalibration.md)
(commit `3e85611`), all three motion-type MP4s were regenerated from scratch
with the new parameters, and a new full-session MP4 was rendered so it could
be compared side-by-side against the retained old-model render
(`full-session-coverage_bb0a4b4.mp4`).

## Tasks performed

1. Regenerated `outputs/up-and-down-and-up-and-down-and.mp4`,
   `outputs/left-and-right-and-left-and-right-and.mp4`, and
   `outputs/inside-and-outside-and-inside-and-outside-and.mp4` from the new
   model (no parameter or code change; simple re-render for cleanliness).
2. Rendered a new full-session video for
   `C:/MSc-Computer-Science/Semester-2/pdss/2026-03-28_0946_full-session.txt`
   using the recalibrated algorithm, at the same resolution/fps
   (640x360 @ 2 fps) as the retained old-model file, so visual comparison
   is apples-to-apples.
3. Saved an analysis JSON alongside the new MP4 and diffed it against the
   retained old-model JSON to assess whether the recalibration improved
   measurable accuracy.

## Artefacts on disk

| Role | Path | Size | Notes |
|---|---|---|---|
| Old-model MP4 | `outputs/full-session-coverage_bb0a4b4.mp4` | ~38 MB | Retained for comparison; gitignored due to size. |
| Old-model JSON | `outputs/full-session-coverage_bb0a4b4.json` | 1.2 KB | Committed. |
| New-model MP4 | `outputs/full-session-coverage.mp4` | ~161 KB | New render, default settings. |
| New-model JSON | `outputs/full-session-coverage.json` | 1.2 KB | New render summary. |

The large size difference between the two MP4s (38 MB vs 161 KB, same
resolution, same duration, same fps) appears to stem from different
libx264 defaults at the time of the old render, not from any intentional
change in this session.

## Key finding

**Zone-coverage percentages are identical between old and new renders.**
This is not an error, it is structural: the coverage accumulator at
[`core.py:528-529`](../ringbrush_coverage/core.py#L528) is driven only by
`smoothed_probabilities`, which the classifier computes from the feature
vector. The dead-reckoning output `(pos_x, pos_y)` is used only to move
the on-screen cursor and is never fed back into classification.

| Zone | Old (bb0a4b4) | New | Delta |
|---|---|---|---|
| Outer left | 78.8% | 78.8% | 0.0 |
| Outer front | 92.4% | 92.4% | 0.0 |
| Outer right | 95.8% | 95.8% | 0.0 |
| Inner upper | 96.2% | 96.2% | 0.0 |
| Inner lower | 72.1% | 72.1% | 0.0 |

What *did* change is the cursor-trail fidelity inside each window, which is
not captured in the JSON. Evidence from signal P90s (already documented in
the recalibration note) shows that horizontal strokes now drive the X-axis
and vertical strokes now drive the Y-axis, instead of both getting lifted
by spurious yaw dominance.

## Implication for future work

If a future session wants the dead-reckoning output to affect the reported
coverage numbers (e.g. by using the cursor position to refine zone attribution
within a window), this would require a change in `analyze_session` -
specifically a coupling between `cursor` / `dead_reckoning` and the
`cumulative_coverage` update. That is an intentional architectural change
and would need explicit design, not a tuning pass.

## Repository state at end of session

- Commit at start of session: `3e85611` (recalibration + archive).
- Working-tree changes pending (not yet committed at time of writing):
  - `outputs/full-session-coverage.mp4` (new-model render)
  - `outputs/full-session-coverage.json` (new-model summary)
  - The three motion-type MP4s were re-rendered but their content is
    effectively the same as what was committed in `3e85611`, so git may
    show them as modified/identical depending on encoding determinism.
  - This session note.
