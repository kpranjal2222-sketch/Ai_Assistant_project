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
- Roles: administrator (manage app and accounts), supervisor (manage project work), viewer (read-only), and worker (own attendance/check-in, AQI/weather, and worker dashboard only).
- Workers can register with a site invitation code set through `SITESENTINEL_WORKER_INVITE_CODE`; IDs are canonical `EMP` numbers and remain unique, including after account deactivation. The shared code does not verify identity; do not distribute it publicly.
- Visitors can self-register as read-only viewer accounts. Supervisors and administrators cannot self-register; only an existing administrator may create them (except the initial administrator setup flow).
- Worker dashboard now includes today's attendance status, verified admin-configured emergency/first-aid/assembly details, the current site notice/toolbox talk, and hazard/near-miss/injury reports sent into supervisor incident tickets. Workers may not view other tickets or worker records.
- Existing account databases are migrated at startup to permit the worker role while preserving account and session rows.
- Worker/visitor registration and worker-dashboard additions are in the current worktree and are not yet published. Authentication, full-suite, JavaScript syntax, and Python compilation checks passed locally on 2026-10-24 (68 tests).
- The previous 61-test suite passed in a source-only snapshot without private model/media files; 4 model-inference checks skipped because those assets were intentionally not published.
- The first two CI runs failed because tests expected unpublished model assets and because Windows used different path spellings. Both issues are fixed; run #3 is green.

## Remaining work

- [x] Publish source/docs/tests to GitHub and verify CI.
- [ ] Investigate the test-client deprecation warning.
- [x] Add migration for the worker role while preserving existing accounts and sessions.
- [ ] Add database backup/restore guidance.
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

> Continue this project from https://github.com/kpranjal2222-sketch/Ai_Assistant_project. Read `PROJECT_HANDOFF.md`, `PROJECT_STATUS.md`, `README.md`, and `docs/PROJECT_REPORT.md` first. The local FastAPI/HTML/CSS/JavaScript/SQLite app includes safety review, incidents, check-in, tasks, AQI/weather, reports, and four access types: administrator, supervisor, visitor/read-only viewer, and worker. Visitor self-registration always creates a read-only account. Worker registration requires the site-configured `SITESENTINEL_WORKER_INVITE_CODE`; IDs are unique `EMP` numbers. Supervisors and administrators cannot self-register and must be created by an existing admin, except initial admin setup. Worker API access is restricted to their own attendance and environmental information, with hazard reports creating supervisor-visible incidents. The worker dashboard also displays admin-configured emergency/site information and toolbox notices. Current worker registration/dashboard changes are local and not published; 68 tests, Python compilation, and JavaScript syntax checks passed locally on 2026-10-24. Latest published main commit `a42e3ab` had 61 passing tests and 4 model tests skipped because model weights are intentionally excluded. Never commit database files, worker images, generated media, secrets, or model weights. Keep the app local-only; don't claim verified model accuracy, OSHA/ISO certification, or live CCTV. Do not change PPE behavior or worker manual verification unless asked. Next: address the Starlette/httpx test-client warning, add SQLite backup/restore guidance, then verify model licenses and evaluate on licensed labelled images. Explain work simply and update these documents after validating changes.
