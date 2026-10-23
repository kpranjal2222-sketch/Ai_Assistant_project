"""Helpers for API tests that exercise authenticated application routes."""


def sign_in_test_admin(client):
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "test-admin",
            "display_name": "Test Administrator",
            "password": "test-admin-password-123",
        },
    )
    if setup.status_code != 200:
        raise AssertionError(f"Could not create test administrator: {setup.text}")
    return setup.json()["user"]
