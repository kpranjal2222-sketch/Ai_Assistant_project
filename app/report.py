"""
report.py  --  Generates a project safety and progress summary PDF.

Uses fpdf2 (a modern, pure-Python PDF library).
Main function: build_safety_report_pdf(stats, inspections, tasks, ...) -> bytes
"""

from datetime import datetime
import math
import os
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from app import database

# Brand colours (R, G, B) -- warm construction palette
ORANGE = (205, 103, 42)
SLATE = (37, 39, 34)
DARK_BLUE = (55, 58, 50)
GREY = (100, 116, 139)
LIGHT_GREY = (241, 245, 249)
RED = (220, 38, 38)
GREEN = (16, 185, 129)
AMBER = (245, 158, 11)

# Shorthand: after writing a cell, move to the next line at the left margin.
NEXT = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _s(text):
    """Make text safe for the PDF core font (latin-1). Drops emoji / unicode."""
    return str(text or "").encode("latin-1", "replace").decode("latin-1")


class _Report(FPDF):
    """A4 PDF with industrial high-density header and footer on every page."""

    def header(self):
        self.set_fill_color(*SLATE)
        self.rect(0, 0, self.w, 22, "F")
        self.set_xy(12, 4)
        self.set_text_color(*ORANGE)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 7, "SITESENTINEL  //  PROJECT SAFETY REVIEW")
        self.set_xy(12, 12)
        self.set_text_color(203, 213, 225)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 5, "Local project summary | Human review required")
        self.set_y(28)

    def footer(self):
        self.set_y(-12)
        self.set_text_color(*GREY)
        self.set_font("Helvetica", "", 8)
        self.cell(
            0,
            8,
            _s(f"Generated project summary  |  {datetime.now():%Y-%m-%d %H:%M}  |  Page {self.page_no()}"),
            align="C",
        )


def _section(pdf, title):
    """Draw an orange section heading with an underline, then reset to body text."""
    pdf.set_text_color(*ORANGE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, _s(title), **NEXT)
    pdf.set_draw_color(*ORANGE)
    pdf.set_line_width(0.4)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)
    pdf.set_text_color(30, 41, 59)
    pdf.set_font("Helvetica", "", 9)


def build_safety_report_pdf(
    stats,
    inspections,
    tasks,
    include_images=True,
    tickets=None,
    risk_index=None,
    zones=None,
):
    """
    Build a local project summary PDF and return it as bytes.
    """
    if tickets is None:
        tickets = database.get_tickets()
    if risk_index is None:
        risk_index = database.get_site_risk_index()
    if zones is None:
        zones = database.get_zones()

    pdf = _Report()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # --- Title & Metadata Block ------------------------------------------
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, "Site Safety & Progress Summary", **NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GREY)
    pdf.cell(
        0,
        5,
        _s("Source: Local project records and model-assisted checks | Human verification required"),
        **NEXT,
    )
    pdf.cell(
        0,
        5,
        _s(f"Generated: {datetime.now():%d %B %Y, %H:%M:%S}  |  Site: Not specified"),
        **NEXT,
    )
    pdf.ln(3)

    # --- 1. Project Ticket Risk Indicator --------------------------------
    _section(pdf, "1. Project Ticket Risk Indicator")
    score = risk_index.get("score", 100)
    rating = risk_index.get("rating", "LOW TICKET RISK")

    pdf.set_fill_color(*LIGHT_GREY)
    pdf.rect(pdf.l_margin, pdf.get_y(), pdf.w - pdf.l_margin - pdf.r_margin, 18, "F")
    pdf.set_xy(pdf.l_margin + 4, pdf.get_y() + 3)

    pdf.set_font("Helvetica", "B", 14)
    if score >= 85:
        pdf.set_text_color(*GREEN)
    elif score >= 65:
        pdf.set_text_color(*AMBER)
    else:
        pdf.set_text_color(*RED)

    pdf.cell(35, 6, f"{score} / 100")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(80, 6, _s(f"Rating: {rating}"))
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GREY)
    pdf.set_xy(pdf.l_margin + 4, pdf.get_y() + 6)
    pdf.cell(
        0,
        5,
        _s(f"Open tickets: {risk_index.get('open_tickets', 0)}  |  Remediated: {risk_index.get('remediated_tickets', 0)}  |  Project-defined indicator; not a compliance score"),
    )
    pdf.set_y(pdf.get_y() + 8)
    pdf.ln(4)

    # --- 2. Saved Inspection Summary --------------------------------------
    _section(pdf, "2. Saved Inspection Summary")
    avg = stats.get("avg_compliance")
    avg_txt = f"{avg:.0f}%" if avg is not None else "N/A"
    violations = int(stats.get("violations", 0))

    summary_rows = [
        ("Saved photo inspections", str(stats.get("checks", 0))),
        ("Workers in saved checks", str(stats.get("workers", 0))),
        ("Hardhat detections marked OK", str(stats.get("helmet_ok", 0))),
        ("Hardhat violations recorded", str(violations)),
        ("Average PPE result in saved checks", avg_txt),
        ("Configured sample zones", str(len(zones))),
        ("Incident tickets", str(len(tickets) if tickets is not None else 0)),
    ]
    with pdf.table(
        width=135,
        col_widths=(3, 1),
        first_row_as_headings=False,
        text_align=("LEFT", "RIGHT"),
    ) as table:
        for k, v in summary_rows:
            row = table.row()
            row.cell(_s(k))
            row.cell(_s(v))
    pdf.ln(2)

    # Safety verdict banner
    pdf.set_font("Helvetica", "B", 10)
    if inspections is None or inspections.empty:
        pdf.set_text_color(*GREY)
        pdf.multi_cell(0, 5, "No inspections recorded; no saved PPE result is available.", **NEXT)
    elif violations > 0:
        pdf.set_text_color(*RED)
        pdf.multi_cell(
            0,
            5,
            _s(
                f"ALERT: {violations} helmet violation(s) recorded across all checks. Corrective action is recommended."
            ),
            **NEXT,
        )
    else:
        pdf.set_text_color(*GREEN)
        pdf.multi_cell(0, 5, "No hardhat violations were recorded in the saved checks.", **NEXT)
    pdf.ln(3)

    head_style = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=ORANGE)

    # --- 3. Configured Sample Zones ---------------------------------------
    if zones:
        _section(pdf, "3. Configured Sample Zones")
        with pdf.table(
            col_widths=(18, 55, 45, 28, 25),
            text_align="CENTER",
            headings_style=head_style,
        ) as table:
            h = table.row()
            for head in ["Code", "Zone Name & Location", "Hazard Type", "Risk Level", "Buffer"]:
                h.cell(head)
            for z in zones:
                row = table.row()
                row.cell(_s(z.get("zone_code", "-")))
                row.cell(_s(z.get("name", "-")))
                row.cell(_s(z.get("hazard_type", "-")))
                row.cell(_s(z.get("risk_level", "-")))
                row.cell("Illustrative")
        pdf.ln(3)

    # --- 4. Incident Tickets ----------------------------------------------
    if tickets is not None and not tickets.empty:
        _section(pdf, "4. Incident Tickets & Follow-up")
        with pdf.table(
            col_widths=(28, 30, 36, 22, 24, 30),
            text_align="CENTER",
            headings_style=head_style,
        ) as table:
            h = table.row()
            for head in ["Ticket ID", "Timestamp", "Hazard Classification", "Severity", "Status", "Resolution"]:
                h.cell(head)
            for _, t in tickets.head(10).iterrows():
                row = table.row()
                row.cell(_s(t.get("ticket_id", "-")))
                row.cell(_s(str(t.get("timestamp", "-"))[:16]))
                row.cell(_s(t.get("hazard_type", "-")))
                row.cell(_s(t.get("severity", "-")))
                row.cell(_s(t.get("status", "-")))
                notes = t.get("supervisor_notes", "") or "Under Review"
                row.cell(_s(notes[:24]))
        pdf.ln(3)

    # --- 5. Saved Photo Checks --------------------------------------------
    if inspections is not None and not inspections.empty:
        _section(pdf, "5. Saved Photo Checks")
        with pdf.table(
            col_widths=(10, 32, 40, 16, 14, 14, 18),
            text_align="CENTER",
            headings_style=head_style,
        ) as table:
            h = table.row()
            for head in ["ID", "Date & Time", "Photo", "Workers", "OK", "Viol.", "Comply"]:
                h.cell(head)
            for _, r in inspections.head(10).iterrows():
                comp = r.get("compliance")
                comp_txt = f"{comp:.0f}%" if comp is not None and math.isfinite(float(comp)) else "-"
                row = table.row()
                row.cell(_s(r.get("id", "-")))
                row.cell(_s(r.get("timestamp", "-")))
                row.cell(_s(str(r.get("image_name", "-"))[:20]))
                row.cell(_s(r.get("total_workers", "-")))
                row.cell(_s(r.get("helmet_ok", "-")))
                row.cell(_s(r.get("violations", "-")))
                row.cell(_s(comp_txt))
        pdf.ln(3)

    # --- 6. Project Schedule & Milestone Tracking ------------------------
    if tasks is not None and not tasks.empty:
        _section(pdf, "6. Construction Progress & Milestone Schedule")
        overall = int(round(tasks["progress"].mean()))
        behind = int((tasks["status"] == "Behind Schedule").sum()) if "status" in tasks else 0
        pdf.set_text_color(40, 40, 40)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(
            0,
            5,
            _s(f"Overall Project Completion: {overall}%    |    Tasks Behind Schedule: {behind}"),
            **NEXT,
        )
        pdf.ln(1)
        with pdf.table(
            col_widths=(54, 26, 28, 18, 30),
            text_align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER"),
            headings_style=head_style,
        ) as table:
            h = table.row()
            for head in ["Task Name", "Start Date", "Planned End", "% Done", "Schedule Status"]:
                h.cell(head)
            for _, r in tasks.iterrows():
                row = table.row()
                row.cell(_s(r.get("name", "-")))
                row.cell(_s(r.get("start_date", "-")))
                row.cell(_s(r.get("end_date", "-")))
                row.cell(_s(f"{r.get('progress', 0)}%"))
                row.cell(_s(r.get("status", "")))
        pdf.ln(3)

    # --- 7. Forensic Evidence Snapshots ----------------------------------
    if include_images:
        evidence_dir = Path(__file__).resolve().parent.parent / "data" / "evidence"

        image_files = []
        if evidence_dir.is_dir():
            image_files.extend(list(evidence_dir.glob("*.jpg"))[:2])
        if inspections is not None and not inspections.empty:
            for p in inspections.get("image_path", []):
                if isinstance(p, str) and p and os.path.exists(p) and len(image_files) < 3:
                    image_files.append(Path(p))

        if image_files:
            _section(pdf, "7. Forensic Photographic Evidence Gallery")
            for img_path in image_files[:2]:
                try:
                    pdf.image(str(img_path), w=125)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.set_text_color(*GREY)
                    pdf.cell(0, 5, _s(f"Capture Evidence: {img_path.name}"), **NEXT)
                    pdf.ln(2)
                except Exception:
                    pass

    # --- 8. Review note ----------------------------------------------------
    pdf.ln(2)
    _section(pdf, "8. Important review note")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(
        0,
        4,
        "Computer-vision outputs and the project-defined ticket indicator are unvalidated decision-support signals. "
        "This document is generated from local project data and does not certify compliance with OSHA, ISO 45001, "
        "or any safety standard. A qualified person must verify findings on site.",
        **NEXT,
    )
    pdf.ln(4)

    # Signature line
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(80, 5, "Project reviewer: ___________________")
    pdf.cell(0, 5, "Date: ___________________", **NEXT)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GREY)
    pdf.cell(80, 4, "Name / role")
    pdf.cell(0, 4, "Date", **NEXT)

    return bytes(pdf.output())
