import io
import json
import os
import re
import tempfile
import unittest
import zlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

os.environ["SITESENTINEL_DISABLE_DEMO_SEED"] = "1"

from auth_test_support import sign_in_test_admin
from web import api
from app import restricted_zone
from app.video_detector import detect_ppe_video

database = api.database


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_db_path = database.DB_PATH
        self.original_dirs = {
            "DETECTIONS_DIR": api.DETECTIONS_DIR,
            "VIDEOS_DIR": api.VIDEOS_DIR,
            "UPLOADS_DIR": api.UPLOADS_DIR,
        }
        database.DB_PATH = self.root / "records.db"
        api.DETECTIONS_DIR = self.root / "detections"
        api.VIDEOS_DIR = self.root / "videos"
        api.UPLOADS_DIR = self.root / "uploads"
        for path in (api.DETECTIONS_DIR, api.VIDEOS_DIR, api.UPLOADS_DIR):
            path.mkdir(parents=True)
        database.init_db()
        self.client = TestClient(api.app)
        sign_in_test_admin(self.client)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        for name, value in self.original_dirs.items():
            setattr(api, name, value)
        self.temp_dir.cleanup()

    @staticmethod
    def png_bytes(size=(24, 24)):
        image = Image.new("RGB", size, "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    def test_database_preserves_unknown_compliance_and_stats(self):
        database.add_inspection("empty.png", 0, 0, 0, None)
        value = database.get_inspections().iloc[0]["compliance"]
        self.assertTrue(value is None or value != value)
        self.assertIsNone(database.get_stats()["avg_compliance"])

    def test_task_crud_validation_and_persistence(self):
        created = self.client.post(
            "/api/tasks",
            data={
                "name": "Foundation",
                "start_date": "2026-10-10",
                "end_date": "2026-10-20",
                "progress": "0",
            },
        )
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["id"]

        updated = self.client.patch(
            f"/api/tasks/{task_id}",
            data={
                "name": "Foundation and slab",
                "start_date": "2026-10-11",
                "end_date": "2026-10-21",
                "progress": "100",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(database.get_task(task_id)["progress"], 100)
        self.assertEqual(database.get_task(task_id)["name"], "Foundation and slab")
        database.init_db()
        self.assertEqual(database.get_task(task_id)["progress"], 100)

        self.assertEqual(
            self.client.patch("/api/tasks/999", data={"progress": "50"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/tasks/999").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/tasks/{task_id}").status_code, 200)
        database.init_db()
        self.assertIsNone(database.get_task(task_id))

    def test_task_input_rejects_invalid_dates_and_progress(self):
        base = {"name": "Task", "start_date": "2026-10-20", "end_date": "2026-10-10"}
        self.assertEqual(self.client.post("/api/tasks", data=base).status_code, 422)
        base["end_date"] = "2026-10-30"
        base["progress"] = "101"
        self.assertEqual(self.client.post("/api/tasks", data=base).status_code, 422)

    def test_sample_tasks_cannot_be_loaded_twice(self):
        self.assertEqual(self.client.post("/api/tasks/load-sample").status_code, 200)
        self.assertEqual(len(database.get_tasks()), 7)
        self.assertEqual(self.client.post("/api/tasks/load-sample").status_code, 409)
        self.assertEqual(len(database.get_tasks()), 7)

    def test_photo_upload_validation_and_controlled_inference_failure(self):
        self.assertEqual(
            self.client.post("/api/detect", files={"file": ("bad.txt", b"data")}).status_code,
            415,
        )
        self.assertEqual(
            self.client.post("/api/detect", files={"file": ("bad.jpg", b"not-image")}).status_code,
            400,
        )
        with patch.object(api, "detect_ppe", side_effect=RuntimeError("C:\\private\\trace")):
            response = self.client.post(
                "/api/detect",
                files={"file": ("ok.png", self.png_bytes(), "image/png")},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_photo_upload_size_limit_and_served_path_guard(self):
        oversized = self.client.post(
            "/api/detect",
            files={"file": ("large.png", b"x" * (api.MAX_IMAGE_BYTES + 1), "image/png")},
        )
        self.assertEqual(oversized.status_code, 413)
        with self.assertRaises(api.HTTPException) as error:
            api._safe_child(api.DETECTIONS_DIR, "..\\outside.jpg")
        self.assertEqual(error.exception.status_code, 400)

    def test_attendance_saves_only_text_and_discards_uploaded_photo(self):
        summary = {
            "ppe": {
                "helmets_ok": 0,
                "helmets_missing": 1,
                "vests_ok": 1,
                "vests_missing": 0,
                "detections": [],
            }
        }
        with patch.object(
            api,
            "detect_multi_hazards",
            return_value=(Image.new("RGB", (24, 24), "white"), summary),
        ):
            response = self.client.post(
                "/api/attendance/check-in",
                data={"employee_id": "EMP-12"},
                files={"file": ("worker.png", self.png_bytes(), "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["photo_retained"])
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["record"]["employee_id"], "EMP-12")
        self.assertEqual(result["record"]["ppe_status"], "REVIEW REQUIRED")
        self.assertIn("missing hardhat", result["record"]["findings"])
        self.assertNotIn("worker.png", response.text)
        self.assertEqual(list(api.UPLOADS_DIR.iterdir()), [])
        self.assertEqual(list(api.DETECTIONS_DIR.iterdir()), [])

        records = self.client.get("/api/attendance").json()["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["employee_id"], "EMP-12")
        conn = database._connect()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(attendance_records)")}
        conn.close()
        self.assertNotIn("image", columns)
        self.assertNotIn("photo", columns)

        with patch.object(
            api,
            "detect_multi_hazards",
            return_value=(Image.new("RGB", (24, 24), "white"), summary),
        ):
            again = self.client.post(
                "/api/attendance/check-in",
                data={"employee_id": "EMP-12"},
                files={"file": ("worker-again.png", self.png_bytes(), "image/png")},
            )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.client.get("/api/attendance").json()["records"]), 1)

    def test_attendance_rejects_invalid_employee_ids_and_images(self):
        invalid_id = self.client.post(
            "/api/attendance/check-in",
            data={"employee_id": "../worker"},
            files={"file": ("worker.png", self.png_bytes(), "image/png")},
        )
        self.assertEqual(invalid_id.status_code, 422)
        unsupported = self.client.post(
            "/api/attendance/check-in",
            data={"employee_id": "EMP-12"},
            files={"file": ("worker.gif", b"not image", "image/gif")},
        )
        self.assertEqual(unsupported.status_code, 415)
        corrupt = self.client.post(
            "/api/attendance/check-in",
            data={"employee_id": "EMP-12"},
            files={"file": ("worker.png", b"not image", "image/png")},
        )
        self.assertEqual(corrupt.status_code, 400)
        self.assertEqual(self.client.get("/api/attendance").json()["records"], [])

    def test_attendance_ticket_requires_supervisor_confirmation_and_is_idempotent(self):
        check_date = date.today().isoformat()
        database.record_attendance(
            "EMP-12",
            "REVIEW REQUIRED",
            "Model flagged a missing hardhat; supervisor review required.",
        )
        payload = {
            "employee_id": "EMP-12",
            "check_date": check_date,
            "confirmed_in_person": "true",
            "confirmed_by": "Supervisor One",
        }

        unconfirmed = dict(payload, confirmed_in_person="false")
        self.assertEqual(
            self.client.post("/api/attendance/confirm-ticket", data=unconfirmed).status_code,
            422,
        )
        invalid_date = dict(payload, check_date="not-a-date")
        self.assertEqual(
            self.client.post("/api/attendance/confirm-ticket", data=invalid_date).status_code,
            422,
        )
        missing = dict(payload, employee_id="EMP-MISSING")
        self.assertEqual(
            self.client.post("/api/attendance/confirm-ticket", data=missing).status_code,
            409,
        )

        confirmed = self.client.post("/api/attendance/confirm-ticket", data=payload)
        self.assertEqual(confirmed.status_code, 200)
        result = confirmed.json()
        self.assertTrue(result["created"])
        ticket = database.get_ticket(result["ticket_id"])
        self.assertEqual(ticket["hazard_type"], "PPE_FOLLOW_UP")
        self.assertEqual(ticket["severity"], "HIGH")
        self.assertIn("EMP-12", ticket["description"])
        self.assertIn("location not recorded", ticket["zone_name"])
        self.assertIn("verified in person by Supervisor One", ticket["supervisor_notes"])
        self.assertEqual(ticket["evidence_file"], "")

        linked = self.client.get("/api/attendance").json()["records"][0]
        self.assertEqual(linked["ticket_id"], result["ticket_id"])
        self.assertEqual(linked["confirmed_by"], "Supervisor One")

        duplicate = self.client.post(
            "/api/attendance/confirm-ticket",
            data=dict(payload, confirmed_by="Another Supervisor"),
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["ticket_id"], result["ticket_id"])
        self.assertEqual(
            len(database.get_tickets().query("hazard_type == 'PPE_FOLLOW_UP'")),
            1,
        )

        database.record_attendance("EMP-OK", "NO FLAG — VERIFY", "No PPE concern flagged.")
        no_concern = self.client.post(
            "/api/attendance/confirm-ticket",
            data=dict(payload, employee_id="EMP-OK"),
        )
        self.assertEqual(no_concern.status_code, 409)

    def test_environment_state_and_location_search_filter_to_india_and_state(self):
        states = self.client.get("/api/environment/states")
        self.assertEqual(states.status_code, 200)
        self.assertIn("Delhi", states.json()["states"])
        self.assertIn("Karnataka", states.json()["states"])
        with patch.object(api, "_fetch_open_meteo_json", return_value={
            "results": [
                {"country_code": "IN", "admin1": "Delhi", "admin2": "New Delhi",
                 "name": "New Delhi", "latitude": 28.6, "longitude": 77.2},
                {"country_code": "IN", "admin1": "Haryana", "admin2": "Gurugram",
                 "name": "Gurugram", "latitude": 28.4, "longitude": 77.0},
                {"country_code": "US", "admin1": "Delhi", "admin2": "Delhi",
                 "name": "Delhi", "latitude": 39.0, "longitude": -75.0},
            ]
        }):
            response = self.client.get(
                "/api/environment/locations",
                params={"state": "Delhi", "query": "New Delhi"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["locations"]), 1)
        self.assertEqual(response.json()["locations"][0]["district"], "New Delhi")
        self.assertEqual(
            self.client.get(
                "/api/environment/locations",
                params={"state": "Atlantis", "query": "Delhi"},
            ).status_code,
            422,
        )

    def test_environment_current_returns_model_weather_and_separate_scenario(self):
        air = {
            "current": {"time": "2026-10-23T13:00", "pm2_5": 20.0, "pm10": 45.0, "us_aqi": 55},
            "current_units": {"pm2_5": "µg/m³", "pm10": "µg/m³"},
            "hourly": {
                "time": ["2026-10-23T13:00", "2026-10-23T14:00"],
                "pm2_5": [20.0, 22.0],
                "pm10": [45.0, 48.0],
            },
        }
        weather = {
            "current": {
                "time": "2026-10-23T13:00", "temperature_2m": 26.0,
                "relative_humidity_2m": 60, "apparent_temperature": 27.0,
                "precipitation": 0.0, "weather_code": 2, "wind_speed_10m": 8.0,
            },
            "current_units": {
                "temperature_2m": "°C", "relative_humidity_2m": "%",
                "precipitation": "mm", "wind_speed_10m": "km/h",
            },
            "daily": {
                "time": ["2026-10-23"],
                "temperature_2m_max": [29.0], "temperature_2m_min": [21.0],
                "precipitation_sum": [0.0], "weather_code": [2],
            },
        }
        with patch.object(api, "_fetch_open_meteo_json", side_effect=[air, weather]) as fetch:
            response = self.client.get(
                "/api/environment/current",
                params={
                    "state": "Delhi", "district": "New Delhi", "name": "New Delhi",
                    "latitude": 28.6, "longitude": 77.2,
                    "activity": "typical", "mitigation": "basic",
                },
            )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["air_quality"]["index"], 55)
        self.assertEqual(result["air_quality"]["category"], "Moderate")
        self.assertEqual(result["weather"]["temperature_2m"], 26.0)
        self.assertEqual(len(result["trend"]), 2)
        self.assertEqual(result["construction_scenario"]["activity"], "typical")
        self.assertIn("not a CPCB station", result["attribution"])
        self.assertEqual(fetch.call_count, 2)

    def test_environment_rejects_out_of_india_coordinates_and_surfaces_provider_errors(self):
        query = {
            "state": "Delhi", "district": "New Delhi", "name": "New Delhi",
            "latitude": 40.0, "longitude": 77.2,
        }
        self.assertEqual(
            self.client.get("/api/environment/current", params=query).status_code,
            422,
        )
        query["latitude"] = 28.6
        with patch.object(
            api,
            "_fetch_open_meteo_json",
            side_effect=api.HTTPException(status_code=502, detail="provider unavailable"),
        ):
            response = self.client.get("/api/environment/current", params=query)
        self.assertEqual(response.status_code, 502)
        self.assertIn("provider unavailable", response.text)

    def test_environment_scenario_endpoint_validates_controls(self):
        response = self.client.get(
            "/api/environment/scenario",
            params={"pm25": 20, "pm10": 40, "activity": "dust_intensive", "mitigation": "strong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activity"], "dust_intensive")
        self.assertEqual(response.json()["mitigation"], "strong")
        invalid = self.client.get(
            "/api/environment/scenario",
            params={"pm25": 20, "pm10": 40, "activity": "unknown", "mitigation": "strong"},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_pre_shift_check_persists(self):
        self.assertEqual(self.client.get("/api/pre-shift").json()["completed_items"], 0)
        saved = self.client.post(
            "/api/pre-shift",
            data={
                "check_date": date.today().isoformat(),
                "headcount_ready": "true",
                "toolbox_talk": "true",
                "ppe_inspected": "true",
                "equipment_checked": "false",
                "access_clear": "true",
                "checked_by": "EMP-12",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            self.client.get("/api/pre-shift").json()["completed_items"],
            4,
        )
        invalid_date = self.client.post("/api/pre-shift", data={"check_date": "not-a-date"})
        self.assertEqual(invalid_date.status_code, 422)

    def test_saved_photo_creates_one_persistent_inspection(self):
        summary = {
            "total": 1,
            "helmet_ok": 1,
            "violations": 0,
            "compliance": 100.0,
            "boxes": [],
        }
        with patch.object(api, "detect_ppe", return_value=(Image.new("RGB", (24, 24)), summary)):
            response = self.client.post(
                "/api/detect",
                data={"save": "true", "conf": "0.35"},
                files={"file": ("site.png", self.png_bytes(), "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["total"], 1)
        self.assertIn("annotated_image", response.json())
        inspections = database.get_inspections()
        self.assertEqual(len(inspections), 1)
        saved = Path(inspections.iloc[0]["image_path"])
        self.assertTrue(saved.is_file())

    def test_video_rejects_unsupported_upload_type(self):
        response = self.client.post(
            "/api/detect-video",
            files={"file": ("not-a-video.exe", b"data", "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 415)

    def test_video_rejects_corrupt_upload_without_leaking_paths(self):
        response = self.client.post(
            "/api/detect-video",
            files={"file": ("broken.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(list(api.UPLOADS_DIR.iterdir()), [])
        self.assertEqual(list(api.VIDEOS_DIR.iterdir()), [])

    def test_annotated_video_uses_browser_playable_h264(self):
        input_path = self.root / "input.mp4"
        output_path = self.root / "annotated.mp4"
        writer = cv2.VideoWriter(
            str(input_path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 64)
        )
        self.assertTrue(writer.isOpened())
        for _ in range(10):
            writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
        writer.release()

        summary = {
            "total": 1,
            "helmet_ok": 1,
            "violations": 0,
            "compliance": 100.0,
            "boxes": [],
        }
        with patch(
            "app.video_detector.detect_ppe",
            return_value=(Image.new("RGB", (64, 64), "white"), summary),
        ):
            result = detect_ppe_video(input_path, output_path, sample_every=5)

        capture = cv2.VideoCapture(str(output_path))
        self.assertTrue(capture.isOpened())
        code = int(capture.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4)).upper()
        decoded_frames = 0
        while True:
            ok, _ = capture.read()
            if not ok:
                break
            decoded_frames += 1
        capture.release()

        self.assertIn(codec, {"avc1", "H264"})
        self.assertEqual(decoded_frames, 10)
        self.assertEqual(result["frames_analysed"], 2)

    def test_zone_polygon_validation_and_point_in_polygon(self):
        polygon = [[10, 10], [80, 10], [80, 80], [10, 80]]
        self.assertEqual(restricted_zone.validate_polygon(polygon, 100, 100)[0], (10.0, 10.0))
        self.assertTrue(restricted_zone.point_in_polygon((20, 20), polygon))
        self.assertTrue(restricted_zone.point_in_polygon((10, 30), polygon))
        self.assertFalse(restricted_zone.point_in_polygon((90, 30), polygon))

        invalid_polygons = [
            [[1, 1], [2, 2]],
            [[1, 1], [20, 1], [20, 20], [1, 20], [1, 1]],
            [[-1, 1], [20, 1], [20, 20]],
            [[1, 1], [float("nan"), 1], [20, 20]],
            [[10**1000, 1], [20, 1], [20, 20]],
            [[1, 1], [20, 20], [1, 20], [20, 1]],
            [[1, 1], [20, 1], [30, 1]],
        ]
        for invalid in invalid_polygons:
            with self.subTest(polygon=invalid), self.assertRaises(ValueError):
                restricted_zone.validate_polygon(invalid, 100, 100)

    def test_zone_classifies_multiple_people_and_clips_partial_boxes(self):
        polygon = [[0, 0], [50, 0], [50, 50], [0, 50]]
        people = restricted_zone.classify_person_boxes(
            [
                {"bbox": [10, 5, 20, 40], "confidence": 0.91},
                {"bbox": [60, 5, 70, 40], "confidence": 0.83},
                {"bbox": [-10, 5, 10, 120], "confidence": 0.72},
            ],
            polygon,
            100,
            100,
        )
        self.assertEqual(len(people), 3)
        self.assertEqual([person["zone_violation"] for person in people], [True, False, False])
        self.assertEqual(people[2]["bbox"], [0.0, 5.0, 10.0, 99.0])
        self.assertEqual(people[0]["ground_point"], [15.0, 40.0])

    def test_person_model_is_loaded_once_and_class_is_verified(self):
        fake_model = SimpleNamespace(names={0: "person"})
        with (
            patch.object(restricted_zone, "_person_model", None),
            patch.object(restricted_zone, "_person_class_id", None),
            patch.object(restricted_zone, "YOLO", return_value=fake_model) as load_model,
            patch.object(restricted_zone, "PERSON_MODEL_PATH", self.root / "yolov8n.pt"),
        ):
            (self.root / "yolov8n.pt").write_bytes(b"local-model")
            first = restricted_zone.get_person_model()
            second = restricted_zone.get_person_model()
        self.assertIs(first[0], second[0])
        self.assertEqual(first[1], 0)
        load_model.assert_called_once()

    def test_restricted_zone_api_classifies_and_returns_annotated_image(self):
        fake_boxes = [
            SimpleNamespace(xyxy=[np.array([2, 2, 8, 16])], conf=[0.91]),
            SimpleNamespace(xyxy=[np.array([15, 2, 22, 16])], conf=[0.83]),
        ]
        fake_model = SimpleNamespace(
            predict=Mock(return_value=[SimpleNamespace(boxes=fake_boxes)])
        )
        polygon = [[0, 0], [10, 0], [10, 18], [0, 18]]
        with patch.object(restricted_zone, "get_person_model", return_value=(fake_model, 0)):
            response = self.client.post(
                "/api/detect-zone",
                data={"polygon": json.dumps(polygon), "conf": "0.35"},
                files={"file": ("site.png", self.png_bytes((24, 24)), "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["total_persons"], 2)
        self.assertEqual(body["summary"]["inside_zone"], 1)
        self.assertEqual(body["summary"]["outside_zone"], 1)
        self.assertEqual(body["summary"]["violations"], 1)
        self.assertEqual(body["summary"]["persons"][0]["confidence"], 0.91)
        self.assertTrue(body["annotated_image"].startswith("data:image/jpeg;base64,"))
        self.assertFalse(body["saved"])
        self.assertEqual(database.get_stats()["checks"], 0)
        fake_model.predict.assert_called_once()
        self.assertEqual(fake_model.predict.call_args.kwargs["classes"], [0])
        self.assertEqual(fake_model.predict.call_args.kwargs["device"], "cpu")

    def test_restricted_zone_api_reports_empty_people_neutrally(self):
        fake_model = SimpleNamespace(
            predict=Mock(return_value=[SimpleNamespace(boxes=[])])
        )
        polygon = [[0, 0], [20, 0], [20, 20], [0, 20]]
        with patch.object(restricted_zone, "get_person_model", return_value=(fake_model, 0)):
            response = self.client.post(
                "/api/detect-zone",
                data={"polygon": json.dumps(polygon)},
                files={"file": ("site.png", self.png_bytes(), "image/png")},
            )
        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertEqual(summary["total_persons"], 0)
        self.assertEqual(summary["violations"], 0)
        self.assertEqual(
            summary["message"],
            "No people detected; zone compliance could not be assessed.",
        )
        self.assertEqual(database.get_stats()["checks"], 0)

    def test_restricted_zone_api_rejects_invalid_inputs_and_reports_model_failure(self):
        image = self.png_bytes()
        valid_polygon = [[0, 0], [20, 0], [20, 20]]
        cases = [
            ({"polygon": "[[0,0],[20,0]]"}, "site.png", image, 422),
            ({"polygon": json.dumps([[0, 0], [25, 0], [20, 20]])}, "site.png", image, 422),
            ({"polygon": "not-json"}, "site.png", image, 422),
            ({"polygon": json.dumps(valid_polygon)}, "broken.png", b"not-an-image", 400),
            ({"polygon": json.dumps(valid_polygon)}, "site.png", b"", 400),
            ({"polygon": json.dumps(valid_polygon)}, "site.exe", image, 415),
            ({"polygon": json.dumps(valid_polygon), "conf": "1.5"}, "site.png", image, 422),
        ]
        for data, filename, content, expected in cases:
            with self.subTest(filename=filename, data=data):
                response = self.client.post(
                    "/api/detect-zone",
                    data=data,
                    files={"file": (filename, content, "application/octet-stream")},
                )
                self.assertEqual(response.status_code, expected)

        missing_polygon = self.client.post(
            "/api/detect-zone",
            files={"file": ("site.png", image, "image/png")},
        )
        self.assertEqual(missing_polygon.status_code, 422)

        with patch.object(
            api,
            "detect_restricted_zone",
            side_effect=FileNotFoundError("private model path"),
        ):
            response = self.client.post(
                "/api/detect-zone",
                data={"polygon": json.dumps(valid_polygon)},
                files={"file": ("site.png", image, "image/png")},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("models/yolov8n.pt is missing", response.json()["detail"])
        self.assertNotIn("private", response.text)

    def test_empty_database_health_and_pdf(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        pdf = self.client.get("/api/report.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf.content, re.DOTALL)
        decoded = []
        for stream in streams:
            try:
                decoded.append(zlib.decompress(stream))
            except zlib.error:
                continue
        text = b" ".join(decoded)
        self.assertIn(b"No inspections recorded", text)
        self.assertNotIn(b"Site is compliant", text)
        self.assertIn(b"does not certify compliance", text)
        self.assertNotIn(b"Official Site Safety", text)


if __name__ == "__main__":
    unittest.main()
