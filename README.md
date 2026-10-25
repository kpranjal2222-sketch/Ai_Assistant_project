# SiteSentinel

SiteSentinel is a local construction project and safety review app. It brings photo/video checks, incident follow-up, worker check-in, task tracking, site information, and reports into one browser interface.

> This is a student project, not certified safety software. AI findings need a human review. The app is for trusted local use; it is not ready for public deployment.

## What you can do

| Feature | What it does |
|---|---|
| Safety review | Check photos and sampled video for possible PPE and other hazards |
| Incident follow-up | Record a concern, assign a status, add notes, and optionally attach evidence |
| Worker check-in | Screen a photo for PPE; the check-in photo is not saved |
| Site map | View configured zones on an illustrative, not-to-scale map |
| Air quality | View regional model AQI/weather by state and district/city, plus a separate construction what-if |
| Project tools | Manage tasks, complete a daily readiness checklist, view the dashboard, and export a PDF summary |
| Accounts | Set up an administrator and manage administrator, supervisor, viewer, and worker accounts |

## Accounts

- **Administrator:** use all features and manage user accounts.
- **Supervisor:** use project features but cannot manage accounts.
- **Visitor (viewer):** self-registers with a unique username and password; read-only.
- **Worker:** sees a worker dashboard with today's check-in status and their own attendance history, regional AQI/weather, verified emergency details, the current site notice/toolbox talk, safety reminders, and a hazard/near-miss/injury report form. Reports become incident tickets for supervisor follow-up; workers cannot read project-wide tickets or other workers' records.
- Administrators set the worker dashboard's emergency contact, first-aid location, assembly point, and current notice from **Accounts → Worker dashboard information**. Enter only verified site details; blank values are shown to workers as not configured.
- Workers sign in with an employee ID such as `EMP001` and a password. IDs are unique and remain reserved after an account is disabled.
- Worker self-registration requires a site invitation code. Configure `SITESENTINEL_WORKER_INVITE_CODE` before starting the app and give the code only to intended workers:

```powershell
$env:SITESENTINEL_WORKER_INVITE_CODE = "your-site-invitation-code"
```

The code enables signup but does not prove a worker's identity; distribute it and employee ID assignments privately. Administrators can also create worker accounts from Accounts by using the assigned `EMP` ID as the username.
- **Supervisor and administrator:** cannot self-register. An existing administrator creates these accounts through Accounts. The first administrator is created only during initial project setup.
- On first open, create the project administrator account: username must be at least 5 characters and password at least 6. Passwords are stored as salted PBKDF2 hashes—not readable text.
- Sign-in is limited to 5 attempts per minute to prevent repeated guesses; wait 1 minute if the limit is reached.
- Sessions expire after 8 hours. There is no password reset/change feature yet.

For first-time setup, optionally set a one-time setup token before starting the server:

```powershell
$env:SITESENTINEL_SETUP_TOKEN = "your-long-random-token"
```

Clear it after creating the first account.

## Run on Windows

From the project folder:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn web.api:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. Data is stored locally in SQLite and under `data/`.

This source repository does not include AI model weights or sample photos/videos. Provide the model files separately and only when you have permission. Without model files, AI review endpoints cannot run; without sample videos, bundled video examples are unavailable.

The test suite runs without those assets; model-dependent accuracy checks are skipped when model files are absent.

## Check the project

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m compileall -q app web tests scripts
node --check web\static\js\app.js
```

## Important limits

- No live CCTV/RTSP or unattended monitoring.
- The map is illustrative, not a surveyed site plan.
- AQI/weather are regional model estimates, not onsite readings.
- The ticket risk value is a project calculation, not an OSHA score.
- Model accuracy and model-file licenses have not been independently verified.
- Bind to `127.0.0.1`; do not expose this student project to the internet.

## Project documents

- [Project report](docs/PROJECT_REPORT.md): how the app works and what technologies it uses.
- [Current status](PROJECT_STATUS.md): what is done and what is not.
- [Handoff checklist](PROJECT_HANDOFF.md): how to continue development in another chat.
