# SiteSentinel Project Report

**Updated:** 2026-10-24

## 1. What is this project?

SiteSentinel is a browser-based construction project and safety review application. It helps a user review possible safety issues, record incidents, check workers in, track tasks, and view project information.

It is a local student project. It does not replace a site supervisor, certify safety, or monitor a site continuously.

## 2. How does it work?

1. Open the app in a browser and sign in.
2. Select a workflow, such as photo review, check-in, or incident follow-up.
3. The browser sends a request to the FastAPI backend.
4. Python modules process the request, run local AI models when needed, and save records in SQLite.
5. The browser displays the result. A person reviews AI findings before recording or acting on them.

## 3. Main features

| Feature | Simple explanation |
|---|---|
| Safety review | Looks for possible PPE, fire/smoke, or machinery hazards in images; video is checked using sampled frames. |
| Restricted-zone check | User draws a zone on a photo; the app checks detected people against it. |
| Incident tickets | Record a concern, add notes/evidence, and update its status. AI does not create a ticket automatically. |
| Worker check-in | Screens a supplied/captured image for PPE. The check-in photo is not stored; this is not identity verification. |
| Task and pre-shift tools | Track construction task progress and a daily readiness checklist. |
| Site map | Shows configured zones on an example layout. It is not a surveyed drawing or live site map. |
| Air quality and weather | Shows regional Open-Meteo model information for a selected state and district/city. A separate what-if estimates a construction activity scenario. Neither is an onsite measurement. |
| Accounts | Role-based sign-in and registration for administrators, supervisors, visitors, and workers. |
| Worker dashboard | Shows today's check-in state, personal attendance history, AQI/weather, administrator-configured emergency and toolbox-talk information, safety reminders, and a worker hazard/near-miss/injury report form. Reports appear in the supervisor incident list. |
| PDF report | Exports a summary of saved project information. It is not an official audit or certificate. |

## 4. User accounts and passwords

- **First setup:** On a new database, the first person creates the administrator account. An optional `SITESENTINEL_SETUP_TOKEN` can protect this first setup.
- **Administrator:** Can use project features and create/manage accounts.
- **Supervisor:** Can use project features but cannot manage accounts.
- **Visitor (viewer):** Can self-register with a unique username and password; read-only API access.
- **Worker:** Signs in with a unique `EMP` employee ID and password. Can view regional environmental information and their own attendance only; worker API requests cannot read project-wide records or manage project tools.
- **Worker registration:** Requires the site invitation code configured in `SITESENTINEL_WORKER_INVITE_CODE`. IDs such as `EMP001` are unique and remain reserved when an account is disabled. The code must be distributed privately; it does not independently verify a worker's identity or prevent a code-holder from claiming an unassigned ID.
- **Worker privacy:** Workers can read only their own attendance, today's check-in, environmental information, and the site notice/emergency information. They can send hazard reports to the incident list but cannot read incident tickets or other workers' records.
- **Site information:** Administrators configure emergency contact, first-aid point, assembly point, and toolbox-talk/site notice in Accounts. Values start blank; the app does not invent emergency numbers or locations.
- **Supervisor/administrator registration:** Not open to public self-registration. An existing administrator provisions these accounts; the first administrator is created only during initial setup.
- **Account setup:** Username must be at least 5 characters; password at least 6. Sign-in allows 5 attempts per minute.
- **Password storage:** The app stores a salted PBKDF2-HMAC-SHA256 hash, not the original password.
- **Session:** Expires after 8 hours. The browser uses an HttpOnly session cookie.
- **Not yet available:** Password change/reset, MFA, individual per-worker invitation codes, and audit history.

The app should remain bound to `127.0.0.1`. This authentication is not sufficient preparation for public hosting.

## 5. Technology used

| Technology | What it does here |
|---|---|
| Python | Backend logic, data handling, AI workflow, and tests. |
| FastAPI + Uvicorn | Provides the web server and REST API routes. |
| SQLite | Stores local records, users, and sessions in `data/records.db`. |
| HTML, CSS, JavaScript | Builds the browser interface and interactive features. |
| Tailwind CSS | Helps build the interface styles; compiled before app runtime. |
| Chart.js | Draws dashboard charts. |
| Lucide | Supplies interface icons. |
| Ultralytics YOLO + PyTorch | Loads local computer-vision models and runs detections. |
| OpenCV, Pillow, NumPy | Reads and processes images/video and image data. |
| Pandas | Handles tabular inspection and statistics data. |
| fpdf2 | Creates the downloadable PDF project summary. |
| Node.js/npm | Builds frontend assets; not needed to serve the built app. |
| Open-Meteo APIs | Provides place lookup and regional modelled AQI/weather; requires internet. |
| Python `unittest` | Runs automated application tests. |

Python package versions are pinned in `requirements.txt`; frontend build packages are in `package.json` and `package-lock.json`. AI model weights and sample media are intentionally excluded from the source repository; supply them separately only after confirming permission. Without model files, AI review endpoints cannot run. Their accuracy and reuse licenses still need verification.

## 6. Important project files

| File/folder | Purpose |
|---|---|
| `web/api.py` | Web server, API endpoints, authentication and request handling. |
| `web/static/` | Browser pages, styles, scripts, and local visual libraries. |
| `app/auth.py` | Password hashing, sessions, and account operations. |
| `app/database.py` | SQLite setup and database functions. |
| `app/detector.py`, `app/hazard_engine.py` | Image-based PPE and hazard detection. |
| `app/video_detector.py`, `app/sentinel.py` | Video and browser-requested frame review. |
| `app/environment.py` | AQI/weather lookup and construction what-if. |
| `app/report.py` | PDF generation. |
| `tests/` | Automated application tests. |
| `models/` | Local AI model files. |
| `data/` | Local database and generated/review media. |
| `.github/workflows/ci.yml` | Automated checks for Windows/Python 3.11; latest run passed on commit `a42e3ab`. |

## 7. What is done and what remains?

- [x] Main app workflows and SQLite storage.
- [x] Admin/supervisor/viewer/worker account roles and role-specific access.
- [x] Worker signup with a site invitation code and unique EMP IDs.
- [x] Worker dashboard and own-attendance API access.
- [x] Worker daily check-in status, administrator-managed emergency/site notice information, and hazard/near-miss/injury reporting to the supervisor incident list.
- [x] 68 automated tests passed locally on 2026-10-24.
- [x] Source-only checkout test run passed; 4 tests requiring unbundled model weights are skipped.
- [x] Python compilation and JavaScript syntax checks passed.
- [x] Browser-tested account setup, login, account creation, and logout.
- [x] GitHub Actions passed on the published `main` commit (61 tests; 4 model tests skipped because weights are excluded). Worker-role, visitor-registration, and worker-dashboard changes have passed 68 local tests but are not yet published.
- [x] Migrate existing SQLite account tables to support worker roles without losing accounts or sessions.
- [ ] Add database backup/restore guidance.
- [ ] Confirm AI model licenses and test accuracy on labelled images.
- [ ] Decide media-retention rules.
- [ ] Add password recovery or cloud deployment only if the project needs them.

## 8. Known limitations

- No CCTV/RTSP connection, background monitoring, or cloud deployment.
- AI detections can be wrong; human review is needed.
- No verified AI accuracy report or model license review yet.
- AQI/weather are regional model estimates, not onsite sensor readings.
- Site map is illustrative; ticket risk is a project-defined value.
- Do not claim OSHA/ISO certification or public deployment readiness.
