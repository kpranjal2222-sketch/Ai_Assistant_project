"""
database.py  --  The platform's permanent memory.

Every safety check is saved into a small database file so the history
survives even after you close the app. We use SQLite, which:
  * is built into Python (nothing to install),
  * stores everything in ONE file (data/records.db),
  * behaves like a professional SQL database (great to mention in the report).

Think of the database as a smart Excel sheet that the program fills in
automatically. Each safety check becomes one row in the 'inspections' table.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

# ---------------------------------------------------------------------------
# File locations. We build paths relative to THIS file so they work no matter
# which folder the app is launched from.
#   data/records.db          -> the database file
#   data/detections/*.jpg    -> saved annotated images (one per saved check)
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DETECTIONS_DIR = DATA_DIR / "detections"
DB_PATH = DATA_DIR / "records.db"

# Make sure the folders exist before we try to write to them.
DATA_DIR.mkdir(exist_ok=True)
DETECTIONS_DIR.mkdir(exist_ok=True)


def _connect():
    """Open a connection to the SQLite database file.
    (Private helper -- the leading underscore is a Python convention meaning
    'for internal use by this module'.)"""
    return sqlite3.connect(DB_PATH)


def init_db(seed_demo=True):
    """
    Create all required database tables if they do not already exist.
    Safe to call every time the app starts -- 'IF NOT EXISTS' prevents duplication.
    """
    conn = _connect()
    # 1. Historical Photo Inspections table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inspections (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp      TEXT,
            image_name     TEXT,
            total_workers  INTEGER,
            helmet_ok      INTEGER,
            violations     INTEGER,
            compliance     REAL,
            image_path     TEXT
        )
        """
    )
    # 2. Construction Tasks table (Gantt schedule)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT,
            start_date  TEXT,
            end_date    TEXT,
            progress    INTEGER
        )
        """
    )
    # 3. Actionable OSHA EHS Incident Tickets table (SiteSentinel V2)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_tickets (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id        TEXT UNIQUE,
            timestamp        TEXT,
            hazard_type      TEXT,
            severity         TEXT,
            zone_name        TEXT,
            description      TEXT,
            evidence_file    TEXT DEFAULT '',
            status           TEXT DEFAULT 'OPEN',
            worker_id        INTEGER,
            supervisor_notes TEXT DEFAULT ''
        )
        """
    )
    # 4. Pre-configured Site Danger Zones table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_zones (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_code   TEXT UNIQUE,
            name        TEXT,
            hazard_type TEXT,
            risk_level  TEXT,
            is_active   INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL,
            check_date  TEXT NOT NULL,
            checked_at  TEXT NOT NULL,
            ppe_status  TEXT NOT NULL,
            findings    TEXT NOT NULL,
            UNIQUE(employee_id, check_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_ticket_links (
            employee_id  TEXT NOT NULL,
            check_date   TEXT NOT NULL,
            ticket_id    TEXT NOT NULL UNIQUE,
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(employee_id, check_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pre_shift_checks (
            check_date       TEXT PRIMARY KEY,
            headcount_ready  INTEGER NOT NULL DEFAULT 0,
            toolbox_talk     INTEGER NOT NULL DEFAULT 0,
            ppe_inspected    INTEGER NOT NULL DEFAULT 0,
            equipment_checked INTEGER NOT NULL DEFAULT 0,
            access_clear     INTEGER NOT NULL DEFAULT 0,
            checked_by       TEXT NOT NULL DEFAULT '',
            updated_at       TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name    TEXT NOT NULL,
            password_hash   TEXT NOT NULL,
            password_salt   TEXT NOT NULL,
            password_rounds INTEGER NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('admin', 'supervisor', 'viewer')),
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    if seed_demo:
        init_default_zones()
        init_default_tickets()


def add_inspection(image_name, total, helmet_ok, violations, compliance, image_path=""):
    """
    Save ONE safety-check result as a new row in the database.

    We use '?' placeholders instead of pasting values into the SQL text.
    This is called a 'parameterised query' and it's the safe, correct way
    to insert data (it prevents a class of bugs/attacks called SQL injection).
    """
    conn = _connect()
    conn.execute(
        """
        INSERT INTO inspections
            (timestamp, image_name, total_workers, helmet_ok, violations, compliance, image_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            image_name,
            int(total),
            int(helmet_ok),
            int(violations),
            float(compliance) if compliance is not None else None,
            image_path,
        ),
    )
    conn.commit()
    conn.close()


def get_inspections():
    """Return every saved inspection as a pandas DataFrame (newest first).
    A DataFrame is convenient for API serialization, statistics, and reports."""
    conn = _connect()
    df = pd.read_sql_query("SELECT * FROM inspections ORDER BY id DESC", conn)
    conn.close()
    return df


def get_stats():
    """
    Return the headline numbers for the dashboard as a simple dictionary:
      checks         -> how many photos were analysed
      workers        -> total workers seen across all checks
      helmet_ok      -> total wearing helmets
      violations     -> total not wearing helmets
      avg_compliance -> average compliance % across all checks (or None)
    """
    df = get_inspections()
    if df.empty:
        return {"checks": 0, "workers": 0, "helmet_ok": 0,
                "violations": 0, "avg_compliance": None}
    average = df["compliance"].mean()
    return {
        "checks": len(df),
        "workers": int(df["total_workers"].sum()),
        "helmet_ok": int(df["helmet_ok"].sum()),
        "violations": int(df["violations"].sum()),
        "avg_compliance": round(float(average), 1) if pd.notna(average) else None,
    }


def record_attendance(employee_id: str, ppe_status: str, findings: str) -> dict:
    """Save an employee check-in and text PPE findings; never persist an image."""
    now = datetime.now()
    check_date = now.strftime("%Y-%m-%d")
    checked_at = now.strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    conn.execute(
        """
        INSERT INTO attendance_records (employee_id, check_date, checked_at, ppe_status, findings)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(employee_id, check_date) DO UPDATE SET
            checked_at = excluded.checked_at,
            ppe_status = excluded.ppe_status,
            findings = excluded.findings
        """,
        (employee_id, check_date, checked_at, ppe_status, findings),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT employee_id, check_date, checked_at, ppe_status, findings
        FROM attendance_records WHERE employee_id = ? AND check_date = ?
        """,
        (employee_id, check_date),
    ).fetchone()
    conn.close()
    return dict(zip(("employee_id", "check_date", "checked_at", "ppe_status", "findings"), row))


def get_attendance_records(check_date: str | None = None) -> list[dict]:
    """Return check-ins for the requested date, defaulting to today."""
    selected_date = check_date or datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    rows = conn.execute(
        """
        SELECT a.employee_id, a.check_date, a.checked_at, a.ppe_status, a.findings,
               t.ticket_id,
               CASE WHEN t.ticket_id IS NULL THEN '' ELSE l.confirmed_by END AS confirmed_by
        FROM attendance_records AS a
        LEFT JOIN attendance_ticket_links AS l
          ON l.employee_id = a.employee_id AND l.check_date = a.check_date
        LEFT JOIN incident_tickets AS t ON t.ticket_id = l.ticket_id
        WHERE a.check_date = ? ORDER BY a.checked_at DESC, a.employee_id
        """,
        (selected_date,),
    ).fetchall()
    conn.close()
    return [
        dict(zip(
            ("employee_id", "check_date", "checked_at", "ppe_status", "findings", "ticket_id", "confirmed_by"),
            row,
        ))
        for row in rows
    ]


def confirm_attendance_incident(
    employee_id: str,
    check_date: str,
    confirmed_by: str,
) -> tuple[str, bool]:
    """Create an idempotent incident ticket after a supervisor verifies a PPE concern in person."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        record = conn.execute(
            """
            SELECT checked_at, ppe_status, findings
            FROM attendance_records WHERE employee_id = ? AND check_date = ?
            """,
            (employee_id, check_date),
        ).fetchone()
        if record is None:
            raise ValueError("No attendance check-in exists for this employee and date.")
        if record[1] != "REVIEW REQUIRED":
            raise ValueError("This check-in has no PPE concern requiring a follow-up ticket.")

        existing = conn.execute(
            """
            SELECT l.ticket_id
            FROM attendance_ticket_links AS l
            JOIN incident_tickets AS t ON t.ticket_id = l.ticket_id
            WHERE l.employee_id = ? AND l.check_date = ?
            """,
            (employee_id, check_date),
        ).fetchone()
        if existing:
            conn.commit()
            return existing[0], False

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"INC-{now:%Y%m%d}-"
        next_sequence = conn.execute(
            """
            SELECT COALESCE(MAX(CAST(substr(ticket_id, ?) AS INTEGER)), 0) + 1
            FROM incident_tickets WHERE ticket_id LIKE ?
            """,
            (len(prefix) + 1, f"{prefix}%"),
        ).fetchone()[0]
        ticket_id = f"{prefix}{next_sequence:03d}"
        zone_name = f"Worker check-in — {employee_id}; location not recorded"
        description = (
            f"Supervisor-confirmed PPE follow-up for employee {employee_id}. "
            f"Check-in recorded at {record[0]}. Model screening note: {record[2]}"
        )
        supervisor_notes = (
            f"Concern verified in person by {confirmed_by}. "
            "The check-in photo was not retained. Review and adjust the initial HIGH severity as needed."
        )
        conn.execute(
            """
            INSERT INTO incident_tickets
                (ticket_id, timestamp, hazard_type, severity, zone_name, description,
                 evidence_file, status, worker_id, supervisor_notes)
            VALUES (?, ?, 'PPE_FOLLOW_UP', 'HIGH', ?, ?, '', 'OPEN', NULL, ?)
            """,
            (ticket_id, timestamp, zone_name, description, supervisor_notes),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO attendance_ticket_links
                (employee_id, check_date, ticket_id, confirmed_by, confirmed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (employee_id, check_date, ticket_id, confirmed_by, timestamp),
        )
        conn.commit()
        return ticket_id, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_pre_shift_check(
    check_date: str,
    headcount_ready: bool,
    toolbox_talk: bool,
    ppe_inspected: bool,
    equipment_checked: bool,
    access_clear: bool,
    checked_by: str = "",
) -> dict:
    """Save the daily pre-shift readiness checklist."""
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = (
        int(headcount_ready),
        int(toolbox_talk),
        int(ppe_inspected),
        int(equipment_checked),
        int(access_clear),
        checked_by,
        updated_at,
        check_date,
    )
    conn = _connect()
    conn.execute(
        """
        INSERT INTO pre_shift_checks
            (headcount_ready, toolbox_talk, ppe_inspected, equipment_checked,
             access_clear, checked_by, updated_at, check_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(check_date) DO UPDATE SET
            headcount_ready = excluded.headcount_ready,
            toolbox_talk = excluded.toolbox_talk,
            ppe_inspected = excluded.ppe_inspected,
            equipment_checked = excluded.equipment_checked,
            access_clear = excluded.access_clear,
            checked_by = excluded.checked_by,
            updated_at = excluded.updated_at
        """,
        values,
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT check_date, headcount_ready, toolbox_talk, ppe_inspected,
               equipment_checked, access_clear, checked_by, updated_at
        FROM pre_shift_checks WHERE check_date = ?
        """,
        (check_date,),
    ).fetchone()
    conn.close()
    keys = (
        "check_date",
        "headcount_ready",
        "toolbox_talk",
        "ppe_inspected",
        "equipment_checked",
        "access_clear",
        "checked_by",
        "updated_at",
    )
    return dict(zip(keys, row))


def get_pre_shift_check(check_date: str | None = None) -> dict | None:
    """Return a saved daily checklist, or None when none has been recorded."""
    selected_date = check_date or datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    row = conn.execute(
        """
        SELECT check_date, headcount_ready, toolbox_talk, ppe_inspected,
               equipment_checked, access_clear, checked_by, updated_at
        FROM pre_shift_checks WHERE check_date = ?
        """,
        (selected_date,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    keys = (
        "check_date",
        "headcount_ready",
        "toolbox_talk",
        "ppe_inspected",
        "equipment_checked",
        "access_clear",
        "checked_by",
        "updated_at",
    )
    return dict(zip(keys, row))


def clear_all():
    """Delete every row from the table (handy for resetting before a demo)."""
    conn = _connect()
    conn.execute("DELETE FROM inspections")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# TASKS  (Progress Tracker -- Day 5)
# A second, separate set of functions for the construction task schedule.
# Same simple pattern as above: open connection -> run SQL -> commit -> close.
# ---------------------------------------------------------------------------
def add_task(name, start_date, end_date, progress=0):
    """Add one construction task. Dates are 'YYYY-MM-DD' strings."""
    conn = _connect()
    cursor = conn.execute(
        "INSERT INTO tasks (name, start_date, end_date, progress) VALUES (?, ?, ?, ?)",
        (name, start_date, end_date, int(progress)),
    )
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id


def get_tasks():
    """Return all tasks as a pandas DataFrame, ordered by start date."""
    conn = _connect()
    df = pd.read_sql_query("SELECT * FROM tasks ORDER BY start_date, id", conn)
    conn.close()
    return df


def update_task_progress(task_id, progress):
    """Change the % complete of one existing task."""
    conn = _connect()
    cursor = conn.execute(
        "UPDATE tasks SET progress = ? WHERE id = ?", (int(progress), int(task_id))
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def get_task(task_id):
    """Return one task as a dictionary, or None when its id does not exist."""
    conn = _connect()
    row = conn.execute(
        "SELECT id, name, start_date, end_date, progress FROM tasks WHERE id = ?",
        (int(task_id),),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(zip(("id", "name", "start_date", "end_date", "progress"), row))


def update_task(task_id, name, start_date, end_date, progress):
    """Update all editable fields for one task; return False if it does not exist."""
    conn = _connect()
    cursor = conn.execute(
        """
        UPDATE tasks
        SET name = ?, start_date = ?, end_date = ?, progress = ?
        WHERE id = ?
        """,
        (name, start_date, end_date, int(progress), int(task_id)),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_task(task_id):
    """Remove one task by its id."""
    conn = _connect()
    cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (int(task_id),))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def clear_tasks():
    """Remove every task (reset the schedule)."""
    conn = _connect()
    conn.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# OSHA EHS INCIDENT TICKETS & RISK INDEX (SiteSentinel V2)
# ---------------------------------------------------------------------------
def create_ticket(
    hazard_type: str,
    severity: str,
    zone_name: str,
    description: str,
    evidence_file: str = "",
    worker_id: int | None = None,
    supervisor_notes: str = "",
) -> str:
    """Create a project incident ticket."""
    conn = _connect()
    conn.execute("BEGIN IMMEDIATE")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"INC-{datetime.now():%Y%m%d}-"
    next_sequence = conn.execute(
        """
        SELECT COALESCE(MAX(CAST(substr(ticket_id, ?) AS INTEGER)), 0) + 1
        FROM incident_tickets
        WHERE ticket_id LIKE ?
        """,
        (len(prefix) + 1, f"{prefix}%"),
    ).fetchone()[0]
    ticket_id = f"{prefix}{next_sequence:03d}"

    conn.execute(
        """
        INSERT INTO incident_tickets
            (ticket_id, timestamp, hazard_type, severity, zone_name, description, evidence_file, status, worker_id, supervisor_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """,
        (
            ticket_id,
            ts,
            hazard_type,
            severity.upper(),
            zone_name,
            description,
            evidence_file,
            worker_id,
            supervisor_notes,
        ),
    )
    conn.commit()
    conn.close()
    return ticket_id


def get_tickets(status: str | None = None):
    """Return all incident tickets as a pandas DataFrame, newest first."""
    conn = _connect()
    if status:
        df = pd.read_sql_query(
            "SELECT * FROM incident_tickets WHERE status = ? ORDER BY id DESC",
            conn,
            params=(status.upper(),),
        )
    else:
        df = pd.read_sql_query("SELECT * FROM incident_tickets ORDER BY id DESC", conn)
    conn.close()
    return df


def get_ticket(ticket_id: str):
    """Return a single ticket dictionary by ticket_id."""
    conn = _connect()
    cursor = conn.execute("SELECT * FROM incident_tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "ticket_id", "timestamp", "hazard_type", "severity", "zone_name", "description", "evidence_file", "status", "worker_id", "supervisor_notes"]
    return dict(zip(cols, row))


def update_ticket_status(ticket_id: str, new_status: str, supervisor_notes: str | None = None) -> bool:
    """Update ticket lifecycle: OPEN -> INVESTIGATING -> REMEDIATED."""
    new_status = new_status.upper()
    if new_status not in ("OPEN", "INVESTIGATING", "REMEDIATED"):
        raise ValueError("Status must be OPEN, INVESTIGATING, or REMEDIATED.")

    conn = _connect()
    if supervisor_notes is not None:
        cursor = conn.execute(
            "UPDATE incident_tickets SET status = ?, supervisor_notes = ? WHERE ticket_id = ?",
            (new_status, supervisor_notes, ticket_id),
        )
    else:
        cursor = conn.execute(
            "UPDATE incident_tickets SET status = ? WHERE ticket_id = ?",
            (new_status, ticket_id),
        )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_ticket(ticket_id: str) -> bool:
    """Remove a ticket by ticket_id."""
    conn = _connect()
    cursor = conn.execute("DELETE FROM incident_tickets WHERE ticket_id = ?", (ticket_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def clear_tickets():
    """Clear all incident tickets."""
    conn = _connect()
    conn.execute("DELETE FROM incident_tickets")
    conn.commit()
    conn.close()


def get_site_risk_index() -> dict:
    """
    Calculate the project-defined incident-ticket risk indicator (0-100).
    This is not an OSHA score or a live hazard assessment.
    """
    df = get_tickets()
    if df.empty:
        return {
            "score": 100,
            "rating": "LOW TICKET RISK",
            "color": "#10B981",
            "open_tickets": 0,
            "remediated_tickets": 0,
            "breakdown": {"catastrophic": 0, "critical": 0, "high": 0, "moderate": 0, "low": 0},
        }

    score = 100
    open_count = 0
    remediated_count = 0
    breakdown = {"catastrophic": 0, "critical": 0, "high": 0, "moderate": 0, "low": 0}

    for _, r in df.iterrows():
        status = r["status"]
        severity = str(r["severity"]).upper()
        if status in ("OPEN", "INVESTIGATING"):
            open_count += 1
            mult = 1.0 if status == "OPEN" else 0.5  # 50% deduction if currently investigating
            if severity == "CATASTROPHIC":
                score -= int(30 * mult)
                breakdown["catastrophic"] += 1
            elif severity == "CRITICAL":
                score -= int(15 * mult)
                breakdown["critical"] += 1
            elif severity == "HIGH":
                score -= int(10 * mult)
                breakdown["high"] += 1
            elif severity == "MODERATE":
                score -= int(6 * mult)
                breakdown["moderate"] += 1
            else:
                score -= int(2 * mult)
                breakdown["low"] += 1
        elif status == "REMEDIATED":
            remediated_count += 1

    score = max(0, min(100, score))

    if score >= 85:
        rating = "LOW TICKET RISK"
        color = "#10B981"
    elif score >= 65:
        rating = "MODERATE TICKET RISK // REVIEW"
        color = "#F59E0B"
    elif score >= 40:
        rating = "HIGH TICKET RISK"
        color = "#EF4444"
    else:
        rating = "SEVERE TICKET RISK"
        color = "#DC2626"

    return {
        "score": score,
        "rating": rating,
        "color": color,
        "open_tickets": open_count,
        "remediated_tickets": remediated_count,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# SITE DANGER ZONES MANAGEMENT
# ---------------------------------------------------------------------------
def init_default_zones():
    """Seed standard site danger zones if table is empty."""
    conn = _connect()
    cursor = conn.execute("SELECT COUNT(*) FROM site_zones")
    if (cursor.fetchone()[0] or 0) == 0:
        default_zones = [
            ("ZONE-A", "South Trench & Excavation Pit", "EXCAVATION_COLLISION", "CRITICAL"),
            ("ZONE-B", "High-Voltage Transformer Bay", "ELECTRICAL_HAZARD", "CATASTROPHIC"),
            ("ZONE-C", "Tower Crane Swing Radius", "OVERHEAD_STRUCK_BY", "CRITICAL"),
            ("ZONE-D", "Elevated Framing Deck", "FALL_FROM_HEIGHT", "HIGH"),
            ("ZONE-E", "Hot-Work & Fuel Storage Bay", "FIRE_EXPLOSION", "CATASTROPHIC"),
        ]
        conn.executemany(
            "INSERT INTO site_zones (zone_code, name, hazard_type, risk_level, is_active) VALUES (?, ?, ?, ?, 1)",
            default_zones,
        )
        conn.commit()
    conn.close()


def get_zones():
    """Return all configured site danger zones."""
    conn = _connect()
    df = pd.read_sql_query("SELECT * FROM site_zones WHERE is_active = 1 ORDER BY id", conn)
    conn.close()
    return df.to_dict(orient="records")


def init_default_tickets():
    """Seed example incident tickets if the table is empty."""
    conn = _connect()
    cursor = conn.execute("SELECT COUNT(*) FROM incident_tickets")
    if (cursor.fetchone()[0] or 0) == 0:
        date_prefix = datetime.now().strftime("%Y%m%d")
        demo_tickets = [
            (
                f"INC-{date_prefix}-001",
                (datetime.now() - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "MISSING_PPE",
                "HIGH",
                "South Trench & Excavation Pit",
                "Worker #2 observed without ANSI Z89.1 Hardhat within active backhoe radius.",
                "",
                "OPEN",
                2,
                "",
            ),
            (
                f"INC-{date_prefix}-002",
                (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "PROXIMITY_DANGER",
                "CRITICAL",
                "Tower Crane Swing Radius",
                "Personnel entered restricted 3.0m heavy equipment swing arc without spotter clearance.",
                "",
                "INVESTIGATING",
                1,
                "EHS Officer dispatched to verify tag line protocol.",
            ),
            (
                f"INC-{date_prefix}-003",
                (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "FIRE_SMOKE_ALERT",
                "CATASTROPHIC",
                "Hot-Work & Fuel Storage Bay",
                "Oxy-acetylene cutting sparks detected near Class B flammable storage locker.",
                "",
                "REMEDIATED",
                4,
                "Fire watch stationed; spark shield curtain installed; hot-work permit re-authorized.",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO incident_tickets 
            (ticket_id, timestamp, hazard_type, severity, zone_name, description, evidence_file, status, worker_id, supervisor_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            demo_tickets,
        )
        conn.commit()
    conn.close()
