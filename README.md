# Ai_Assistant_project
Construction Management
# CONSTRUCTION MANAGEMENT
### AI-Assisted Construction Project Monitoring & Safety Management Platform

A college project for the subject **AI-Assisted Coding**.

A web-based platform that uses AI (computer vision) to monitor a construction
site — automatically tracking work progress and detecting safety violations
such as workers not wearing helmets or safety vests.

---

## 🎯 What it does (Core Features)
1. **🪖 PPE Safety Detection (AI star feature)** — Upload a construction site
   photo; the AI detects workers and flags anyone missing a helmet or safety vest.
2. **📈 Progress Tracker** — Enter construction tasks and % complete; view
   progress charts vs. schedule.
3. **📋 Incident Log** — Every safety check is saved to a database with date and
   violations found.
4. **🚨 Alerts** — Warnings shown when a violation is detected + recent alerts on
   the dashboard.

## 🎁 Stretch Goals (only if time remains)
- 👷 Worker Attendance tracking
- 📄 In-app Auto Report generation

---

## 🛠️ Tech Stack
| Tool | Purpose | Why |
|------|---------|-----|
| Python 3.11 | Language | Beginner-friendly, #1 for AI |
| Streamlit | Web UI | Build a website using only Python |
| Ultralytics YOLO | AI vision | Pre-trained object detection (helmets, vests, people) |
| SQLite | Database | Built-in, stores the incident log |
| Plotly | Charts | Dashboard graphs |

> **Note:** We use **Python 3.11** (NOT 3.14) because the AI libraries
> (PyTorch/YOLO) are not yet compatible with Python 3.14.

---

## 🚀 How to Run (will be filled in once built)
```bash
# 1. Activate the virtual environment
source venv/Scripts/activate      # Git Bash on Windows

# 2. Run the app
streamlit run app/main.py
```

---

## 📅 Project Status
See **PROJECT_PLAN.md** for the full day-by-day plan and current progress.

## 👤 Author
Solo project. Built with AI-assisted coding (Claude Code).
