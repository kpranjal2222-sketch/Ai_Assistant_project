import unittest
from pathlib import Path
import numpy as np
from PIL import Image

from app.hazard_engine import (
    detect_multi_hazards,
    get_models,
    _calculate_distance,
    _get_bottom_center,
)

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILES = (
    ROOT / "models" / "ppe_multi.pt",
    ROOT / "models" / "fire_smoke.pt",
    ROOT / "models" / "yolov8n.pt",
)
MODELS_AVAILABLE = all(path.is_file() for path in MODEL_FILES)


class TestHazardEngine(unittest.TestCase):
    @unittest.skipUnless(MODELS_AVAILABLE, "Local model weights are not included in source-only checkout")
    def test_models_exist_and_load(self):
        ppe_model, fire_model, coco_model = get_models()
        self.assertIsNotNone(ppe_model, "PPE Multi model failed to load")
        self.assertIsNotNone(fire_model, "Fire/Smoke model failed to load")
        self.assertIsNotNone(coco_model, "COCO person/vehicle model failed to load")

        self.assertIn("Hardhat", ppe_model.names.values())
        self.assertIn("Safety Vest", ppe_model.names.values())
        self.assertIn("fire", fire_model.names.values())
        self.assertIn("smoke", fire_model.names.values())

    def test_distance_and_bottom_center_math(self):
        bbox = [10.0, 20.0, 30.0, 100.0]
        center = _get_bottom_center(bbox)
        self.assertEqual(center, (20.0, 100.0))

        dist = _calculate_distance((0, 0), (3, 4))
        self.assertEqual(dist, 5.0)

    @unittest.skipUnless(MODELS_AVAILABLE, "Local model weights are not included in source-only checkout")
    def test_synthetic_image_clear_result(self):
        # Blank green canvas should yield 0 hazards and CLEAR severity
        blank = Image.new("RGB", (400, 300), color=(34, 139, 34))
        annotated, summary = detect_multi_hazards(blank, ppe_conf=0.4, fire_conf=0.4)
        self.assertIsInstance(annotated, Image.Image)
        self.assertEqual(summary["overall_severity"], "CLEAR")
        self.assertEqual(summary["fire_smoke"]["tier"], "CLEAR")
        self.assertEqual(summary["ppe"]["helmets_ok"], 0)
        self.assertEqual(summary["ppe"]["helmets_missing"], 0)
        self.assertEqual(len(summary["machinery_hazards"]), 0)

    @unittest.skipUnless(MODELS_AVAILABLE, "Local model weights are not included in source-only checkout")
    def test_sample_workers_image(self):
        sample_path = ROOT / "sample_images" / "workers_site_1.jpg"
        if not sample_path.exists():
            self.skipTest("sample image workers_site_1.jpg not found")

        img = Image.open(sample_path)
        annotated, summary = detect_multi_hazards(img, ppe_conf=0.35, fire_conf=0.30)
        self.assertIsInstance(annotated, Image.Image)
        self.assertGreater(summary["ppe"]["helmets_ok"], 0, "Expected at least 1 helmet in workers_site_1")
        self.assertEqual(summary["fire_smoke"]["tier"], "CLEAR")

    @unittest.skipUnless(MODELS_AVAILABLE, "Local model weights are not included in source-only checkout")
    def test_sample_violation_image(self):
        sample_path = ROOT / "sample_images" / "violation_demo.jpg"
        if not sample_path.exists():
            self.skipTest("sample image violation_demo.jpg not found")

        img = Image.open(sample_path)
        annotated, summary = detect_multi_hazards(img, ppe_conf=0.35, fire_conf=0.30)
        self.assertIsInstance(annotated, Image.Image)
        self.assertGreater(summary["ppe"]["helmets_missing"], 0, "Expected helmet violations in violation_demo")
        self.assertIn(summary["overall_severity"], ["MODERATE", "CRITICAL", "CATASTROPHIC"])


if __name__ == "__main__":
    unittest.main()
