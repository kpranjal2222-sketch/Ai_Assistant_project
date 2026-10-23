import io
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image

os.environ["SITESENTINEL_DISABLE_DEMO_SEED"] = "1"

from auth_test_support import sign_in_test_admin
from app import database
from web import api


class TestIncidentTickets(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_evidence_dir = api.EVIDENCE_DIR
        database.DB_PATH = Path(self.temp_dir.name) / "records.db"
        api.EVIDENCE_DIR = Path(self.temp_dir.name) / "evidence"
        api.EVIDENCE_DIR.mkdir()
        database.init_db()
        database.clear_tickets()
        self.client = TestClient(api.app)
        sign_in_test_admin(self.client)

    def tearDown(self):
        database.clear_tickets()
        database.DB_PATH = self.original_db_path
        api.EVIDENCE_DIR = self.original_evidence_dir
        self.temp_dir.cleanup()

    def test_ticket_lifecycle_and_risk_index(self):
        # Initial risk index with 0 tickets should be 100
        idx_initial = database.get_site_risk_index()
        self.assertEqual(idx_initial["score"], 100)
        self.assertEqual(idx_initial["rating"], "LOW TICKET RISK")

        # 1. Create a Critical ticket
        tid = database.create_ticket(
            hazard_type="PROXIMITY_DANGER",
            severity="CRITICAL",
            zone_name="Zone A: Excavation Pit",
            description="Worker within 1.2m of excavator swing radius",
            evidence_file="test_snap.jpg",
            worker_id=2,
        )
        self.assertTrue(tid.startswith("INC-"))

        # Risk score should decrease (100 - 15 = 85)
        idx_after_open = database.get_site_risk_index()
        self.assertEqual(idx_after_open["score"], 85)
        self.assertEqual(idx_after_open["open_tickets"], 1)

        # 2. Update to INVESTIGATING
        updated = database.update_ticket_status(tid, "INVESTIGATING", supervisor_notes="Supervisor alerted.")
        self.assertTrue(updated)
        ticket = database.get_ticket(tid)
        self.assertEqual(ticket["status"], "INVESTIGATING")
        self.assertEqual(ticket["supervisor_notes"], "Supervisor alerted.")

        # In investigation: penalty reduced by 50% (100 - 7 = 93)
        idx_investigating = database.get_site_risk_index()
        self.assertGreater(idx_investigating["score"], 85)

        # 3. Resolve ticket -> REMEDIATED
        database.update_ticket_status(tid, "REMEDIATED", supervisor_notes="Worker safely repositioned.")
        idx_remediated = database.get_site_risk_index()
        self.assertEqual(idx_remediated["score"], 100)
        self.assertEqual(idx_remediated["open_tickets"], 0)
        self.assertEqual(idx_remediated["remediated_tickets"], 1)

        database.create_ticket(
            hazard_type="MISSING_PPE",
            severity="HIGH",
            zone_name="North access road",
            description="A worker needs a replacement hardhat.",
        )
        high_risk = database.get_site_risk_index()
        self.assertEqual(high_risk["score"], 90)
        self.assertEqual(high_risk["breakdown"]["high"], 1)

    def test_tickets_api_endpoints(self):
        # Test POST /api/tickets
        create_res = self.client.post(
            "/api/tickets",
            data={
                "hazard_type": "FIRE_EMERGENCY",
                "severity": "CATASTROPHIC",
                "zone_name": "Hot-Work Bay",
                "description": "Active flame detected near fuel storage",
            },
        )
        self.assertEqual(create_res.status_code, 200)
        tid = create_res.json()["ticket_id"]

        # Test GET /api/tickets
        list_res = self.client.get("/api/tickets")
        self.assertEqual(list_res.status_code, 200)
        tickets = list_res.json()["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["ticket_id"], tid)

        # Test GET /api/risk-index
        risk_res = self.client.get("/api/risk-index")
        self.assertEqual(risk_res.status_code, 200)
        self.assertEqual(risk_res.json()["score"], 70)  # 100 - 30 for Catastrophic

        # Test PATCH /api/tickets/{ticket_id}
        patch_res = self.client.patch(
            f"/api/tickets/{tid}",
            data={"status": "REMEDIATED", "supervisor_notes": "Flame extinguished."},
        )
        self.assertEqual(patch_res.status_code, 200)

        # Test GET /api/zones
        zones_res = self.client.get("/api/zones")
        self.assertEqual(zones_res.status_code, 200)
        self.assertGreaterEqual(len(zones_res.json()["zones"]), 4)

    def test_ticket_creation_rejects_invalid_fields(self):
        response = self.client.post(
            "/api/tickets",
            data={
                "hazard_type": "MISSING_PPE",
                "severity": "URGENT",
                "zone_name": "North access road",
                "description": "A worker needs a replacement hardhat.",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(database.get_tickets()), 0)

    def test_ticket_can_attach_validated_optional_evidence_image(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 24), "orange").save(image_bytes, format="PNG")
        response = self.client.post(
            "/api/tickets",
            data={
                "hazard_type": "MISSING_PPE",
                "severity": "HIGH",
                "zone_name": "Review location",
                "description": "Reviewer confirmed a possible missing hardhat.",
            },
            files={"evidence_upload": ("review.png", image_bytes.getvalue(), "image/png")},
        )

        self.assertEqual(response.status_code, 200)
        ticket = database.get_ticket(response.json()["ticket_id"])
        self.assertRegex(ticket["evidence_file"], r"^ticket_[a-f0-9]{32}\.jpg$")
        evidence_path = api.EVIDENCE_DIR / ticket["evidence_file"]
        self.assertTrue(evidence_path.is_file())
        with Image.open(evidence_path) as evidence:
            self.assertEqual(evidence.format, "JPEG")
            self.assertEqual(evidence.size, (32, 24))
        served = self.client.get(f"/api/evidence/{ticket['evidence_file']}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/jpeg")

    def test_ticket_evidence_rejects_unsupported_or_invalid_images(self):
        ticket_data = {
            "hazard_type": "MISSING_PPE",
            "severity": "HIGH",
            "zone_name": "Review location",
            "description": "Reviewer confirmed a possible missing hardhat.",
        }
        unsupported = self.client.post(
            "/api/tickets",
            data=ticket_data,
            files={"evidence_upload": ("review.svg", b"<svg/>", "image/svg+xml")},
        )
        self.assertEqual(unsupported.status_code, 415)

        invalid_image = self.client.post(
            "/api/tickets",
            data=ticket_data,
            files={"evidence_upload": ("review.png", b"not an image", "image/png")},
        )
        self.assertEqual(invalid_image.status_code, 400)
        self.assertEqual(len(database.get_tickets()), 0)
        self.assertEqual(list(api.EVIDENCE_DIR.iterdir()), [])

    def test_ticket_ids_remain_unique_after_deletion(self):
        first = database.create_ticket("MISSING_PPE", "HIGH", "Zone A", "First report.")
        second = database.create_ticket("MISSING_PPE", "HIGH", "Zone A", "Second report.")
        self.assertTrue(database.delete_ticket(first))
        third = database.create_ticket("MISSING_PPE", "HIGH", "Zone A", "Third report.")
        sequences = [ticket.rsplit("-", 1)[-1] for ticket in (first, second, third)]
        self.assertEqual(sequences, ["001", "002", "003"])


if __name__ == "__main__":
    unittest.main()
