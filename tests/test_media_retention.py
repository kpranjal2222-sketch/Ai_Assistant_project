import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scripts.cleanup_media import cleanup_old_media


class TestMediaRetention(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        for directory in ("detections", "evidence", "uploads", "videos"):
            (self.data_dir / directory).mkdir(parents=True)
        database = sqlite3.connect(self.data_dir / "records.db")
        database.executescript(
            """
            CREATE TABLE inspections (image_path TEXT);
            CREATE TABLE incident_tickets (evidence_file TEXT);
            """
        )
        database.commit()
        database.close()
        self.now = datetime(2026, 10, 23, 12, 0, 0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _old_file(self, directory, name):
        path = self.data_dir / directory / name
        path.write_bytes(b"sample")
        old_time = (self.now - timedelta(days=45)).timestamp()
        os.utime(path, (old_time, old_time))
        return path

    def assert_same_media_path(self, actual, expected):
        self.assertEqual(actual.parent.name, expected.parent.name)
        self.assertEqual(actual.name, expected.name)

    def test_dry_run_lists_unreferenced_old_files_without_deleting(self):
        path = self._old_file("videos", "annotated.mp4")
        candidates = cleanup_old_media(30, data_dir=self.data_dir, now=self.now)
        self.assertEqual(len(candidates), 1)
        self.assert_same_media_path(candidates[0], path)
        self.assertTrue(path.is_file())

    def test_apply_deletes_only_explicitly_eligible_files(self):
        path = self._old_file("videos", "annotated.mp4")
        recent_path = self.data_dir / "videos" / "recent.mp4"
        recent_path.write_bytes(b"recent")
        candidates = cleanup_old_media(30, apply=True, data_dir=self.data_dir, now=self.now)
        self.assertEqual(len(candidates), 1)
        self.assert_same_media_path(candidates[0], path)
        self.assertFalse(path.exists())
        self.assertTrue(recent_path.exists())

    def test_saved_inspection_and_ticket_evidence_are_never_candidates(self):
        inspection = self._old_file("detections", "inspection.jpg")
        ticket_evidence = self._old_file("evidence", "ticket.jpg")
        unreferenced = self._old_file("evidence", "orphan.jpg")
        database = sqlite3.connect(self.data_dir / "records.db")
        database.execute("INSERT INTO inspections (image_path) VALUES (?)", (str(inspection),))
        database.execute("INSERT INTO incident_tickets (evidence_file) VALUES (?)", (ticket_evidence.name,))
        database.commit()
        database.close()

        candidates = cleanup_old_media(30, data_dir=self.data_dir, now=self.now)
        self.assertEqual(len(candidates), 1)
        self.assert_same_media_path(candidates[0], unreferenced)

    def test_invalid_retention_age_is_rejected(self):
        with self.assertRaises(ValueError):
            cleanup_old_media(0, data_dir=self.data_dir, now=self.now)


if __name__ == "__main__":
    unittest.main()
