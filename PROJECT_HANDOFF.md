# SiteSentinel Handoff

Use this file to continue the project in a new chat.

## Start here

1. Read [README.md](README.md) to run the app.
2. Read [PROJECT_STATUS.md](PROJECT_STATUS.md) for completed and remaining work.
3. Read [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) for the system and technology overview.

## Current project

- GitHub repository: [kpranjal2222-sketch/Ai_Assistant_project](https://github.com/kpranjal2222-sketch/Ai_Assistant_project), branch `main`.
- Latest published commit: `a42e3ab`; latest GitHub Actions run passed on Windows/Python 3.11.
- Local web app: FastAPI backend, HTML/CSS/JavaScript frontend.
- Local database: SQLite (`data/records.db`).
- Features: safety review, incidents, worker check-in, tasks, pre-shift checklist, AQI/weather, site map, PDF report, and user accounts.
- Roles: administrator (manage app and accounts), supervisor (manage project work), viewer (read-only).
- Authentication tests, full app tests, JavaScript syntax check, and Python compilation passed on 2026-10-23.
- The 61-test suite also passes in a source-only snapshot without private model/media files; 4 model-inference checks skip because those assets are intentionally not published.
- The first two CI runs failed because tests expected unpublished model assets and because Windows used different path spellings. Both issues are fixed; run #3 is green.

## Remaining work

- [x] Publish source/docs/tests to GitHub and verify CI.
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

## Message for the next AI assistant

> Continue this project from https://github.com/kpranjal2222-sketch/Ai_Assistant_project. Read `PROJECT_HANDOFF.md`, `PROJECT_STATUS.md`, `README.md`, and `docs/PROJECT_REPORT.md` first. The local FastAPI/HTML/CSS/JavaScript/SQLite app already has safety review, incident tickets, check-in, tasks, AQI/weather, reports, and admin/supervisor/viewer accounts. Latest main commit `a42e3ab` has a green GitHub Actions run: 61 tests, 4 model tests skipped because model weights are intentionally excluded. Never commit database files, worker images, generated media, secrets, or model weights. Keep the app local-only; don't claim verified model accuracy, OSHA/ISO certification, or live CCTV. Do not change PPE behavior or worker manual verification unless asked. Next: address the Starlette/httpx test-client warning, then add SQLite backup/migrations; later verify model licenses and evaluate on licensed labelled images. Explain work simply and update these documents after validating changes.
