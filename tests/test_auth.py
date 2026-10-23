import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["SITESENTINEL_DISABLE_DEMO_SEED"] = "1"

from app import auth, database
from web import api


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "records.db"
        database.init_db()
        self.client = TestClient(api.app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def first_admin_payload():
        return {
            "username": "site-admin",
            "display_name": "Site Administrator",
            "password": "a-unique-test-password",
        }

    def create_admin(self, client=None):
        client = client or self.client
        return client.post("/api/auth/setup", json=self.first_admin_payload())

    def test_setup_login_logout_and_session_cookie(self):
        status = self.client.get("/api/auth/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["configured"])
        self.assertEqual(self.client.get("/api/stats").status_code, 401)
        self.assertEqual(self.client.get("/sample_videos/").status_code, 401)

        setup = self.create_admin()
        self.assertEqual(setup.status_code, 200)
        self.assertEqual(setup.json()["user"]["role"], "admin")
        self.assertIn("httponly", setup.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", setup.headers["set-cookie"].lower())
        self.assertTrue(self.client.get("/api/auth/status").json()["authenticated"])
        self.assertEqual(self.create_admin().status_code, 409)

        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/stats").status_code, 401)

        login = self.client.post(
            "/api/auth/login",
            json={"username": "SITE-ADMIN", "password": "a-unique-test-password"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"]["username"], "site-admin")

    def test_password_is_hashed_and_login_rejects_bad_credentials(self):
        self.create_admin()
        connection = database._connect()
        try:
            stored = connection.execute(
                "SELECT password_hash, password_salt FROM app_users"
            ).fetchone()
        finally:
            connection.close()
        self.assertNotIn("a-unique-test-password", stored[0])
        self.assertNotEqual(stored[0], stored[1])

        bad_login = TestClient(api.app)
        response = bad_login.post(
            "/api/auth/login",
            json={"username": "site-admin", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)

    def test_roles_restrict_writes_and_account_management(self):
        self.create_admin()
        created = self.client.post(
            "/api/auth/users",
            json={
                "username": "read-only",
                "display_name": "Read Only",
                "password": "another-test-password",
                "role": "viewer",
            },
        )
        self.assertEqual(created.status_code, 200)

        viewer = TestClient(api.app)
        login = viewer.post(
            "/api/auth/login",
            json={"username": "read-only", "password": "another-test-password"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(viewer.get("/api/stats").status_code, 200)
        self.assertEqual(
            viewer.post("/api/tasks", data={"name": "Denied"}).status_code,
            403,
        )
        self.assertEqual(viewer.get("/api/auth/users").status_code, 403)

        supervisor = self.client.post(
            "/api/auth/users",
            json={
                "username": "site-supervisor",
                "display_name": "Supervisor",
                "password": "supervisor-test-password",
                "role": "supervisor",
            },
        )
        self.assertEqual(supervisor.status_code, 200)
        supervisor_client = TestClient(api.app)
        supervisor_client.post(
            "/api/auth/login",
            json={"username": "site-supervisor", "password": "supervisor-test-password"},
        )
        self.assertEqual(supervisor_client.get("/api/auth/users").status_code, 403)

        self.assertEqual(self.client.get("/api/auth/users").status_code, 200)

    def test_deactivation_revokes_sessions_and_last_admin_is_protected(self):
        setup = self.create_admin()
        admin_id = setup.json()["user"]["id"]
        self.assertEqual(
            self.client.patch(
                f"/api/auth/users/{admin_id}",
                json={"active": False},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/users",
                json={
                    "username": "disable-me",
                    "display_name": "Disable Me",
                    "password": "disable-test-password",
                    "role": "viewer",
                },
            ).status_code,
            200,
        )
        viewer = TestClient(api.app)
        viewer.post(
            "/api/auth/login",
            json={"username": "disable-me", "password": "disable-test-password"},
        )
        viewer_id = next(
            user["id"]
            for user in self.client.get("/api/auth/users").json()["users"]
            if user["username"] == "disable-me"
        )
        self.assertEqual(
            self.client.patch(
                f"/api/auth/users/{viewer_id}",
                json={"active": False},
            ).status_code,
            200,
        )
        self.assertEqual(viewer.get("/api/stats").status_code, 401)

    def test_session_expiry_and_cross_origin_write_protection(self):
        self.create_admin()
        with mock.patch.object(
            auth, "_utc_now", return_value=auth._utc_now() + auth.SESSION_LIFETIME
        ):
            self.assertEqual(self.client.get("/api/stats").status_code, 401)

        response = self.client.post(
            "/api/tasks",
            headers={"Origin": "https://untrusted.example"},
            data={"name": "Blocked task"},
        )
        self.assertEqual(response.status_code, 403)

    def test_setup_token_is_enforced_when_configured(self):
        with mock.patch.dict(os.environ, {"SITESENTINEL_SETUP_TOKEN": "one-time-token"}):
            denied = self.create_admin()
            self.assertEqual(denied.status_code, 403)
            accepted = self.client.post(
                "/api/auth/setup",
                headers={"X-Setup-Token": "one-time-token"},
                json=self.first_admin_payload(),
            )
            self.assertEqual(accepted.status_code, 200)

    def test_login_attempts_are_rate_limited(self):
        client_key = "198.51.100.24"
        limited_client = TestClient(api.app, client=(client_key, 1234))
        try:
            with mock.patch.object(auth, "create_session", return_value=None):
                for _ in range(api._LOGIN_ATTEMPT_LIMIT):
                    response = limited_client.post(
                        "/api/auth/login",
                        json={"username": "site-admin", "password": "incorrect-password"},
                    )
                    self.assertEqual(response.status_code, 401)
                blocked = limited_client.post(
                    "/api/auth/login",
                    json={"username": "site-admin", "password": "incorrect-password"},
                )
                self.assertEqual(blocked.status_code, 429)
        finally:
            with api._login_attempt_lock:
                api._login_attempts.pop(client_key, None)


if __name__ == "__main__":
    unittest.main()
