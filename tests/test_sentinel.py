import unittest
import numpy as np
from PIL import Image
from unittest.mock import patch

from app.sentinel import (
    WorkerTracker,
    TemporalVotingFilter,
    SiteSentinel,
    _bbox_iou,
)


class TestSentinel(unittest.TestCase):
    def test_bbox_iou(self):
        boxA = [0, 0, 10, 10]
        boxB = [0, 0, 10, 10]
        self.assertAlmostEqual(_bbox_iou(boxA, boxB), 1.0)

        boxC = [10, 10, 20, 20]
        self.assertAlmostEqual(_bbox_iou(boxA, boxC), 0.0)

        boxD = [5, 0, 15, 10]
        # Inter: 5 * 10 = 50. Union: 100 + 100 - 50 = 150. IoU: 50 / 150 = 0.3333...
        self.assertAlmostEqual(_bbox_iou(boxA, boxD), 1.0 / 3.0, places=4)

    def test_worker_tracker_persistence(self):
        tracker = WorkerTracker(max_disappeared=5, iou_threshold=0.3)

        # Frame 1: Worker at [10, 10, 50, 100]
        tracks = tracker.update([[10, 10, 50, 100]])
        self.assertEqual(len(tracks), 1)
        worker_id = list(tracks.keys())[0]
        self.assertEqual(worker_id, 1)

        # Frame 2: Worker slightly shifted to [12, 11, 52, 101]
        tracks = tracker.update([[12, 11, 52, 101]])
        self.assertEqual(len(tracks), 1)
        self.assertIn(worker_id, tracks, "Expected persistent worker ID to match across frames")

        # Frame 3: Worker disappeared for 10 frames -> track should be deleted
        for _ in range(10):
            tracker.update([])
        self.assertEqual(len(tracker.tracks), 0, "Expected track to be cleaned up after max_disappeared")

    def test_temporal_voting_anti_flicker(self):
        # Window size 5, requires 3 violations to confirm
        filt = TemporalVotingFilter(window_size=5, required_violations=3)

        # 1-frame transient flicker -> should NOT trigger confirmed violation
        res1 = filt.vote(worker_id=1, is_violation_in_frame=True)
        self.assertFalse(res1, "A single-frame transient blip should be filtered out by temporal voting")

        # Frame 2: compliant
        res2 = filt.vote(worker_id=1, is_violation_in_frame=False)
        self.assertFalse(res2)

        # Frame 3 & 4: sustained violations -> now 3 violations out of last 4 frames
        filt.vote(worker_id=1, is_violation_in_frame=True)
        res4 = filt.vote(worker_id=1, is_violation_in_frame=True)
        self.assertTrue(res4, "Sustained violation (3 of last 4 frames) should trigger confirmed violation")

    def test_sentinel_process_frame(self):
        sentinel = SiteSentinel()
        # Synthetic frame (300 x 400 BGR)
        frame = np.full((300, 400, 3), 40, dtype=np.uint8)
        summary = {
            "ppe": {"detections": [], "falls_detected": 0},
            "fire_smoke": {"tier": "CLEAR"},
            "machinery_hazards": [],
            "total_violations": 0,
            "overall_severity": "CLEAR",
        }
        with patch(
            "app.sentinel.detect_multi_hazards",
            return_value=(Image.fromarray(frame[:, :, ::-1]), summary),
        ):
            annotated, telemetry = sentinel.process_frame(
                frame, conf=0.35, save_evidence=False
            )

        self.assertEqual(annotated.shape, frame.shape)
        self.assertIn("fps", telemetry)
        self.assertIn("tracked_workers", telemetry)
        self.assertIn("siren_trigger", telemetry)
        self.assertIn("hazard_summary", telemetry)

    def test_sentinel_worker_observation_includes_class_and_confidence(self):
        sentinel = SiteSentinel()
        summary = {
            "ppe": {
                "detections": [{
                    "class": "NO-Hardhat",
                    "bbox": [40, 40, 100, 160],
                    "conf": 0.88,
                    "is_violation": True,
                }],
                "falls_detected": 0,
            },
            "fire_smoke": {"tier": "CLEAR"},
            "machinery_hazards": [],
            "total_violations": 1,
            "overall_severity": "HIGH",
            "active_hazards": ["NO_HELMET"],
        }
        image = Image.new("RGB", (400, 300), "white")
        frame = np.full((300, 400, 3), 40, dtype=np.uint8)

        with patch("app.sentinel.detect_multi_hazards", return_value=(image, summary)):
            _, telemetry = sentinel.process_frame(frame, save_evidence=False)

        worker = telemetry["tracked_workers"][0]
        self.assertEqual(worker["observation_class"], "NO-Hardhat")
        self.assertAlmostEqual(worker["confidence"], 0.88)


if __name__ == "__main__":
    unittest.main()
