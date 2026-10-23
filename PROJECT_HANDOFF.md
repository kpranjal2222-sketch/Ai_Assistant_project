# SiteSentinel Handoff

Use this file to continue the project in a new chat.

## Start here

1. Read [README.md](README.md) to run the app.
2. Read [PROJECT_STATUS.md](PROJECT_STATUS.md) for completed and remaining work.
3. Read [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) for the system and technology overview.

## Current project

- Local web app: FastAPI backend, HTML/CSS/JavaScript frontend.
- Local database: SQLite (`data/records.db`).
- Features: safety review, incidents, worker check-in, tasks, pre-shift checklist, AQI/weather, site map, PDF report, and user accounts.
- Roles: administrator (manage app and accounts), supervisor (manage project work), viewer (read-only).
- Authentication tests, full app tests, JavaScript syntax check, and Python compilation passed on 2026-10-23.
- A Windows/Python 3.11 CI workflow exists but has not yet run on GitHub.

## Remaining work

- [ ] Connect/publish to GitHub and verify CI.
- [ ] Investigate the test-client deprecation warning.
- [ ] Add database migrations and backup/restore guidance.
- [ ] Verify model licenses and evaluate model accuracy with labelled data.
- [ ] Decide safe media-retention rules.
- [ ] Consider password recovery, PostgreSQL, or Docker only if needed.

## Important boundaries

- Keep local-only; do not expose publicly.
- No CCTV/RTSP or unattended monitoring.
- The map and construction AQI scenario are illustrative.
- Do not claim certified safety, model accuracy, or OSHA compliance.
- Do not change PPE model behavior or worker manual verification without explicit request and tests.

## Windows commands

```powershell
.\venv\Scripts\python.exe -m uvicorn web.api:app --host 127.0.0.1 --port 8000
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m compileall -q app web tests scripts
node --check web\static\js\app.js
```
