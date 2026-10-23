"""FastAPI backend for the browser UI in ``web/static/``.

The API delegates PPE detection, persistence, video processing, and PDF generation
to the existing Python modules in ``app/``.

Run from the project root with:
    python -m uvicorn web.api:app --port 8000 --host 127.0.0.1

Authenticated sessions protect project APIs. The application is still intended
for trusted local use until deployment hardening is completed.
"""

import base64
import io
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent

import cv2
import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from app import auth, database, environment, report
from app.detector import MODEL_PATH, detect_ppe
from app.hazard_engine import detect_multi_hazards
from app.restricted_zone import detect_restricted_zone, validate_polygon
from app.sentinel import SiteSentinel, EVIDENCE_DIR
from app.video_detector import detect_ppe_video

logger = logging.getLogger(__name__)

# --- Folders ---------------------------------------------------------------
DATA_DIR = ROOT / "data"
DETECTIONS_DIR = DATA_DIR / "detections"
VIDEOS_DIR = DATA_DIR / "videos"
UPLOADS_DIR = DATA_DIR / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
for d in (DETECTIONS_DIR, VIDEOS_DIR, UPLOADS_DIR, EVIDENCE_DIR):
    d.mkdir(parents=True, exist_ok=True)

_sentinel = SiteSentinel()

# Make sure the database tables exist (safe to call every start). Tests can
# opt out of demo seeding so importing the app never changes the user's data.
database.init_db(seed_demo=os.environ.get("SITESENTINEL_DISABLE_DEMO_SEED") != "1")

app = FastAPI(title="SiteSentinel Project API", version="1.0")

SESSION_COOKIE_NAME = "sitesentinel_session"
_login_attempts = defaultdict(deque)
_login_attempt_lock = threading.Lock()
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_WINDOW_SECONDS = 15 * 60


class AccountRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=80)
    role: str = Field(default="viewer", max_length=20)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class UserStatusRequest(BaseModel):
    active: bool


def _request_is_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="strict",
        max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="strict",
        path="/",
    )


def _check_login_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    with _login_attempt_lock:
        attempts = _login_attempts[client_key]
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_ATTEMPT_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many sign-in attempts. Wait 15 minutes before trying again.",
            )
        attempts.append(now)


@app.middleware("http")
async def require_authenticated_api(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api/")
    is_sample_media = path.startswith("/sample_videos/")
    if not (is_api or is_sample_media):
        return await call_next(request)

    public_auth_paths = {
        "/api/auth/status",
        "/api/auth/setup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/health",
    }
    if is_api and path not in public_auth_paths:
        user = auth.get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Sign in to continue.", "code": "authentication_required"},
            )
        request.state.user = user
        if request.method not in {"GET", "HEAD", "OPTIONS"} and user["role"] == "viewer":
            return JSONResponse(
                status_code=403,
                content={"detail": "A supervisor or administrator account is required."},
            )
        if path.startswith("/api/auth/users") and user["role"] != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "Only administrators can manage accounts."},
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                expected_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
                forwarded_proto = request.headers.get("x-forwarded-proto")
                forwarded_host = request.headers.get("x-forwarded-host")
                if forwarded_proto and forwarded_host:
                    expected_origin = f"{forwarded_proto.split(',', 1)[0].strip()}://{forwarded_host.split(',', 1)[0].strip()}"
                if origin.rstrip("/") != expected_origin.rstrip("/"):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Cross-origin API requests are not allowed."},
                    )
    elif is_sample_media:
        user = auth.get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Sign in to access sample media.", "code": "authentication_required"},
            )

    return await call_next(request)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
INDIAN_STATES_AND_UTS = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Delhi", "Puducherry",
)
OPEN_METEO_ENDPOINTS = {
    "geocoding": "https://geocoding-api.open-meteo.com/v1/search",
    "air_quality": "https://air-quality-api.open-meteo.com/v1/air-quality",
    "weather": "https://api.open-meteo.com/v1/forecast",
}

# ---------------------------------------------------------------------------
# Task status helper
# ---------------------------------------------------------------------------
# Status colors match the app's validated light-mode palette (see css/styles.css:
# --good / --series-blue / --critical / --muted) so the Progress tracker's chart
# and pills read the same blue/green/red as the rest of the UI.
STATUS_COLORS = {
    "Completed": "#0ca30c",       # --good
    "In Progress": "#2a78d6",     # --series-blue
    "Behind Schedule": "#d03b3b", # --critical
    "Not Started": "#898781",     # --muted
}


def _task_status(progress, end_date):
    """A task past its planned end date is behind unless it is complete."""
    if progress >= 100:
        return "Completed"
    end = pd.to_datetime(end_date).date()
    if end < date.today():
        return "Behind Schedule"
    if progress > 0:
        return "In Progress"
    return "Not Started"


def _tasks_records():
    """Return tasks as a list of dicts with a computed 'status' field."""
    df = database.get_tasks()
    if df.empty:
        return []
    df = df.copy()
    df["status"] = df.apply(
        lambda r: _task_status(int(r["progress"]), r["end_date"]), axis=1
    )
    return df.to_dict(orient="records")


def _tasks_dataframe_with_status():
    """A DataFrame WITH a status column, for report.build_safety_report_pdf()."""
    df = database.get_tasks()
    if df.empty:
        return df
    df = df.copy()
    df["status"] = df.apply(
        lambda r: _task_status(int(r["progress"]), r["end_date"]), axis=1
    )
    return df


def _safe_child(base: Path, name: str) -> Path:
    """Resolve base/name and refuse anything that escapes base (path traversal)."""
    target = (base / name).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise HTTPException(status_code=400, detail="Invalid path.")
    return target


def _safe_upload_name(filename: str | None) -> str:
    """Keep a harmless display name; never use an upload name as a disk path."""
    leaf = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", leaf).strip("._")
    return cleaned[:120] or "upload"


def _remove_generated(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove generated temporary file: %s", path, exc_info=True)


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="The uploaded file is too large.")
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return raw


def _validated_confidence(conf: float) -> float:
    if not math.isfinite(conf) or not 0 <= conf <= 1:
        raise HTTPException(status_code=422, detail="Confidence must be between 0 and 1.")
    return conf


def _validated_task_fields(name, start_date, end_date, progress):
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Task name is required.")
    if len(name) > 120:
        raise HTTPException(status_code=422, detail="Task name must be 120 characters or fewer.")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Task dates must use YYYY-MM-DD format.")
    if end < start:
        raise HTTPException(status_code=422, detail="Planned end date must be on or after the start date.")
    if not 0 <= progress <= 100:
        raise HTTPException(status_code=422, detail="Progress must be between 0 and 100.")
    return name, start.isoformat(), end.isoformat(), progress


def _validated_ticket_fields(hazard_type, severity, zone_name, description, supervisor_notes):
    hazard_type = (hazard_type or "").strip().upper()
    severity = (severity or "").strip().upper()
    zone_name = (zone_name or "").strip()
    description = (description or "").strip()
    supervisor_notes = (supervisor_notes or "").strip()
    if not hazard_type or len(hazard_type) > 80:
        raise HTTPException(status_code=422, detail="Hazard type is required and must be 80 characters or fewer.")
    if severity not in {"LOW", "MODERATE", "HIGH", "CRITICAL", "CATASTROPHIC"}:
        raise HTTPException(status_code=422, detail="Choose a supported incident severity.")
    if not zone_name or len(zone_name) > 120:
        raise HTTPException(status_code=422, detail="Zone or location is required and must be 120 characters or fewer.")
    if not description or len(description) > 1000:
        raise HTTPException(status_code=422, detail="Description is required and must be 1000 characters or fewer.")
    if len(supervisor_notes) > 2000:
        raise HTTPException(status_code=422, detail="Supervisor notes must be 2000 characters or fewer.")
    return hazard_type, severity, zone_name, description, supervisor_notes


# ===========================================================================
# STATS & INSPECTIONS
# ===========================================================================
@app.get("/api/auth/status")
def api_auth_status(request: Request):
    user = auth.get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    return {
        "configured": auth.user_count() > 0,
        "authenticated": user is not None,
        "user": user,
        "setup_token_required": bool(os.environ.get("SITESENTINEL_SETUP_TOKEN")),
    }


@app.post("/api/auth/setup")
def api_auth_setup(payload: AccountRequest, request: Request, response: Response):
    client_key = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_key)
    configured_token = os.environ.get("SITESENTINEL_SETUP_TOKEN")
    supplied_token = request.headers.get("x-setup-token", "")
    if configured_token and not secrets.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=403, detail="A valid initial setup token is required.")
    try:
        auth.create_first_admin(
            payload.username,
            payload.display_name or payload.username,
            payload.password,
        )
    except auth.AuthenticationError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))
    authenticated = auth.create_session(payload.username, payload.password)
    if authenticated is None:
        raise HTTPException(status_code=500, detail="The administrator account could not be signed in.")
    token, user = authenticated
    with _login_attempt_lock:
        _login_attempts.pop(client_key, None)
    _set_session_cookie(response, request, token)
    return {"user": user}


@app.post("/api/auth/login")
def api_auth_login(payload: LoginRequest, request: Request, response: Response):
    client_key = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_key)
    authenticated = auth.create_session(payload.username, payload.password)
    if authenticated is None:
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    with _login_attempt_lock:
        _login_attempts.pop(client_key, None)
    token, user = authenticated
    _set_session_cookie(response, request, token)
    return {"user": user}


@app.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response):
    auth.revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    _clear_session_cookie(response, request)
    return {"ok": True}


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    user = auth.get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return {"user": user}


@app.get("/api/auth/users")
def api_auth_list_users():
    return {"users": auth.list_users()}


@app.post("/api/auth/users")
def api_auth_create_user(payload: AccountRequest):
    try:
        user = auth.create_user(
            payload.username,
            payload.display_name or payload.username,
            payload.password,
            payload.role,
        )
    except auth.AuthenticationError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))
    return {"user": user}


@app.patch("/api/auth/users/{user_id}")
def api_auth_update_user_status(
    user_id: int,
    payload: UserStatusRequest,
    request: Request,
):
    try:
        updated = auth.set_user_active(
            user_id,
            payload.active,
            request.state.user["id"],
        )
    except auth.AuthenticationError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))
    if not updated:
        raise HTTPException(status_code=404, detail="User account not found.")
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    return database.get_stats()


@app.get("/api/health")
def api_health():
    try:
        database.get_stats()
    except Exception:
        logger.exception("Database health check failed.")
        raise HTTPException(status_code=503, detail="The database is unavailable.")
    if not MODEL_PATH.is_file():
        raise HTTPException(status_code=503, detail="The PPE model file is missing.")
    return {"status": "ok", "database": "ok", "model_file": "ok"}


@app.post("/api/attendance/check-in")
async def api_attendance_check_in(
    employee_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Run an in-memory PPE check and persist only the employee ID and text findings."""
    employee_id = employee_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", employee_id):
        raise HTTPException(
            status_code=422,
            detail="Employee ID must be 1–32 letters, numbers, hyphens, or underscores.",
        )
    try:
        suffix = Path(_safe_upload_name(file.filename)).suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image.")
        raw = await _read_upload(file, MAX_IMAGE_BYTES)
    finally:
        await file.close()
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="The image dimensions are too large.")
            image = image.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a valid image.")

    try:
        annotated, summary = detect_multi_hazards(image)
    except Exception:
        logger.exception("Attendance PPE screening failed.")
        raise HTTPException(status_code=503, detail="PPE screening failed. Check the model files and server log.")

    ppe = summary.get("ppe", {})
    findings = []
    inconclusive = False
    if ppe.get("helmets_missing", 0):
        findings.append("Model flagged a missing hardhat; supervisor review required.")
    elif not ppe.get("helmets_ok", 0):
        findings.append("Hardhat not conclusively detected; manual check required.")
        inconclusive = True

    if ppe.get("vests_missing", 0):
        findings.append("Model flagged a missing safety vest; supervisor review required.")
    elif not ppe.get("vests_ok", 0):
        findings.append("Safety vest not conclusively detected; manual check required.")
        inconclusive = True

    flagged = bool(ppe.get("helmets_missing", 0) or ppe.get("vests_missing", 0))
    ppe_status = "REVIEW REQUIRED" if flagged or inconclusive else "NO FLAG — VERIFY"
    findings_text = " ".join(findings) if findings else (
        "No missing PPE was flagged; supervisor must verify the worker in person."
    )
    try:
        record = database.record_attendance(employee_id, ppe_status, findings_text)
    except Exception:
        logger.exception("Could not save the attendance check-in.")
        raise HTTPException(status_code=500, detail="The attendance record could not be saved.")

    output = io.BytesIO()
    annotated.save(output, format="JPEG", quality=85)
    annotated_data = base64.b64encode(output.getvalue()).decode("ascii")
    return {
        "record": record,
        "annotated_image": f"data:image/jpeg;base64,{annotated_data}",
        "photo_retained": False,
        "human_review_required": True,
    }


@app.get("/api/attendance")
def api_attendance_records():
    return {"records": database.get_attendance_records()}


@app.post("/api/attendance/confirm-ticket")
def api_confirm_attendance_ticket(
    employee_id: str = Form(...),
    check_date: str = Form(...),
    confirmed_in_person: bool = Form(...),
    confirmed_by: str = Form(...),
):
    """Log a PPE follow-up only after an in-person supervisor confirmation."""
    employee_id = employee_id.strip()
    confirmed_by = confirmed_by.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", employee_id):
        raise HTTPException(status_code=422, detail="Employee ID is invalid.")
    if not confirmed_in_person:
        raise HTTPException(status_code=422, detail="In-person supervisor confirmation is required.")
    try:
        if date.fromisoformat(check_date).isoformat() != check_date:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Check-in date must use YYYY-MM-DD format.")
    if not confirmed_by or len(confirmed_by) > 80 or any(ord(char) < 32 for char in confirmed_by):
        raise HTTPException(status_code=422, detail="Supervisor name must be 1–80 printable characters.")
    try:
        ticket_id, created = database.confirm_attendance_incident(
            employee_id, check_date, confirmed_by
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception("Could not create a supervisor-confirmed attendance ticket.")
        raise HTTPException(status_code=500, detail="The incident ticket could not be created.")
    return {"ok": True, "ticket_id": ticket_id, "created": created}


def _fetch_open_meteo_json(service: str, params: dict) -> dict:
    endpoint = OPEN_METEO_ENDPOINTS[service]
    request = Request(
        f"{endpoint}?{urlencode(params)}",
        headers={"User-Agent": "SiteSentinel/1.0 (local environmental dashboard)"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read(1_000_001)
    except (URLError, TimeoutError):
        logger.warning("Open-Meteo %s request failed.", service, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"The {service.replace('_', ' ')} provider is temporarily unavailable.",
        )
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=502, detail="The environmental provider response was too large.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Open-Meteo %s returned invalid JSON.", service)
        raise HTTPException(status_code=502, detail="The environmental provider returned invalid data.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="The environmental provider returned an invalid response.")
    if payload.get("error"):
        logger.warning("Open-Meteo %s rejected the request: %s", service, payload.get("reason"))
        raise HTTPException(status_code=502, detail="The environmental provider rejected the request.")
    return payload


def _normalise_region_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    if normalized.startswith("national capital territory of "):
        normalized = normalized.removeprefix("national capital territory of ")
    if normalized.startswith("nct of "):
        normalized = normalized.removeprefix("nct of ")
    return normalized


@app.get("/api/environment/states")
def api_environment_states():
    return {"states": list(INDIAN_STATES_AND_UTS)}


@app.get("/api/environment/locations")
def api_environment_locations(state: str, query: str):
    state = state.strip()
    query = query.strip()
    if state not in INDIAN_STATES_AND_UTS:
        raise HTTPException(status_code=422, detail="Choose a valid Indian state or union territory.")
    if len(query) < 2 or len(query) > 80 or any(ord(char) < 32 for char in query):
        raise HTTPException(status_code=422, detail="Enter a district or city name (2–80 characters).")
    payload = _fetch_open_meteo_json("geocoding", {
        "name": query,
        "count": 20,
        "language": "en",
        "format": "json",
    })
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise HTTPException(status_code=502, detail="The geocoding provider returned invalid results.")
    selected_state = _normalise_region_name(state)
    locations = []
    for result in results:
        if not isinstance(result, dict) or result.get("country_code") != "IN":
            continue
        result_state = result.get("admin1")
        if not isinstance(result_state, str) or _normalise_region_name(result_state) != selected_state:
            continue
        name = result.get("name")
        latitude = result.get("latitude")
        longitude = result.get("longitude")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 100
            or not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
        ):
            continue
        district = result.get("admin2")
        if not isinstance(district, str) or not district.strip() or len(district) > 100:
            district = name
        locations.append({
            "name": name,
            "district": district,
            "state": state,
            "latitude": latitude,
            "longitude": longitude,
        })
    return {"locations": locations}


def _provider_number(payload: dict, key: str, *, required: bool = True):
    value = payload.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HTTPException(status_code=502, detail="The environmental provider returned incomplete data.")
    return float(value)


def _environment_category(index):
    if index is None:
        return "Unavailable"
    if index <= 50:
        return "Good"
    if index <= 100:
        return "Moderate"
    if index <= 150:
        return "Unhealthy for sensitive groups"
    if index <= 200:
        return "Unhealthy"
    if index <= 300:
        return "Very unhealthy"
    return "Hazardous"


@app.get("/api/environment/current")
def api_environment_current(
    state: str,
    district: str,
    name: str,
    latitude: float,
    longitude: float,
    activity: str = "typical",
    mitigation: str = "basic",
):
    state = state.strip()
    district = district.strip()
    name = name.strip()
    if state not in INDIAN_STATES_AND_UTS:
        raise HTTPException(status_code=422, detail="Choose a valid Indian state or union territory.")
    if not district or len(district) > 100 or not name or len(name) > 100:
        raise HTTPException(status_code=422, detail="The selected district or city is invalid.")
    if not (6.0 <= latitude <= 38.0 and 68.0 <= longitude <= 98.0):
        raise HTTPException(status_code=422, detail="Choose a location within India.")
    try:
        environment.validate_site_scenario_options(activity, mitigation)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    coordinates = {"latitude": latitude, "longitude": longitude}
    air = _fetch_open_meteo_json("air_quality", {
        **coordinates,
        "current": "pm2_5,pm10,us_aqi",
        "hourly": "pm2_5,pm10,us_aqi",
        "forecast_days": 2,
        "timezone": "auto",
    })
    weather = _fetch_open_meteo_json("weather", {
        **coordinates,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
        "forecast_days": 3,
        "timezone": "auto",
    })
    air_current = air.get("current")
    weather_current = weather.get("current")
    if not isinstance(air_current, dict) or not isinstance(weather_current, dict):
        raise HTTPException(status_code=502, detail="The environmental provider returned incomplete current conditions.")
    pm25 = _provider_number(air_current, "pm2_5")
    pm10 = _provider_number(air_current, "pm10")
    us_aqi = _provider_number(air_current, "us_aqi", required=False)
    timestamp = air_current.get("time")
    if not isinstance(timestamp, str):
        raise HTTPException(status_code=502, detail="The environmental provider omitted its update time.")

    hourly = air.get("hourly", {})
    hourly_times = hourly.get("time", []) if isinstance(hourly, dict) else []
    hourly_pm25 = hourly.get("pm2_5", []) if isinstance(hourly, dict) else []
    hourly_pm10 = hourly.get("pm10", []) if isinstance(hourly, dict) else []
    if not all(isinstance(values, list) for values in (hourly_times, hourly_pm25, hourly_pm10)):
        raise HTTPException(status_code=502, detail="The environmental provider returned invalid hourly data.")
    current_hour = timestamp[:13]
    trend = []
    for index, time_value in enumerate(hourly_times):
        if (
            isinstance(time_value, str)
            and time_value[:13] >= current_hour
            and index < len(hourly_pm25)
            and index < len(hourly_pm10)
            and isinstance(hourly_pm25[index], (int, float))
            and isinstance(hourly_pm10[index], (int, float))
            and math.isfinite(hourly_pm25[index])
            and math.isfinite(hourly_pm10[index])
        ):
            trend.append({
                "time": time_value,
                "pm25": hourly_pm25[index],
                "pm10": hourly_pm10[index],
            })
        if len(trend) == 12:
            break

    weather_values = {
        key: _provider_number(weather_current, key, required=False)
        for key in (
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "precipitation",
            "weather_code",
            "wind_speed_10m",
        )
    }
    if any(value is None for value in weather_values.values()):
        raise HTTPException(status_code=502, detail="The weather provider returned incomplete current conditions.")
    try:
        scenario = environment.construction_site_scenario(pm25, pm10, activity, mitigation)
    except ValueError as error:
        raise HTTPException(status_code=502, detail="The environmental provider returned invalid particulate values.") from error

    daily = weather.get("daily", {})
    forecast = []
    if isinstance(daily, dict):
        dates = daily.get("time", [])
        maxima = daily.get("temperature_2m_max", [])
        minima = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])
        codes = daily.get("weather_code", [])
        if all(isinstance(values, list) for values in (dates, maxima, minima, precip, codes)):
            for index, day in enumerate(dates[:3]):
                if all(index < len(values) for values in (maxima, minima, precip, codes)):
                    forecast.append({
                        "date": day,
                        "high": maxima[index],
                        "low": minima[index],
                        "precipitation": precip[index],
                        "weather_code": codes[index],
                    })

    return {
        "location": {"name": name, "district": district, "state": state},
        "source": "Open-Meteo CAMS global atmospheric composition model",
        "source_url": "https://open-meteo.com/",
        "attribution": "Weather and air-quality data by Open-Meteo; air quality is a regional model estimate, not a CPCB station observation.",
        "model_resolution": "Approximately 45 km; not district- or construction-site sensor data.",
        "updated_at": timestamp,
        "timezone": air.get("timezone"),
        "utc_offset_seconds": air.get("utc_offset_seconds"),
        "air_quality": {
            "standard": "US AQI",
            "index": us_aqi,
            "category": _environment_category(us_aqi),
            "pm25": pm25,
            "pm10": pm10,
            "units": air.get("current_units", {}),
        },
        "weather": {
            **weather_values,
            "time": weather_current.get("time"),
            "units": weather.get("current_units", {}),
            "forecast": forecast,
        },
        "trend": trend,
        "construction_scenario": scenario,
        "scenario_assumptions": {
            "pm_additions_before_controls": {
                "light": {"pm25": 4, "pm10": 10},
                "typical": {"pm25": 12, "pm10": 35},
                "dust_intensive": {"pm25": 30, "pm10": 90},
            },
            "control_efficiency": {"none": 0, "basic": 0.35, "strong": 0.60},
        },
    }


@app.get("/api/environment/scenario")
def api_environment_scenario(
    pm25: float,
    pm10: float,
    activity: str = "typical",
    mitigation: str = "basic",
):
    try:
        return environment.construction_site_scenario(pm25, pm10, activity, mitigation)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/api/pre-shift")
def api_get_pre_shift():
    record = database.get_pre_shift_check()
    if record is None:
        return {"checklist": None, "completed_items": 0, "total_items": 5}
    item_keys = (
        "headcount_ready",
        "toolbox_talk",
        "ppe_inspected",
        "equipment_checked",
        "access_clear",
    )
    completed = sum(bool(record[key]) for key in item_keys)
    return {
        "checklist": record,
        "completed_items": completed,
        "total_items": len(item_keys),
    }


@app.post("/api/pre-shift")
def api_save_pre_shift(
    check_date: str = Form(...),
    headcount_ready: bool = Form(False),
    toolbox_talk: bool = Form(False),
    ppe_inspected: bool = Form(False),
    equipment_checked: bool = Form(False),
    access_clear: bool = Form(False),
    checked_by: str = Form(""),
):
    try:
        check_date = date.fromisoformat(check_date).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Checklist date must use YYYY-MM-DD format.")
    checked_by = checked_by.strip()
    if len(checked_by) > 32 or (checked_by and not re.fullmatch(r"[A-Za-z0-9_-]+", checked_by)):
        raise HTTPException(status_code=422, detail="Checked-by ID must be 1–32 letters, numbers, hyphens, or underscores.")
    try:
        record = database.save_pre_shift_check(
            check_date,
            headcount_ready,
            toolbox_talk,
            ppe_inspected,
            equipment_checked,
            access_clear,
            checked_by,
        )
    except Exception:
        logger.exception("Could not save the pre-shift checklist.")
        raise HTTPException(status_code=500, detail="The pre-shift checklist could not be saved.")
    return {"checklist": record}


@app.get("/api/inspections")
def api_inspections():
    df = database.get_inspections()
    if df.empty:
        return []
    # Expose just the file name for the image so the frontend can build a URL.
    records = df.astype(object).where(pd.notna(df), None).to_dict(orient="records")
    for r in records:
        p = r.get("image_path") or ""
        r["image_file"] = Path(p).name if p else ""
    return records


@app.get("/api/detections/{name}")
def api_detection_image(name: str):
    target = _safe_child(DETECTIONS_DIR, name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(str(target))


# ===========================================================================
# PHOTO DETECTION
# ===========================================================================
@app.post("/api/detect")
async def api_detect(
    file: UploadFile = File(...),
    conf: float = Form(0.35),
    save: bool = Form(False),
):
    suffix = Path(_safe_upload_name(file.filename)).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image.")
    raw = await _read_upload(file, MAX_IMAGE_BYTES)
    conf = _validated_confidence(conf)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="The image dimensions are too large.")
            image = source.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a valid image.")

    try:
        annotated, summary = detect_ppe(image, conf=conf)
    except Exception:
        logger.exception("PPE image detection failed.")
        raise HTTPException(
            status_code=503,
            detail="PPE detection failed. Check the model file and server log.",
        )

    # Encode the annotated image as a base64 data URL for the browser.
    buf = io.BytesIO()
    annotated.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    saved_file = ""
    if save:
        saved_file = f"detection_{uuid4().hex}.jpg"
        saved_path = DETECTIONS_DIR / saved_file
        try:
            annotated.save(str(saved_path), format="JPEG", quality=90)
            database.add_inspection(
                _safe_upload_name(file.filename),
                summary["total"],
                summary["helmet_ok"],
                summary["violations"],
                summary["compliance"],
                str(saved_path),
            )
        except Exception:
            _remove_generated(saved_path)
            logger.exception("Could not persist the PPE inspection.")
            raise HTTPException(status_code=500, detail="The inspection could not be saved.")

    return {
        "summary": summary,
        "annotated_image": f"data:image/jpeg;base64,{b64}",
        "saved": bool(save),
        "saved_file": saved_file,
    }


@app.post("/api/detect-zone")
async def api_detect_zone(
    file: UploadFile = File(...),
    polygon: str = Form(...),
    conf: float = Form(0.35),
):
    """Analyze a user-drawn zone; this result is returned but not persisted."""
    suffix = Path(_safe_upload_name(file.filename)).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image.")
    raw = await _read_upload(file, MAX_IMAGE_BYTES)
    conf = _validated_confidence(conf)
    if len(polygon) > 20_000:
        raise HTTPException(status_code=422, detail="Zone coordinates exceed the supported size.")
    try:
        points = json.loads(polygon)
    except (json.JSONDecodeError, TypeError, RecursionError):
        raise HTTPException(status_code=422, detail="Zone coordinates must be valid JSON.")

    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="The image dimensions are too large.")
            image = image.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a valid image.")

    try:
        points = validate_polygon(points, image.width, image.height)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    try:
        annotated, summary = detect_restricted_zone(image, points, conf=conf)
    except FileNotFoundError:
        logger.exception("The person-detection model is unavailable.")
        raise HTTPException(
            status_code=503,
            detail="Person detection is unavailable: models/yolov8n.pt is missing.",
        )
    except Exception:
        logger.exception("Restricted-zone person detection failed.")
        raise HTTPException(
            status_code=503,
            detail="Restricted-zone detection failed. Check the person model and server log.",
        )

    buf = io.BytesIO()
    annotated.save(buf, format="JPEG", quality=90)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "summary": summary,
        "annotated_image": f"data:image/jpeg;base64,{encoded}",
        "saved": False,
    }


# ===========================================================================
# VIDEO DETECTION  (the new AI feature)
# ===========================================================================
@app.post("/api/detect-video")
async def api_detect_video(
    file: UploadFile = File(...),
    conf: float = Form(0.35),
    sample_every: int = Form(5),
):
    suffix = Path(_safe_upload_name(file.filename)).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload an MP4, MOV, AVI, MKV, or WebM video.")
    raw = await _read_upload(file, MAX_VIDEO_BYTES)
    conf = _validated_confidence(conf)
    if not 1 <= sample_every <= 15:
        raise HTTPException(status_code=422, detail="Frame sampling interval must be between 1 and 15.")

    token = uuid4().hex
    in_path = UPLOADS_DIR / f"upload_{token}{suffix}"
    out_name = f"annotated_{token}.mp4"
    out_path = VIDEOS_DIR / out_name

    try:
        in_path.write_bytes(raw)
        summary = detect_ppe_video(in_path, out_path, conf=conf, sample_every=sample_every)
    except ValueError:
        _remove_generated(out_path)
        raise HTTPException(
            status_code=422,
            detail="The video is invalid or has no analysable frames. Check the format and duration.",
        )
    except Exception:
        _remove_generated(out_path)
        logger.exception("PPE video processing failed.")
        raise HTTPException(
            status_code=503,
            detail="Video processing failed. Check the video codec, model file, and server log.",
        )
    finally:
        _remove_generated(in_path)

    return {"summary": summary, "video_url": f"/api/videos/{out_name}"}


@app.get("/api/videos/{name}")
def api_video(name: str):
    target = _safe_child(VIDEOS_DIR, name)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Video not found.")
    return FileResponse(str(target), media_type="video/mp4")


# ===========================================================================
# TASKS  (Progress Tracker)
# ===========================================================================
@app.get("/api/tasks")
def api_tasks():
    return {"tasks": _tasks_records(), "status_colors": STATUS_COLORS}


@app.post("/api/tasks")
async def api_add_task(
    name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    progress: int = Form(0),
):
    values = _validated_task_fields(name, start_date, end_date, progress)
    task_id = database.add_task(*values)
    return {"ok": True, "id": task_id}


@app.patch("/api/tasks/{task_id}")
async def api_update_task(
    task_id: int,
    name: str | None = Form(None),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    progress: int | None = Form(None),
):
    current = database.get_task(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if all(value is None for value in (name, start_date, end_date, progress)):
        raise HTTPException(status_code=422, detail="Provide at least one task field to update.")
    values = _validated_task_fields(
        current["name"] if name is None else name,
        current["start_date"] if start_date is None else start_date,
        current["end_date"] if end_date is None else end_date,
        current["progress"] if progress is None else progress,
    )
    if not database.update_task(task_id, *values):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int):
    if not database.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"ok": True}


@app.post("/api/tasks/load-sample")
def api_load_sample_tasks():
    if not database.get_tasks().empty:
        raise HTTPException(status_code=409, detail="Sample tasks can only be loaded into an empty task list.")
    today = date.today()
    samples = [
        ("Site Preparation",   -40, -25, 100),
        ("Foundation Work",    -25,  -5, 100),
        ("Structural Framing", -10,  10,  60),
        ("Plumbing",            -8,  -1,  40),
        ("Electrical Wiring",   -3,  20,  25),
        ("Roofing",             12,  30,   0),
        ("Interior Finishing",  25,  50,   0),
    ]
    for nm, s_off, e_off, pr in samples:
        database.add_task(
            nm,
            (today + timedelta(days=s_off)).isoformat(),
            (today + timedelta(days=e_off)).isoformat(),
            pr,
        )
    return {"ok": True, "added": len(samples)}


@app.delete("/api/tasks")
def api_clear_tasks():
    database.clear_tasks()
    return {"ok": True}


# ===========================================================================
# REPORT (PDF)
# ===========================================================================
@app.get("/api/report.pdf")
def api_report(include_images: bool = True):
    stats = database.get_stats()
    df = database.get_inspections()
    tasks = _tasks_dataframe_with_status()
    tickets_df = database.get_tickets()
    risk_index = database.get_site_risk_index()
    zones = database.get_zones()
    pdf_bytes = report.build_safety_report_pdf(
        stats,
        df,
        tasks,
        include_images=include_images,
        tickets=tickets_df,
        risk_index=risk_index,
        zones=zones,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="construction_safety_report_{date.today().isoformat()}.pdf"'
        },
    )


# ===========================================================================
# Browser-driven frame review and multi-hazard endpoint
# ===========================================================================
@app.post("/api/sentinel/process-frame")
async def api_sentinel_process_frame(
    file: UploadFile = File(...),
    conf: float = Form(0.35),
    save_evidence: bool = Form(True),
):
    """
    Process one frame sent by the browser's sample-clip or local-webcam review.
    Applies persistent worker tracking, N-of-M temporal de-bouncing,
    and automatic evidence snapshotting.
    """
    raw = await _read_upload(file, MAX_IMAGE_BYTES)
    conf = _validated_confidence(conf)
    try:
        nparr = np.frombuffer(raw, np.uint8)
        frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise ValueError("Could not decode image frame.")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid frame image.")

    annotated_bgr, telemetry = _sentinel.process_frame(
        frame_bgr, conf=conf, save_evidence=save_evidence
    )

    # Encode annotated frame to JPEG base64 for browser stream display
    ok, buf = cv2.imencode(".jpg", annotated_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Frame encoding failed.")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    return {
        "annotated_frame": f"data:image/jpeg;base64,{b64}",
        "telemetry": telemetry,
    }


@app.post("/api/detect-multi")
async def api_detect_multi(
    file: UploadFile = File(...),
    conf: float = Form(0.35),
    fire_conf: float = Form(0.30),
):
    """
    Full Multi-Hazard Inspection on an uploaded photo:
    Dual-PPE (Helmet + Vest), Fall Detection, Fire & Smoke, and Machinery Proximity.
    """
    suffix = Path(_safe_upload_name(file.filename)).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image.")
    raw = await _read_upload(file, MAX_IMAGE_BYTES)
    conf = _validated_confidence(conf)
    fire_conf = _validated_confidence(fire_conf)

    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="The image dimensions are too large.")
            image = source.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a valid image.")

    try:
        annotated, summary = detect_multi_hazards(image, ppe_conf=conf, fire_conf=fire_conf)
    except Exception:
        logger.exception("Multi-hazard detection failed.")
        raise HTTPException(status_code=503, detail="Multi-hazard detection failed.")

    buf = io.BytesIO()
    annotated.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "summary": summary,
        "annotated_image": f"data:image/jpeg;base64,{b64}",
    }


@app.get("/api/evidence")
def api_get_evidence():
    """List auto-captured forensic evidence snapshots."""
    files = sorted(EVIDENCE_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    records = []
    for f in files[:50]:  # Return latest 50
        records.append({
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"/api/evidence/{f.name}",
        })
    return {"evidence": records}


@app.get("/api/evidence/{name}")
def api_evidence_file(name: str):
    """Serve a forensic evidence snapshot securely."""
    target = _safe_child(EVIDENCE_DIR, name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Evidence snapshot not found.")
    return FileResponse(str(target))


# ===========================================================================
# Incident tickets and project risk indicator
# ===========================================================================
@app.get("/api/tickets")
def api_get_tickets(status: str | None = None):
    """List all incident tickets, optionally filtered by status."""
    df = database.get_tickets(status=status)
    if df.empty:
        return {"tickets": []}
    return {"tickets": df.to_dict(orient="records")}


@app.post("/api/tickets")
async def api_create_ticket(
    hazard_type: str = Form(...),
    severity: str = Form(...),
    zone_name: str = Form(...),
    description: str = Form(...),
    evidence_upload: UploadFile | None = File(None),
    worker_id: int | None = Form(None),
    supervisor_notes: str = Form(""),
):
    """Log a reviewed project incident, optionally retaining a validated image."""
    hazard_type, severity, zone_name, description, supervisor_notes = _validated_ticket_fields(
        hazard_type, severity, zone_name, description, supervisor_notes
    )
    if worker_id is not None and worker_id < 0:
        raise HTTPException(status_code=422, detail="Worker ID must be zero or greater.")

    evidence_path = None
    evidence_name = ""
    evidence_image = None
    if evidence_upload is not None:
        try:
            suffix = Path(_safe_upload_name(evidence_upload.filename)).suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise HTTPException(status_code=415, detail="Evidence must be a JPG, PNG, or WebP image.")
            raw = await _read_upload(evidence_upload, MAX_IMAGE_BYTES)
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    source.verify()
                with Image.open(io.BytesIO(raw)) as source:
                    if source.width * source.height > MAX_IMAGE_PIXELS:
                        raise HTTPException(status_code=413, detail="Evidence image dimensions are too large.")
                    evidence_image = ImageOps.exif_transpose(source).convert("RGB")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=400, detail="Evidence is not a valid image.")
        finally:
            await evidence_upload.close()

        evidence_name = f"ticket_{uuid4().hex}.jpg"
        evidence_path = EVIDENCE_DIR / evidence_name

    try:
        if evidence_image is not None:
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_image.save(evidence_path, format="JPEG", quality=90)
        ticket_id = database.create_ticket(
            hazard_type=hazard_type,
            severity=severity,
            zone_name=zone_name,
            description=description,
            evidence_file=evidence_name,
            worker_id=worker_id,
            supervisor_notes=supervisor_notes,
        )
    except Exception:
        if evidence_path is not None:
            _remove_generated(evidence_path)
        logger.exception("Could not create the incident ticket.")
        raise HTTPException(status_code=500, detail="The incident ticket could not be created.")
    return {"ok": True, "ticket_id": ticket_id}


@app.patch("/api/tickets/{ticket_id}")
async def api_update_ticket(
    ticket_id: str,
    status: str = Form(...),
    supervisor_notes: str | None = Form(None),
):
    """Update ticket resolution status (OPEN -> INVESTIGATING -> REMEDIATED)."""
    try:
        updated = database.update_ticket_status(ticket_id, status, supervisor_notes)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    if not updated:
        raise HTTPException(status_code=404, detail="Incident ticket not found.")
    return {"ok": True}


@app.delete("/api/tickets/{ticket_id}")
def api_delete_ticket(ticket_id: str):
    """Delete an incident ticket."""
    if not database.delete_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Incident ticket not found.")
    return {"ok": True}


@app.get("/api/risk-index")
def api_get_risk_index():
    """Get the project-defined risk indicator based on active tickets."""
    return database.get_site_risk_index()


@app.get("/api/zones")
def api_get_zones():
    """Get pre-configured site danger zones."""
    return {"zones": database.get_zones()}


SAMPLE_VIDEOS_DIR = ROOT / "sample_videos"
if SAMPLE_VIDEOS_DIR.is_dir():
    app.mount("/sample_videos", StaticFiles(directory=str(SAMPLE_VIDEOS_DIR)), name="sample_videos")

# ===========================================================================
# STATIC FRONTEND  (mounted LAST so /api/* routes win)
# ===========================================================================
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
