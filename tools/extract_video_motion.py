"""Extract hand-motion ground truth (`format-3`) from a brushing-session MP4.

Runs the MediaPipe Tasks HandLandmarker frame-by-frame on a front-facing video
and emits a CSV suitable for synchronization with the ring's IMU log. The IMU
is worn on a finger, so the hand wrist + MCP joints are the closest rigid-body
proxies for what the ring is rotating/translating with.

CSV columns:
    frame, t_ms,
    wrist_x, wrist_y,           # MediaPipe landmark 0 (normalized [0,1])
    index_mcp_x, index_mcp_y,   # landmark 5
    middle_mcp_x, middle_mcp_y, # landmark 9 (close to a ring on the middle finger)
    ring_mcp_x, ring_mcp_y,     # landmark 13 (close to a ring on the ring finger)
    hand_score, handedness

If no hand is detected on a frame, the landmark columns are blank (NaN).
Coordinates are normalized so x in [0, 1] and y in [0, 1] regardless of frame
size, with origin top-left (OpenCV convention). Requires the
`hand_landmarker.task` model under `models/` (downloadable from
https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision


LANDMARK_WRIST = 0
LANDMARK_INDEX_MCP = 5
LANDMARK_MIDDLE_MCP = 9
LANDMARK_RING_MCP = 13

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"


def _select_primary_hand(result) -> tuple[int, float, str] | None:
    """Pick the most-confident hand from the HandLandmarker result."""
    if not result.hand_landmarks:
        return None
    best_idx: int | None = None
    best_score: float = -1.0
    best_label: str = ""
    for i, hand_handedness in enumerate(result.handedness):
        classification = hand_handedness[0]
        score = float(classification.score)
        if score > best_score:
            best_score = score
            best_idx = i
            best_label = classification.category_name
    if best_idx is None:
        return None
    return best_idx, best_score, best_label


def extract(
    video_path: Path,
    output_csv: Path,
    *,
    model_path: Path = DEFAULT_MODEL,
    min_hand_detection_confidence: float = 0.4,
    min_hand_presence_confidence: float = 0.4,
    min_tracking_confidence: float = 0.4,
    progress_every: int = 300,
) -> dict:
    if not model_path.exists():
        raise FileNotFoundError(
            f"HandLandmarker model not found at {model_path}. "
            "Download it from "
            "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
        )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames_meta = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    options = mp_vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=min_hand_detection_confidence,
        min_hand_presence_confidence=min_hand_presence_confidence,
        min_tracking_confidence=min_tracking_confidence,
    )

    columns = [
        "frame", "t_ms",
        "wrist_x", "wrist_y",
        "index_mcp_x", "index_mcp_y",
        "middle_mcp_x", "middle_mcp_y",
        "ring_mcp_x", "ring_mcp_y",
        "hand_score", "handedness",
    ]

    detected = 0
    frame_index = 0
    started = time.time()
    last_valid_ts_ms = -1

    with mp_vision.HandLandmarker.create_from_options(options) as landmarker, \
            output_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)

        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break

            t_ms_float = cap.get(cv2.CAP_PROP_POS_MSEC)
            if not t_ms_float or t_ms_float <= 0:
                t_ms_float = (frame_index / fps * 1000.0) if fps > 0 else float(frame_index)

            # The HandLandmarker VIDEO mode requires strictly increasing
            # integer timestamps. CAP_PROP_POS_MSEC sometimes returns the same
            # ms for back-to-back frames at 30 fps; bump by 1 ms in that case.
            ts_int = int(round(t_ms_float))
            if ts_int <= last_valid_ts_ms:
                ts_int = last_valid_ts_ms + 1
            last_valid_ts_ms = ts_int

            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            result = landmarker.detect_for_video(mp_image, ts_int)

            selection = _select_primary_hand(result)
            if selection is None:
                writer.writerow([frame_index, f"{t_ms_float:.3f}"] + [""] * (len(columns) - 2))
            else:
                idx, score, label = selection
                lm = result.hand_landmarks[idx]
                wrist = lm[LANDMARK_WRIST]
                index_mcp = lm[LANDMARK_INDEX_MCP]
                middle_mcp = lm[LANDMARK_MIDDLE_MCP]
                ring_mcp = lm[LANDMARK_RING_MCP]
                writer.writerow(
                    [
                        frame_index,
                        f"{t_ms_float:.3f}",
                        f"{wrist.x:.6f}", f"{wrist.y:.6f}",
                        f"{index_mcp.x:.6f}", f"{index_mcp.y:.6f}",
                        f"{middle_mcp.x:.6f}", f"{middle_mcp.y:.6f}",
                        f"{ring_mcp.x:.6f}", f"{ring_mcp.y:.6f}",
                        f"{score:.4f}",
                        label,
                    ]
                )
                detected += 1

            frame_index += 1
            if progress_every and frame_index % progress_every == 0:
                elapsed = time.time() - started
                rate = frame_index / max(elapsed, 1e-6)
                pct = (frame_index / total_frames_meta * 100.0) if total_frames_meta else 0.0
                print(
                    f"  {frame_index:>6d} / {total_frames_meta or '?':>6} frames "
                    f"({pct:5.1f}%) @ {rate:5.1f} fps, detected={detected}",
                    flush=True,
                )

    cap.release()
    elapsed = time.time() - started

    return {
        "video_path": str(video_path),
        "csv_path": str(output_csv),
        "video_fps": fps,
        "video_frame_count_metadata": total_frames_meta,
        "video_width": width,
        "video_height": height,
        "processed_frames": frame_index,
        "detected_frames": detected,
        "detection_rate": detected / frame_index if frame_index else 0.0,
        "elapsed_seconds": elapsed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run MediaPipe HandLandmarker on a brushing-session MP4 and emit a per-frame CSV."
    )
    parser.add_argument("video", type=Path, help="Input .mp4")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output CSV (format-3)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="Path to hand_landmarker.task")
    parser.add_argument("--min-hand-detection-confidence", type=float, default=0.4)
    parser.add_argument("--min-hand-presence-confidence", type=float, default=0.4)
    parser.add_argument("--min-tracking-confidence", type=float, default=0.4)
    args = parser.parse_args(argv)

    print(f"Extracting from {args.video}")
    summary = extract(
        args.video.resolve(),
        args.output.resolve(),
        model_path=args.model.resolve(),
        min_hand_detection_confidence=args.min_hand_detection_confidence,
        min_hand_presence_confidence=args.min_hand_presence_confidence,
        min_tracking_confidence=args.min_tracking_confidence,
    )
    rate = summary["detection_rate"] * 100.0
    print(
        f"Done in {summary['elapsed_seconds']:.1f}s: "
        f"{summary['processed_frames']} frames, "
        f"hand detected in {summary['detected_frames']} ({rate:.1f}%)"
    )
    print(f"CSV: {summary['csv_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
