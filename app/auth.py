"""Password hashing and database-backed sessions for the local application."""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from app import database

PASSWORD_ROUNDS = 600_000
SESSION_LIFETIME = timedelta(hours=8)
VALID_ROLES = {"admin", "supervisor", "viewer"}
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


class AuthenticationError(ValueError):
    """An authentication or user-management request was invalid."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _normalise_username(username: str) -> str:
    username = username.strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise AuthenticationError(
            "Username must be 3–32 letters, numbers, dots, hyphens, or underscores."
        )
    return username


def _validate_password(password: str) -> None:
    if not 12 <= len(password) <= 256:
        raise AuthenticationError("Password must be 12–256 characters long.")
    if password.isspace():
        raise AuthenticationError("Password cannot contain only whitespace.")


def _password_record(password: str) -> tuple[str, str, int]:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS
    )
    return password_hash.hex(), salt.hex(), PASSWORD_ROUNDS


def _password_matches(password: str, password_hash: str, salt: str, rounds: int) -> bool:
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds
    )
    return hmac.compare_digest(candidate.hex(), password_hash)


def user_count() -> int:
    connection = database._connect()
    try:
        return connection.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]
    finally:
        connection.close()


def create_first_admin(username: str, display_name: str, password: str) -> dict:
    username = _normalise_username(username)
    display_name = display_name.strip()
    if not display_name or len(display_name) > 80:
        raise AuthenticationError("Display name must be 1–80 characters.")
    _validate_password(password)
    password_hash, salt, rounds = _password_record(password)
    connection = database._connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM app_users LIMIT 1").fetchone():
            raise AuthenticationError("Initial account setup is already complete.", 409)
        cursor = connection.execute(
            """
            INSERT INTO app_users
                (username, display_name, password_hash, password_salt,
                 password_rounds, role, created_at)
            VALUES (?, ?, ?, ?, ?, 'admin', ?)
            """,
            (username, display_name, password_hash, salt, rounds, _utc_now().isoformat()),
        )
        connection.commit()
        return {
            "id": cursor.lastrowid,
            "username": username,
            "display_name": display_name,
            "role": "admin",
        }
    except AuthenticationError:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_user(username: str, display_name: str, password: str, role: str) -> dict:
    username = _normalise_username(username)
    display_name = display_name.strip()
    if not display_name or len(display_name) > 80:
        raise AuthenticationError("Display name must be 1–80 characters.")
    if role not in VALID_ROLES:
        raise AuthenticationError("Choose admin, supervisor, or viewer.")
    _validate_password(password)
    password_hash, salt, rounds = _password_record(password)
    connection = database._connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO app_users
                (username, display_name, password_hash, password_salt,
                 password_rounds, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username, display_name, password_hash, salt, rounds,
                role, _utc_now().isoformat(),
            ),
        )
        connection.commit()
        return {
            "id": cursor.lastrowid,
            "username": username,
            "display_name": display_name,
            "role": role,
        }
    except Exception as error:
        connection.rollback()
        if "UNIQUE constraint failed: app_users.username" in str(error):
            raise AuthenticationError("That username is already in use.", 409) from error
        raise
    finally:
        connection.close()


def list_users() -> list[dict]:
    connection = database._connect()
    try:
        rows = connection.execute(
            """
            SELECT id, username, display_name, role, is_active, created_at
            FROM app_users ORDER BY username COLLATE NOCASE
            """
        ).fetchall()
        return [
            {
                "id": row[0],
                "username": row[1],
                "display_name": row[2],
                "role": row[3],
                "is_active": bool(row[4]),
                "created_at": row[5],
            }
            for row in rows
        ]
    finally:
        connection.close()


def set_user_active(user_id: int, active: bool, acting_user_id: int) -> bool:
    connection = database._connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT role, is_active FROM app_users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            connection.rollback()
            return False
        if user_id == acting_user_id and not active:
            raise AuthenticationError("You cannot deactivate your own account.", 409)
        if user[0] == "admin" and user[1] and not active:
            other_admin = connection.execute(
                """
                SELECT 1 FROM app_users
                WHERE role = 'admin' AND is_active = 1 AND id != ? LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if not other_admin:
                raise AuthenticationError("The last active administrator cannot be deactivated.", 409)
        connection.execute(
            "UPDATE app_users SET is_active = ? WHERE id = ?",
            (int(active), user_id),
        )
        if not active:
            connection.execute("DELETE FROM app_sessions WHERE user_id = ?", (user_id,))
        connection.commit()
        return True
    except AuthenticationError:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_session(username: str, password: str) -> tuple[str, dict] | None:
    username = username.strip()
    expire_sessions()
    connection = database._connect()
    try:
        row = connection.execute(
            """
            SELECT id, username, display_name, password_hash, password_salt,
                   password_rounds, role, is_active
            FROM app_users WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()
        if row is None:
            _password_matches(password, "0" * 64, "0" * 32, PASSWORD_ROUNDS)
            return None
        if not row[7] or not _password_matches(password, row[3], row[4], row[5]):
            return None

        token = secrets.token_urlsafe(32)
        now = _utc_now()
        expires = now + SESSION_LIFETIME
        connection.execute(
            "INSERT INTO app_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode("ascii")).hexdigest(),
                row[0],
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        connection.commit()
        return token, {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "role": row[6],
        }
    finally:
        connection.close()


def get_session_user(token: str | None) -> dict | None:
    if not token or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
    connection = database._connect()
    try:
        row = connection.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role
            FROM app_sessions s JOIN app_users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1
            """,
            (token_hash, _utc_now().isoformat()),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "role": row[3],
        }
    finally:
        connection.close()


def revoke_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
    connection = database._connect()
    try:
        connection.execute("DELETE FROM app_sessions WHERE token_hash = ?", (token_hash,))
        connection.commit()
    finally:
        connection.close()


def expire_sessions() -> int:
    connection = database._connect()
    try:
        cursor = connection.execute(
            "DELETE FROM app_sessions WHERE expires_at <= ?", (_utc_now().isoformat(),)
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()
