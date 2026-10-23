import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image

os.environ["SITESENTINEL_DISABLE_DEMO_SEED"] = "1"

from app import database
from auth_test_support import sign_in_test_admin
from web import api


class TestApiV2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "records.db"
        database.init_db()
        self.client = TestClient(api.app)
        sign_in_test_admin(self.client)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def _create_test_image(size=(320, 240), color=(100, 150, 200)):
        img = Image.new("RGB", size, color=color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        return buf

    def test_sentinel_process_frame_endpoint(self):
        img_buf = self._create_test_image()
        telemetry = {"fps": 12.5, "siren_trigger": False, "workers": []}
        with patch.object(
            api._sentinel,
            "process_frame",
            side_effect=lambda frame, **kwargs: (frame, telemetry),
        ):
            response = self.client.post(
                "/api/sentinel/process-frame",
                files={"file": ("frame.jpg", img_buf, "image/jpeg")},
                data={"conf": "0.35", "save_evidence": "false"},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("annotated_frame", data)
        self.assertTrue(data["annotated_frame"].startswith("data:image/jpeg;base64,"))
        self.assertIn("telemetry", data)
        self.assertIn("fps", data["telemetry"])
        self.assertIn("siren_trigger", data["telemetry"])

    def test_detect_multi_endpoint(self):
        img_buf = self._create_test_image()
        summary = {"overall_severity": "CLEAR", "ppe": {}, "fire_smoke": {}}
        with patch.object(
            api,
            "detect_multi_hazards",
            side_effect=lambda image, **kwargs: (image, summary),
        ):
            response = self.client.post(
                "/api/detect-multi",
                files={"file": ("site.jpg", img_buf, "image/jpeg")},
                data={"conf": "0.35", "fire_conf": "0.30"},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("annotated_image", data)
        self.assertIn("summary", data)
        self.assertIn("overall_severity", data["summary"])
        self.assertIn("ppe", data["summary"])
        self.assertIn("fire_smoke", data["summary"])

    def test_evidence_endpoint(self):
        response = self.client.get("/api/evidence")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("evidence", data)
        self.assertIsInstance(data["evidence"], list)


if __name__ == "__main__":
    unittest.main()
