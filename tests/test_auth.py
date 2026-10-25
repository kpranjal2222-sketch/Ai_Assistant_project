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
        with api._login_attempt_lock:
            api._login_attempts.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "records.db"
        database.init_db()
        self.client = TestClient(api.app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()
        with api._login_attempt_lock:
            api._login_attempts.clear()

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

    def test_account_setup_accepts_five_character_username_and_six_character_password(self):
        response = self.client.post(
            "/api/auth/setup",
            json={
                "username": "abcde",
                "display_name": "Project Admin",
                "password": "abc123",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_account_setup_rejects_username_or_password_below_minimum(self):
        for username, password in (("abcd", "abc123"), ("abcde", "abc12")):
            with self.subTest(username=username, password=password):
                response = self.client.post(
                    "/api/auth/setup",
                    json={
                        "username": username,
                        "display_name": "Project Admin",
                        "password": password,
                    },
                )
                self.assertEqual(response.status_code, 422)

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

    def test_worker_invite_registration_unique_ids_and_restricted_access(self):
        self.create_admin()
        database.record_attendance("EMP001", "NO FLAG — VERIFY", "Supervisor review required.")
        with mock.patch.dict(os.environ, {"SITESENTINEL_WORKER_INVITE_CODE": "site-invite"}):
            worker = TestClient(api.app)
            registration = worker.post(
                "/api/auth/worker-register",
                json={
                    "employee_id": "EMP0001",
                    "display_name": "Worker One",
                    "password": "worker-test-password",
                    "invite_code": "site-invite",
                },
            )
            self.assertEqual(registration.status_code, 200, registration.text)
            self.assertEqual(registration.json()["user"]["username"], "EMP001")
            self.assertEqual(registration.json()["user"]["role"], "worker")
            self.assertEqual(
                worker.get("/api/attendance/me").json()["records"][0]["employee_id"],
                "EMP001",
            )
            self.assertEqual(worker.get("/api/environment/states").status_code, 200)
            self.assertEqual(worker.get("/api/stats").status_code, 403)
            self.assertEqual(worker.get("/api/attendance").status_code, 403)
            self.assertEqual(worker.get("/api/auth/users").status_code, 403)
            self.assertEqual(worker.get("/sample_videos/").status_code, 403)
            self.assertEqual(
                worker.post("/api/tasks", data={"name": "Not allowed"}).status_code,
                403,
            )
            self.assertEqual(
                worker.post(
                    "/api/attendance/check-in",
                    data={"employee_id": "EMP002"},
                    files={"file": ("photo.jpg", b"not an image", "image/jpeg")},
                ).status_code,
                403,
            )

            duplicate = TestClient(api.app).post(
                "/api/auth/worker-register",
                json={
                    "employee_id": "EMP001",
                    "display_name": "Worker Two",
                    "password": "worker-test-password",
                    "invite_code": "site-invite",
                },
            )
            self.assertEqual(duplicate.status_code, 409)

            wrong_role = TestClient(api.app).post(
                "/api/auth/login",
                json={
                    "username": "EMP001",
                    "password": "worker-test-password",
                    "role": "supervisor",
                },
            )
            self.assertEqual(wrong_role.status_code, 401)
            correct_role = TestClient(api.app).post(
                "/api/auth/login",
                json={
                    "username": "EMP001",
                    "password": "worker-test-password",
                    "role": "worker",
                },
            )
            self.assertEqual(correct_role.status_code, 200)
            worker_user_id = next(
                user["id"]
                for user in self.client.get("/api/auth/users").json()["users"]
                if user["username"] == "EMP001"
            )
            self.assertEqual(
                self.client.patch(
                    f"/api/auth/users/{worker_user_id}",
                    json={"active": False},
                ).status_code,
                200,
            )
            duplicate_after_deactivation = TestClient(api.app).post(
                "/api/auth/worker-register",
                json={
                    "employee_id": "EMP001",
                    "display_name": "Worker Three",
                    "password": "worker-test-password",
                    "invite_code": "site-invite",
                },
            )
            self.assertEqual(duplicate_after_deactivation.status_code, 409)

    def test_worker_registration_requires_configured_invite_code(self):
        with mock.patch.dict(os.environ, {"SITESENTINEL_WORKER_INVITE_CODE": ""}):
            response = self.client.post(
                "/api/auth/worker-register",
                json={
                    "employee_id": "EMP001",
                    "display_name": "Worker One",
                    "password": "worker-test-password",
                    "invite_code": "anything",
                },
            )
        self.assertEqual(response.status_code, 503)

    def test_worker_dashboard_hazard_reports_site_info_and_private_today_status(self):
        self.create_admin()
        with mock.patch.dict(os.environ, {"SITESENTINEL_WORKER_INVITE_CODE": "site-invite"}):
            worker = TestClient(api.app)
            registration = worker.post(
                "/api/auth/worker-register",
                json={
                    "employee_id": "EMP007",
                    "display_name": "Worker Seven",
                    "password": "worker-test-password",
                    "invite_code": "site-invite",
                },
            )
            self.assertEqual(registration.status_code, 200, registration.text)
            database.record_attendance("EMP007", "NO FLAG — VERIFY", "Supervisor review required.")
            today = worker.get("/api/attendance/today")
            self.assertEqual(today.status_code, 200)
            self.assertEqual(today.json()["record"]["employee_id"], "EMP007")
            site_info = worker.get("/api/worker/site-info")
            self.assertEqual(site_info.status_code, 200)
            self.assertEqual(site_info.json()["emergency_contact"], "")

            database.clear_tickets()
            report = worker.post(
                "/api/worker/hazard-reports",
                json={
                    "report_type": "near_miss",
                    "location": "North loading area",
                    "description": "A loose cable was across the walking route.",
                },
            )
            self.assertEqual(report.status_code, 200, report.text)
            self.assertTrue(report.json()["ticket_id"].startswith("INC-"))
            self.assertEqual(worker.get("/api/tickets").status_code, 403)
            self.assertEqual(
                worker.put(
                    "/api/worker/site-info",
                    json={"site_notice": "Not authorized"},
                ).status_code,
                403,
            )
            tickets = self.client.get("/api/tickets").json()["tickets"]
            self.assertEqual(len(tickets), 1)
            self.assertIn("EMP007", tickets[0]["description"])
            self.assertEqual(tickets[0]["hazard_type"], "WORKER_NEAR_MISS_REPORT")

            saved_info = self.client.put(
                "/api/worker/site-info",
                json={
                    "emergency_contact": "Site office 100",
                    "first_aid_location": "Ground-floor site office",
                    "assembly_point": "East gate",
                    "site_notice": "Keep the south access clear today.",
                },
            )
            self.assertEqual(saved_info.status_code, 200, saved_info.text)
            self.assertEqual(
                worker.get("/api/worker/site-info").json()["assembly_point"],
                "East gate",
            )

    def test_visitor_registration_creates_read_only_account_only(self):
        response = self.client.post(
            "/api/auth/visitor-register",
            json={
                "username": "site-visitor",
                "display_name": "Site Visitor",
                "password": "visitor-test-password",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["role"], "viewer")
        self.assertEqual(self.client.get("/api/stats").status_code, 200)
        self.assertEqual(
            self.client.post("/api/tasks", data={"name": "Visitor task"}).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/auth/users").status_code, 403)

        connection = database._connect()
        try:
            account = connection.execute(
                "SELECT role FROM app_users WHERE username = 'site-visitor'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(account, ("viewer",))

    def test_existing_database_migrates_worker_role_without_losing_accounts_or_sessions(self):
        legacy_db_path = Path(self.temp_dir.name) / "legacy-records.db"
        database.DB_PATH = legacy_db_path
        connection = database._connect()
        connection.executescript(
            """
            CREATE TABLE app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_rounds INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'supervisor', 'viewer')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE app_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            INSERT INTO app_users
                (username, display_name, password_hash, password_salt,
                 password_rounds, role, created_at)
            VALUES ('legacy-admin', 'Legacy Admin', 'hash', 'salt', 1, 'admin', '2026-01-01');
            INSERT INTO app_sessions
                (token_hash, user_id, created_at, expires_at)
            VALUES ('session-hash', 1, '2026-01-01', '2099-01-01');
            """
        )
        connection.close()
        try:
            database.init_db()
            connection = database._connect()
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT username, role FROM app_users WHERE id = 1"
                    ).fetchone(),
                    ("legacy-admin", "admin"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT user_id FROM app_sessions WHERE token_hash = 'session-hash'"
                    ).fetchone(),
                    (1,),
                )
                connection.execute(
                    """
                    INSERT INTO app_users
                        (username, display_name, password_hash, password_salt,
                         password_rounds, role, created_at)
                    VALUES ('EMP001', 'Worker', 'hash', 'salt', 1, 'worker', '2026-01-01')
                    """
                )
                connection.commit()
            finally:
                connection.close()
        finally:
            database.DB_PATH = Path(self.temp_dir.name) / "records.db"

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
