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
| Accounts | Set up an administrator and create administrator, supervisor, or viewer accounts |

## Accounts

- **Administrator:** use all features and manage user accounts.
- **Supervisor:** use project features but cannot manage accounts.
- **Viewer:** read-only.
- The first user creates the administrator account. Passwords must be at least 12 characters and are stored as salted PBKDF2 hashes—not readable text.
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

This source repository does not include AI model weights or sample photos/videos. Provide the model files separately and only when you have permission to use them. Without model files, AI review endpoints cannot run; without sample videos, the bundled video examples are unavailable.

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
