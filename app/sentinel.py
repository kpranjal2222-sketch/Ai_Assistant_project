"""
sentinel.py -- Browser-driven construction-safety frame processor.

Incorporates algorithms from:
1. prodbykosta/ppe-safety-detection-ai:
   - Persistent Worker Tracking (IoU/Centroid matching across frames).
   - Persistent Worker IDs (Worker #1, Worker #2...) to avoid duplicate counts.
2. sonubiswal/sentinelvision:
   - N-of-M Temporal Voting (Anti-flicker smoothing window).
   - Sustained-violation verification before raising alarms.
3. Auto-Evidence Snapshot Engine:
   - Saves forensic evidence captures into data/evidence/.
"""

import collections
from datetime import datetime
import io
import math
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple, Any
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from .hazard_engine import detect_multi_hazards

ROOT_DIR = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT_DIR / "data" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _bbox_iou(boxA: List[float], boxB: List[float]) -> float:
    """Compute Intersection-over-Union between two bounding boxes [x1, y1, x2, y2]."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    interArea = max(0.0, xB - xA) * max(0.0, yB - yA)
    boxAArea = max(0.0, boxA[2] - boxA[0]) * max(0.0, boxA[3] - boxA[1])
    boxBArea = max(0.0, boxB[2] - boxB[0]) * max(0.0, boxB[3] - boxB[1])

    denom = boxAArea + boxBArea - interArea
    return interArea / denom if denom > 0 else 0.0


class WorkerTracker:
    """
    Persistent Worker Tracker (Inspired by prodbykosta/ppe-safety-detection-ai).
    Tracks detected workers across video frames, assigning persistent IDs.
    """
    def __init__(self, max_disappeared: int = 25, iou_threshold: float = 0.3):
        self.next_id = 1
        self.tracks: Dict[int, Dict[str, Any]] = {}
        self.max_disappeared = max_disappeared
        self.iou_threshold = iou_threshold

    def update(self, detected_boxes: List[List[float]]) -> Dict[int, Dict[str, Any]]:
        """
        Update tracker with current frame person bounding boxes.
        Returns active tracks dictionary: {worker_id: {bbox, disappeared, history}}.
        """
        if not detected_boxes:
            # Increment disappeared count for all existing tracks
            to_delete = []
            for track_id in self.tracks:
                self.tracks[track_id]["disappeared"] += 1
                if self.tracks[track_id]["disappeared"] > self.max_disappeared:
                    to_delete.append(track_id)
            for tid in to_delete:
                del self.tracks[tid]
            return self.tracks

        if not self.tracks:
            # Initialize tracks for first detections
            for bbox in detected_boxes:
                self.tracks[self.next_id] = {
                    "id": self.next_id,
                    "bbox": bbox,
                    "disappeared": 0,
                    "first_seen": time.time(),
                }
                self.next_id += 1
            return self.tracks

        # Match existing tracks with current detections using IoU matrix
        track_ids = list(self.tracks.keys())
        prev_boxes = [self.tracks[tid]["bbox"] for tid in track_ids]

        # Compute IoU matrix
        iou_matrix = np.zeros((len(track_ids), len(detected_boxes)), dtype=np.float32)
        for i, p_box in enumerate(prev_boxes):
            for j, d_box in enumerate(detected_boxes):
                iou_matrix[i, j] = _bbox_iou(p_box, d_box)

        matched_tracks = set()
        matched_detections = set()

        if iou_matrix.size > 0:
            # Greedy bipartite matching on highest IoU
            while True:
                max_val = np.max(iou_matrix)
                if max_val < self.iou_threshold:
                    break
                row, col = np.unravel_index(np.argmax(iou_matrix), iou_matrix.shape)
                if row in matched_tracks or col in matched_detections:
                    iou_matrix[row, col] = 0.0
                    continue

                track_id = track_ids[row]
                self.tracks[track_id]["bbox"] = detected_boxes[col]
                self.tracks[track_id]["disappeared"] = 0
                matched_tracks.add(row)
                matched_detections.add(col)
                iou_matrix[row, :] = 0.0
                iou_matrix[:, col] = 0.0

        # Handle unmatched existing tracks
        for i, tid in enumerate(track_ids):
            if i not in matched_tracks:
                self.tracks[tid]["disappeared"] += 1

        # Delete expired tracks
        to_delete = [tid for tid, data in self.tracks.items() if data["disappeared"] > self.max_disappeared]
        for tid in to_delete:
            del self.tracks[tid]

        # Create new tracks for unmatched detections
        for j, bbox in enumerate(detected_boxes):
            if j not in matched_detections:
                self.tracks[self.next_id] = {
                    "id": self.next_id,
                    "bbox": bbox,
                    "disappeared": 0,
                    "first_seen": time.time(),
                }
                self.next_id += 1

        return self.tracks


class TemporalVotingFilter:
    """
    N-of-M Temporal Voting Filter (Inspired by sonubiswal/sentinelvision).
    Maintains a sliding window of recent inspection results per worker.
    Requires N violations out of the last M frames before triggering a confirmed alert.
    """
    def __init__(self, window_size: int = 5, required_violations: int = 3):
        self.window_size = window_size
        self.required_violations = required_violations
        self.worker_history: Dict[int, collections.deque] = {}
        self.confirmed_violations: Dict[int, bool] = {}

    def vote(self, worker_id: int, is_violation_in_frame: bool) -> bool:
        """
        Update rolling window and return True if violation is confirmed via temporal voting.
        """
        if worker_id not in self.worker_history:
            self.worker_history[worker_id] = collections.deque(maxlen=self.window_size)

        self.worker_history[worker_id].append(1 if is_violation_in_frame else 0)

        # Count violations in current window
        violation_count = sum(self.worker_history[worker_id])
        is_confirmed = violation_count >= self.required_violations
        self.confirmed_violations[worker_id] = is_confirmed
        return is_confirmed

    def cleanup(self, active_track_ids: List[int]):
        """Remove history for tracks that are no longer active."""
        active_set = set(active_track_ids)
        to_remove = [tid for tid in self.worker_history if tid not in active_set]
        for tid in to_remove:
            del self.worker_history[tid]
            if tid in self.confirmed_violations:
                del self.confirmed_violations[tid]


class SiteSentinel:
    """
    Browser-driven frame processor with worker tracking and temporal de-bouncing,
    and automatic evidence snapshot generation.
    """
    def __init__(self, sample_every: int = 1):
        self.tracker = WorkerTracker(max_disappeared=30, iou_threshold=0.3)
        self.temporal_filter = TemporalVotingFilter(window_size=5, required_violations=3)
        self.sample_every = sample_every
        self.frame_count = 0
        self.last_summary: Optional[Dict[str, Any]] = None
        self.last_snapshot_time: Dict[str, float] = {}
        self.snapshot_cooldown_sec = 6.0  # Avoid saving duplicate snapshots within 6 seconds

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        conf: float = 0.35,
        save_evidence: bool = True
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Analyze a single video frame.

        Parameters:
        -----------
        frame_bgr : np.ndarray
            OpenCV BGR video frame.
        conf : float
            Confidence threshold for inference.
        save_evidence : bool
            Whether to auto-capture forensic evidence snapshots on confirmed violations.

        Returns:
        --------
        annotated_bgr : np.ndarray
            Annotated frame with tracking IDs and HUD.
        telemetry : dict
            Real-time diagnostics and active hazard events.
        """
        self.frame_count += 1
        t_start = time.time()

        # Convert to RGB PIL for hazard engine
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)

        # Execute Multi-Hazard AI Inspection (Phase 1 Engine)
        annotated_pil, hazard_summary = detect_multi_hazards(
            pil_img,
            ppe_conf=conf,
            fire_conf=0.30,
            proximity_threshold=180.0,
            include_machinery=True,
        )

        annotated_bgr = cv2.cvtColor(np.array(annotated_pil), cv2.COLOR_RGB2BGR)
        h, w = frame_bgr.shape[:2]

        # Extract person/worker bounding boxes for tracking
        person_boxes = []
        for det in hazard_summary["ppe"]["detections"]:
            if det["class"] in ("Person", "Hardhat", "NO-Hardhat", "Safety Vest", "NO-Safety Vest"):
                person_boxes.append(det["bbox"])

        # Update persistent worker tracker (prodbykosta)
        active_tracks = self.tracker.update(person_boxes)

        # Apply N-of-M temporal de-bouncing (sonubiswal)
        siren_trigger = False
        evidence_saved = None
        active_tracked_workers = []

        now = time.time()
        for track_id, track_data in active_tracks.items():
            tx1, ty1, tx2, ty2 = track_data["bbox"]

            # Associate the strongest overlapping PPE observation with this track.
            related_detections = [
                det
                for det in hazard_summary["ppe"]["detections"]
                if _bbox_iou([tx1, ty1, tx2, ty2], det["bbox"]) > 0.15
            ]
            worker_has_violation = any(det["is_violation"] for det in related_detections)
            observation = max(
                related_detections,
                key=lambda det: (bool(det["is_violation"]), float(det["conf"])),
                default=None,
            )

            # Apply temporal voting filter
            is_confirmed_violation = self.temporal_filter.vote(track_id, worker_has_violation)

            # Draw Persistent Worker ID badge
            status_color = (0, 0, 230) if is_confirmed_violation else (0, 200, 0)
            worker_tag = f"Worker #{track_id} {'[NON-COMPLIANT]' if is_confirmed_violation else '[OK]'}"
            cv2.putText(
                annotated_bgr,
                worker_tag,
                (tx1, max(20, ty1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                status_color,
                2,
                cv2.LINE_AA,
            )

            active_tracked_workers.append({
                "worker_id": track_id,
                "bbox": [tx1, ty1, tx2, ty2],
                "confirmed_violation": is_confirmed_violation,
                "observation_class": observation["class"] if observation else None,
                "confidence": observation["conf"] if observation else None,
            })

            if is_confirmed_violation:
                siren_trigger = True
                # Auto-Evidence Snapshot Engine
                hazard_key = f"worker_{track_id}_ppe"
                if save_evidence and (now - self.last_snapshot_time.get(hazard_key, 0) > self.snapshot_cooldown_sec):
                    self.last_snapshot_time[hazard_key] = now
                    snap_id = f"incident_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}.jpg"
                    snap_path = EVIDENCE_DIR / snap_id
                    cv2.imwrite(str(snap_path), annotated_bgr)
                    evidence_saved = {
                        "filename": snap_id,
                        "path": str(snap_path),
                        "type": "PPE_VIOLATION",
                        "worker_id": track_id,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }

        # Check Fire & Disaster Siren Trigger
        if hazard_summary["fire_smoke"]["tier"] in ("EMERGENCY", "WARNING") or hazard_summary["ppe"]["falls_detected"] > 0:
            siren_trigger = True
            fire_key = "fire_emergency"
            if save_evidence and (now - self.last_snapshot_time.get(fire_key, 0) > self.snapshot_cooldown_sec):
                self.last_snapshot_time[fire_key] = now
                snap_id = f"fire_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}.jpg"
                snap_path = EVIDENCE_DIR / snap_id
                cv2.imwrite(str(snap_path), annotated_bgr)
                evidence_saved = {
                    "filename": snap_id,
                    "path": str(snap_path),
                    "type": "FIRE_EMERGENCY" if hazard_summary["fire_smoke"]["tier"] == "EMERGENCY" else "SMOKE_ALERT",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

        # Heavy Machinery Proximity Siren Trigger
        if len(hazard_summary["machinery_hazards"]) > 0:
            siren_trigger = True

        self.temporal_filter.cleanup(list(active_tracks.keys()))
        fps = round(1.0 / max(0.001, (time.time() - t_start)), 1)

        # Draw Mission-Control HUD Telemetry Bar at bottom of screen
        hud_bar_h = 28
        cv2.rectangle(annotated_bgr, (0, h - hud_bar_h), (w, h), (15, 20, 28), -1)
        hud_text = (
            f"SITESENTINEL // FPS: {fps} | WORKERS: {len(active_tracks)} | "
            f"HAZARDS: {hazard_summary['total_violations']} | "
            f"SIREN: {'ACTIVE' if siren_trigger else 'ARMED'} | "
            f"SEVERITY: {hazard_summary['overall_severity']}"
        )
        cv2.putText(
            annotated_bgr,
            hud_text,
            (10, h - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (56, 189, 248),  # Cyan telemetry
            1,
            cv2.LINE_AA,
        )

        telemetry = {
            "fps": fps,
            "frame_idx": self.frame_count,
            "tracked_workers": active_tracked_workers,
            "siren_trigger": siren_trigger,
            "hazard_summary": hazard_summary,
            "evidence_saved": evidence_saved,
        }

        return annotated_bgr, telemetry
