# Project Status

**Updated:** 2026-10-23

SiteSentinel is a working **local construction-safety review app** built with FastAPI, Python, HTML/CSS/JavaScript, and SQLite.

## Done

- [x] Photo, video, and frame-based safety review.
- [x] Incident tickets with notes, status changes, and optional image evidence.
- [x] Worker PPE check-in; check-in photos are not retained.
- [x] Daily pre-shift checklist and construction task tracker.
- [x] Illustrative site map and dashboard.
- [x] Regional AQI/weather lookup and a separate construction what-if.
- [x] PDF project summary.
- [x] Administrator, supervisor, and read-only viewer accounts.
- [x] First-admin setup, password hashing, login/logout, expiring sessions, and account management.
- [x] 61 automated tests passed; Python and JavaScript syntax checks passed.
- [x] Windows/Python 3.11 GitHub Actions workflow added; it still needs a run on GitHub.

## Still to do

1. [ ] Publish/connect the project to GitHub and verify the CI run.
2. [ ] Review the Starlette/httpx deprecation warning from the test client.
3. [ ] Add database backup/restore instructions and proper schema migrations.
4. [ ] Confirm model licenses and measure model accuracy on labelled images.
5. [ ] Decide on safe media-retention rules.
6. [ ] Add password change/recovery only if the project needs shared accounts.
7. [ ] Consider Docker/PostgreSQL/cloud deployment only after the local app is stable and deployment needs are clear.

## Keep these limitations clear

- The app is for local demonstration, not public deployment.
- It has no RTSP/CCTV integration or background surveillance.
- The map is not to scale; AQI values are regional model estimates.
- AI results and ticket-risk scores do not certify site safety or OSHA compliance.
- The installed models' accuracy and redistribution rights have not been confirmed.

For a simple architecture/technology explanation, see [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md). To continue in another chat, see [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md).
