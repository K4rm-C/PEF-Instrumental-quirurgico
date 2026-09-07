"""
Frontend preview harness — DEVELOPMENT ONLY. NOT the production application.

Renders the currently implemented Jinja templates — the complete Operator CDE presentation
layer (Sign In, Operator Dashboard, Counting Sessions, New Counting Session, Session
History, Active Counting Session, Count Validation (no discrepancy / with discrepancy),
Closed Session Details, and the Close Session / Supervisor Review Required modals), the
complete Supervisor CDE / Quality presentation layer (Supervisor Dashboard, Sessions,
Discrepancies incl. Post Approval / Post Correction, Discrepancy Review, Session Details,
Session History, Reports, Indicators, Audit Log, and the Approve Review / Request Correction
modals), and the complete IT Administrator presentation layer (Dashboard, Instrument
Families, Instruments, Kits — each with New/Edit forms, Users incl. New/Edit and the Change
Password / Deactivate User modals, Roles incl. Edit Role, and Configuration incl. Edit
Institution Information and New/Edit Capture Station) — through a small standalone Flask app
that lives entirely under devtools/ and never imports from app.py, extensions.py,
controllers/, clients/, or models/. It exists only so the frontend templates in templates/
and static/ can be visually reviewed in a browser while the real backend does not exist yet.

This harness intentionally does NOT:
  - initialize SQLAlchemy or any of the apps/BackendWebFlask/models/ classes
  - require PostgreSQL, Redis, or MongoDB
  - perform real authentication
  - require Flask-Babel to be installed or configured

Run it with:
    python devtools/frontend_preview.py

Then open:
    http://127.0.0.1:5001/preview/sign-in
    http://127.0.0.1:5001/preview/operator/dashboard
    http://127.0.0.1:5001/preview/operator/profile
    http://127.0.0.1:5001/preview/operator/sessions
    http://127.0.0.1:5001/preview/operator/sessions/new
    http://127.0.0.1:5001/preview/operator/session-history
    http://127.0.0.1:5001/preview/operator/sessions/active
    http://127.0.0.1:5001/preview/operator/sessions/validation
    http://127.0.0.1:5001/preview/operator/sessions/validation-discrepancy
    http://127.0.0.1:5001/preview/operator/sessions/closed
    http://127.0.0.1:5001/preview/supervisor/dashboard
    http://127.0.0.1:5001/preview/supervisor/profile
    http://127.0.0.1:5001/preview/supervisor/sessions
    http://127.0.0.1:5001/preview/supervisor/session-history
    http://127.0.0.1:5001/preview/supervisor/sessions/details
    http://127.0.0.1:5001/preview/supervisor/discrepancies
    http://127.0.0.1:5001/preview/supervisor/discrepancies/post-approval
    http://127.0.0.1:5001/preview/supervisor/discrepancies/post-correction
    http://127.0.0.1:5001/preview/supervisor/discrepancies/review
    http://127.0.0.1:5001/preview/supervisor/reports
    http://127.0.0.1:5001/preview/supervisor/indicators
    http://127.0.0.1:5001/preview/supervisor/audit-log
    http://127.0.0.1:5001/preview/admin/dashboard
    http://127.0.0.1:5001/preview/admin/profile
    http://127.0.0.1:5001/preview/admin/instrument-families
    http://127.0.0.1:5001/preview/admin/instrument-families/new
    http://127.0.0.1:5001/preview/admin/instrument-families/edit
    http://127.0.0.1:5001/preview/admin/instruments
    http://127.0.0.1:5001/preview/admin/instruments/new
    http://127.0.0.1:5001/preview/admin/instruments/edit
    http://127.0.0.1:5001/preview/admin/kits
    http://127.0.0.1:5001/preview/admin/kits/new
    http://127.0.0.1:5001/preview/admin/kits/edit
    http://127.0.0.1:5001/preview/admin/users
    http://127.0.0.1:5001/preview/admin/users/new
    http://127.0.0.1:5001/preview/admin/users/edit
    http://127.0.0.1:5001/preview/admin/roles
    http://127.0.0.1:5001/preview/admin/roles/edit
    http://127.0.0.1:5001/preview/admin/configuration
    http://127.0.0.1:5001/preview/admin/configuration/institution/edit
    http://127.0.0.1:5001/preview/admin/configuration/capture-stations/new
    http://127.0.0.1:5001/preview/admin/configuration/capture-stations/edit

See devtools/README.md for full details.
"""

import os

from flask import Flask, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
TEMPLATES_DIR = os.path.join(BACKEND_ROOT, "templates")
STATIC_DIR = os.path.join(BACKEND_ROOT, "static")

app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR,
    static_url_path="/static",
)

# ---------------------------------------------------------------------------
# Preview-only Flask-Babel stand-in
# ---------------------------------------------------------------------------
# The real templates call {{ _("...") }} — including gettext-style parameterized strings
# such as {{ _("Showing %(shown)s of %(total)s sessions", shown=3, total=8) }} — in
# preparation for Flask-Babel, which is not installed or configured yet (see
# FRONTEND_INTEGRATION.md). For preview rendering only, `_` is mapped to a function that
# returns the original English string, interpolating any %(name)s placeholders exactly like
# real gettext would. This stand-in must never be used in the production application.


def _preview_gettext(text, **kwargs):
    return text % kwargs if kwargs else text


app.jinja_env.globals["_"] = _preview_gettext


# ---------------------------------------------------------------------------
# Demo context — plain Python dicts only. No database models, no backend imports.
# Values mirror the approved Figma references and the same temporary demo data already
# documented inline in the corresponding templates.
# ---------------------------------------------------------------------------
PREVIEW_LOCALE = "en"

PREVIEW_CURRENT_USER = {
    "name": "Alex Morgan",
    "role_label": "Operator",
    "profile_role_label": "Operator CDE",
    "email": "alex.morgan@institution.edu",
    "institution": "Hospital Institution",
    "status_label": "Active",
    "status_variant": "success",
    "avatar_url": None,
}

PREVIEW_DASHBOARD_STATS = {
    "sessions_today": 12,
    "active_sessions": 3,
    "closed_sessions": 9,
    "open_discrepancies": 2,
}

PREVIEW_SESSIONS_BY_DAY = [
    {"label": "Mon", "count": 8},
    {"label": "Tue", "count": 12},
    {"label": "Wed", "count": 10},
    {"label": "Thu", "count": 15},
    {"label": "Fri", "count": 11},
]

PREVIEW_SESSION_STATUS_BREAKDOWN = [
    {"label": "Active Sessions", "value": 3, "variant": "info"},
    {"label": "Closed (Verified)", "value": 9, "variant": "success"},
    {"label": "With Discrepancies", "value": 2, "variant": "danger"},
]

PREVIEW_RECENT_SESSIONS = [
    {
        "session_id": "WS-026",
        "procedure_label": "Appendectomy - OR-3",
        "kit_name": "Delivery Kit",
        "operator_name": "Alex Morgan",
        "status_label": "Active",
        "status_variant": "info",
        "action_label": "Continue",
        "action_url": "#",
    },
    {
        "session_id": "WS-025",
        "procedure_label": "Cholecystectomy - OR-1",
        "kit_name": "Suture Kit",
        "operator_name": "Alex Morgan",
        "status_label": "Pending Validation",
        "status_variant": "pending",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-024",
        "procedure_label": "Hernia Repair - OR-2",
        "kit_name": "General Surgery Kit",
        "operator_name": "Alex Morgan",
        "status_label": "Closed",
        "status_variant": "success",
        "action_label": "View Details",
        "action_url": "#",
    },
]

# Counting Sessions (operational queue: Active / Pending Validation / With Discrepancies)
PREVIEW_SESSIONS = [
    {
        "session_id": "WS-024",
        "procedure_name": "Appendectomy",
        "operating_room": "OR-3",
        "kit_name": "Delivery Kit",
        "operator_name": "Alex Morgan",
        "capture_station_name": "Station CDE-01",
        "started_at": "14:32",
        "status_label": "Active",
        "status_variant": "info",
        "action_label": "Continue",
        "action_url": "#",
    },
    {
        "session_id": "WS-022",
        "procedure_name": "Cholecystectomy",
        "operating_room": "OR-1",
        "kit_name": "Delivery Kit",
        "operator_name": "Daniel Lee",
        "capture_station_name": "Station CDE-01",
        "started_at": "12:44",
        "status_label": "With Discrepancies",
        "status_variant": "danger",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-025",
        "procedure_name": "Hernia Repair",
        "operating_room": "OR-2",
        "kit_name": "Suture Kit",
        "operator_name": "Alex Morgan",
        "capture_station_name": "Station CDE-02",
        "started_at": "15:05",
        "status_label": "Pending Validation",
        "status_variant": "pending",
        "action_label": "Review",
        "action_url": "#",
    },
]

# Session History (Closed / Cancelled)
PREVIEW_SESSION_HISTORY = [
    {
        "session_id": "WS-023",
        "procedure_name": "Hernia Repair",
        "operating_room": "OR-2",
        "kit_name": "General Surgery Kit",
        "operator_name": "Carlos Rivera",
        "started_at": "2024-01-15 08:30",
        "closed_at": "2024-01-15 10:15",
        "status_label": "Closed",
        "status_variant": "success",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-021",
        "procedure_name": "Appendectomy",
        "operating_room": "OR-3",
        "kit_name": "Delivery Kit",
        "operator_name": "Alex Morgan",
        "started_at": "2024-01-14 14:00",
        "closed_at": "2024-01-14 16:30",
        "status_label": "Closed",
        "status_variant": "success",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-019",
        "procedure_name": "Cholecystectomy",
        "operating_room": "OR-1",
        "kit_name": "Suture Kit",
        "operator_name": "Alex Morgan",
        "started_at": "2024-01-13 09:00",
        "closed_at": None,
        "status_label": "Cancelled",
        "status_variant": "neutral",
        "action_label": "View Details",
        "action_url": "#",
    },
]

# New Counting Session — Session Setup options
PREVIEW_OPERATIONS = [
    {
        "operation_id": "op-1",
        "procedure_name": "Appendectomy",
        "operating_room_name": "OR-3",
        "patient_name": "Maria García",
        "physician_name": "Dr. James Wilson",
    },
    {
        "operation_id": "op-2",
        "procedure_name": "Cholecystectomy",
        "operating_room_name": "OR-1",
        "patient_name": "Carlos Rivera",
        "physician_name": "Dr. Sarah Chen",
    },
]

PREVIEW_AVAILABLE_KITS = [
    {"kit_id": "delivery-kit", "kit_name": "Delivery Kit"},
    {"kit_id": "suture-kit", "kit_name": "Suture Kit"},
    {"kit_id": "general-surgery-kit", "kit_name": "General Surgery Kit"},
]

PREVIEW_AVAILABLE_CAPTURE_STATIONS = [
    {"station_id": "cde-01", "station_name": "Station CDE-01"},
    {"station_id": "cde-02", "station_name": "Station CDE-02"},
]

# New Counting Session — Expected Inventory Preview (Delivery Kit contents)
PREVIEW_EXPECTED_INVENTORY = [
    {"instrument_family_name": "Kelly Clamp", "expected_quantity": 4},
    {"instrument_family_name": "Foerster Sponge Forceps", "expected_quantity": 2},
    {"instrument_family_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2},
    {"instrument_family_name": "Mayo Scissors", "expected_quantity": 1},
    {"instrument_family_name": "Allis Tissue Forceps", "expected_quantity": 2},
]

# New Counting Session — Instrument Readiness (presentation-ready values; see section 12/22
# of the implementation prompt: the template does not infer variants from raw counts)
PREVIEW_INSTRUMENT_READINESS = {
    "expected_count": 11,
    "available_count": 11,
    "missing_count": 0,
    "missing_variant": "neutral",
    "readiness_variant": "success",
    "ready": True,
    "status_label": "Instrument Set Ready",
}

# Active Counting Session (matches the approved Figma example: WS-026, one discrepancy)
PREVIEW_ACTIVE_SESSION = {
    "session_id": "WS-026",
    "procedure_name": "Appendectomy",
    "operating_room": "OR-3",
    "patient_name": "Maria García",
    "physician_name": "Dr. James Wilson",
    "kit_name": "Delivery Kit",
    "capture_station_name": "Station CDE-01",
    "operator_name": "Alex Morgan",
    "status_label": "In Progress",
    "status_variant": "info",
}

PREVIEW_ACTIVE_INSTRUMENT_COUNTS = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 1, "difference": -1, "status_label": "Discrepancy", "status_variant": "danger"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Allis Tissue Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

PREVIEW_ACTIVE_SESSION_SUMMARY = {
    "expected_total": 11,
    "counted_total": 10,
    "discrepancy_count": 1,
    "discrepancy_variant": "danger",
}

PREVIEW_ACTIVE_ALERTS = [
    {
        "variant": "danger",
        "title": "Current Alerts",
        "message": "Instrument discrepancy detected - Mayo-Hegar Needle Holder: Expected 2, Counted 1, Difference -1",
    },
]

# Count Validation — No Discrepancy (matches the approved Figma example: WS-024)
PREVIEW_VALIDATION_SESSION_NO_DISCREPANCY = {
    "session_id": "WS-024",
    "procedure_name": "Appendectomy",
    "operating_room": "OR-3",
    "patient_name": "Maria García",
    "physician_name": "Dr. James Wilson",
    "kit_name": "Delivery Kit",
    "operator_name": "Alex Morgan",
    "status_label": "Active",
    "status_variant": "info",
}

PREVIEW_PRE_POST_NO_DISCREPANCY = {
    "expected_total": 11,
    "counted_total": 11,
    "difference": 0,
    "status_label": "All instruments accounted for",
    "status_variant": "success",
}

PREVIEW_COUNT_COMPARISON_NO_DISCREPANCY = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Allis Tissue Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

# Count Validation — Discrepancy (matches the approved Figma example: WS-026)
PREVIEW_VALIDATION_SESSION_DISCREPANCY = {
    "session_id": "WS-026",
    "procedure_name": "Appendectomy",
    "operating_room": "OR-3",
    "patient_name": "Maria García",
    "physician_name": "Dr. James Wilson",
    "kit_name": "Delivery Kit",
    "operator_name": "Alex Morgan",
    "status_label": "Active",
    "status_variant": "info",
}

PREVIEW_PRE_POST_DISCREPANCY = {
    "expected_total": 11,
    "counted_total": 10,
    "difference": -1,
    "status_label": "Instrument discrepancy requires review",
    "status_variant": "warning",
}

PREVIEW_COUNT_COMPARISON_DISCREPANCY = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 1, "difference": -1, "status_label": "Discrepancy", "status_variant": "danger"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Allis Tissue Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

PREVIEW_DISCREPANCY_SUMMARY = {
    "session_id": "WS-026",
    "instrument_name": "Mayo-Hegar Needle Holder",
    "expected_quantity": 2,
    "counted_quantity": 1,
}

# Closed Session Details (matches the approved Figma example: WS-024, General Surgery Kit)
PREVIEW_CLOSED_SESSION = {
    "session_id": "WS-024",
    "procedure_name": "Hernia Repair",
    "operating_room": "OR-2",
    "patient_name": "Carlos Rivera",
    "physician_name": "Dr. Sarah Chen",
    "kit_name": "General Surgery Kit",
    "operator_name": "Alex Morgan",
    "capture_station_name": "Station CDE-02",
    "started_at": "2024-01-15 08:30",
    "closed_at": "2024-01-15 10:15",
    "status_label": "Closed",
    "status_variant": "success",
}

PREVIEW_PRE_PROCEDURE_STATUS = {"expected_count": 11, "status_label": "Complete", "status_variant": "success"}
PREVIEW_POST_PROCEDURE_STATUS = {"validated_count": 11, "status_label": "Complete", "status_variant": "success"}
PREVIEW_CLOSURE_SUMMARY = {"variant": "success", "message": "All Instruments Accounted For"}

PREVIEW_VALIDATED_COUNT = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Allis Tissue Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

PREVIEW_ACTIVITY_LOG = [
    {"timestamp": "08:30", "event_label": "Session Started", "actor_name": "Alex Morgan", "details": "General Surgery Kit loaded"},
    {"timestamp": "08:36", "event_label": "Count Recorded", "actor_name": "Alex Morgan", "details": "Initial count submitted"},
    {"timestamp": "10:10", "event_label": "Count Validated", "actor_name": "Alex Morgan", "details": "All instruments accounted for"},
    {"timestamp": "10:15", "event_label": "Session Closed", "actor_name": "Alex Morgan", "details": "Session finalized"},
]

# Counting Sessions demo state shown after a discrepancy review has been submitted (matches
# the approved "counting-sessions-review-submitted" Figma reference: WS-026 becomes Pending
# Review). This intentionally replaces PREVIEW_SESSIONS's WS-024 row with WS-026 rather than
# mutating it, since that is exactly what the approved reference shows.
PREVIEW_SESSIONS_AFTER_REVIEW_SUBMITTED = [
    {
        "session_id": "WS-026",
        "procedure_name": "Appendectomy",
        "operating_room": "OR-3",
        "kit_name": "Delivery Kit",
        "operator_name": "Alex Morgan",
        "capture_station_name": "Station CDE-01",
        "started_at": "14:32",
        "status_label": "Pending Review",
        "status_variant": "pending",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-022",
        "procedure_name": "Cholecystectomy",
        "operating_room": "OR-1",
        "kit_name": "Delivery Kit",
        "operator_name": "Daniel Lee",
        "capture_station_name": "Station CDE-01",
        "started_at": "12:44",
        "status_label": "With Discrepancies",
        "status_variant": "danger",
        "action_label": "View Details",
        "action_url": "#",
    },
    {
        "session_id": "WS-025",
        "procedure_name": "Hernia Repair",
        "operating_room": "OR-2",
        "kit_name": "Suture Kit",
        "operator_name": "Alex Morgan",
        "capture_station_name": "Station CDE-02",
        "started_at": "15:05",
        "status_label": "Pending Validation",
        "status_variant": "pending",
        "action_label": "Review",
        "action_url": "#",
    },
]


PREVIEW_SUPERVISOR_CURRENT_USER = {
    "name": "Sophia Turner",
    "role_label": "Supervisor CDE / Quality",
    "profile_role_label": "Supervisor CDE / Quality",
    "email": "sophia.turner@institution.edu",
    "institution": "Hospital Institution",
    "status_label": "Active",
    "status_variant": "success",
    "avatar_url": None,
}

PREVIEW_SUPERVISOR_DASHBOARD_STATS = {
    "sessions_today": 12,
    "active_sessions": 3,
    "pending_reviews": 2,
    "open_discrepancies": 3,
}

PREVIEW_SUPERVISOR_REVIEW_STATUS = [
    {"label": "Pending Review", "value": 2, "variant": "warning"},
    {"label": "With Discrepancies", "value": 3, "variant": "danger"},
    {"label": "Closed Today", "value": 9, "variant": "success"},
]

PREVIEW_SESSIONS_REQUIRING_ATTENTION = [
    {
        "session_id": "WS-026", "procedure_label": "Appendectomy - OR-3", "kit_name": "Delivery Kit",
        "operator_name": "Alex Morgan", "issue_label": "Missing instrument", "submitted_at": "15:42",
        "status_label": "Pending Review", "status_variant": "pending", "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-025", "procedure_label": "Cholecystectomy - OR-1", "kit_name": "Suture Kit",
        "operator_name": "Emily Carter", "issue_label": "Counting error", "submitted_at": "14:20",
        "status_label": "Pending Review", "status_variant": "pending", "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-024", "procedure_label": "Hernia Repair - OR-2", "kit_name": "General Surgery Kit",
        "operator_name": "Daniel Lee", "issue_label": "Instrument mismatch", "submitted_at": "11:15",
        "status_label": "Under Review", "status_variant": "info", "action_label": "View Details", "action_url": "#",
    },
]

# Supervisor Sessions (monitoring queue)
PREVIEW_SUPERVISOR_SESSIONS = [
    {
        "session_id": "WS-026", "procedure_name": "Appendectomy", "operating_room": "OR-3",
        "kit_name": "Delivery Kit", "operator_name": "Alex Morgan", "capture_station_name": "Station CDE-01",
        "started_at": "15:20", "status_label": "Pending Review", "status_variant": "pending",
        "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-025", "procedure_name": "Cholecystectomy", "operating_room": "OR-1",
        "kit_name": "Suture Kit", "operator_name": "Emily Carter", "capture_station_name": "Station CDE-02",
        "started_at": "14:10", "status_label": "Active", "status_variant": "info",
        "action_label": "View Details", "action_url": "#",
    },
    {
        "session_id": "WS-024", "procedure_name": "Hernia Repair", "operating_room": "OR-2",
        "kit_name": "General Surgery Kit", "operator_name": "Daniel Lee", "capture_station_name": "Station CDE-01",
        "started_at": "11:30", "status_label": "With Discrepancies", "status_variant": "danger",
        "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-023", "procedure_name": "Appendectomy", "operating_room": "OR-1",
        "kit_name": "Delivery Kit", "operator_name": "Sophia Turner", "capture_station_name": "Station CDE-03",
        "started_at": "09:45", "status_label": "Closed", "status_variant": "success",
        "action_label": "View Details", "action_url": "#",
    },
]

# Supervisor Session History (Closed / Cancelled)
PREVIEW_SUPERVISOR_SESSION_HISTORY = [
    {
        "session_id": "WS-023", "procedure_name": "Appendectomy", "operating_room": "OR-1",
        "kit_name": "Suture Kit", "operator_name": "Emily Carter",
        "started_at": "13:18", "closed_at": "13:42",
        "status_label": "Closed", "status_variant": "success", "action_label": "View Details", "action_url": "#",
    },
    {
        "session_id": "WS-021", "procedure_name": "Cholecystectomy", "operating_room": "OR-3",
        "kit_name": "General Surgery Kit", "operator_name": "Sophia Turner",
        "started_at": "11:57", "closed_at": "12:30",
        "status_label": "Closed", "status_variant": "success", "action_label": "View Details", "action_url": "#",
    },
    {
        "session_id": "WS-019", "procedure_name": "Hernia Repair", "operating_room": "OR-2",
        "kit_name": "Delivery Kit", "operator_name": "Daniel Lee",
        "started_at": "09:15", "closed_at": "09:48",
        "status_label": "Closed", "status_variant": "success", "action_label": "View Details", "action_url": "#",
    },
    {
        "session_id": "WS-018", "procedure_name": "Appendectomy", "operating_room": "OR-1",
        "kit_name": "Suture Kit", "operator_name": "Alex Morgan",
        "started_at": "08:30", "closed_at": None,
        "status_label": "Cancelled", "status_variant": "neutral", "action_label": "View Details", "action_url": "#",
    },
]

# Discrepancies — default state (matches the approved "supervisor-discrepancies" reference)
PREVIEW_SUPERVISOR_DISCREPANCIES_SUMMARY = {"pending_reviews": 2, "open_discrepancies": 3, "reviewed_today": 5}

PREVIEW_SUPERVISOR_DISCREPANCIES = [
    {
        "session_id": "WS-026", "procedure_name": "Appendectomy", "operating_room": "OR-3",
        "kit_name": "Delivery Kit", "operator_name": "Alex Morgan", "instrument_name": "Mayo-Hegar Needle Holder",
        "expected_quantity": 2, "counted_quantity": 1, "difference": -1, "reason": "Missing instrument",
        "status_label": "Pending Review", "status_variant": "pending", "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-025", "procedure_name": "Cholecystectomy", "operating_room": "OR-1",
        "kit_name": "Suture Kit", "operator_name": "Emily Carter", "instrument_name": "Suture Needle Set",
        "expected_quantity": 6, "counted_quantity": 5, "difference": -1, "reason": "Counting error",
        "status_label": "Pending Review", "status_variant": "pending", "action_label": "Review", "action_url": "#",
    },
    {
        "session_id": "WS-019", "procedure_name": "Hernia Repair", "operating_room": "OR-2",
        "kit_name": "General Surgery Kit", "operator_name": "Daniel Lee", "instrument_name": "Retractor Blade",
        "expected_quantity": 3, "counted_quantity": 2, "difference": -1, "reason": "Instrument mismatch",
        "status_label": "Reviewed", "status_variant": "success", "action_label": "View Details", "action_url": "#",
    },
]

# Discrepancies — Post Approval state (matches "supervisor-discrepancies-post-approval"):
# WS-026 becomes Approved / View Details; WS-025 and WS-019 are unchanged.
PREVIEW_SUPERVISOR_DISCREPANCIES_POST_APPROVAL = [
    dict(PREVIEW_SUPERVISOR_DISCREPANCIES[0], status_label="Approved", status_variant="success",
         action_label="View Details", action_url="#"),
    PREVIEW_SUPERVISOR_DISCREPANCIES[1],
    PREVIEW_SUPERVISOR_DISCREPANCIES[2],
]

# Discrepancies — Post Correction state (matches "supervisor-discrepancies-post-correction"):
# WS-026 becomes Correction Required / Review; WS-025 and WS-019 are unchanged.
PREVIEW_SUPERVISOR_DISCREPANCIES_POST_CORRECTION = [
    dict(PREVIEW_SUPERVISOR_DISCREPANCIES[0], status_label="Correction Required", status_variant="warning",
         action_label="Review", action_url="#"),
    PREVIEW_SUPERVISOR_DISCREPANCIES[1],
    PREVIEW_SUPERVISOR_DISCREPANCIES[2],
]

# Discrepancy Review (matches the approved "supervisor-discrepancy-review" reference: WS-026)
PREVIEW_DISCREPANCY_REVIEW_SESSION = {
    "session_id": "WS-026", "procedure_name": "Appendectomy", "operating_room": "OR-3",
    "patient_name": "Maria García", "physician_name": "Dr. James Wilson",
    "kit_name": "Delivery Kit", "operator_name": "Alex Morgan", "capture_station_name": "Station CDE-01",
    "submitted_at": "15:42", "status_label": "Pending Review", "status_variant": "pending",
}

PREVIEW_DISCREPANCY_REVIEW_PRE_POST = {
    "expected_total": 11, "counted_total": 10, "difference": -1,
    "status_label": "Instrument discrepancy requires Supervisor review", "status_variant": "warning",
}

PREVIEW_DISCREPANCY_REVIEW_COUNT_COMPARISON = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 1, "difference": -1, "status_label": "Discrepancy", "status_variant": "danger"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Allis Tissue Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

PREVIEW_OPERATOR_SUBMISSION = {
    "reason": "Missing instrument",
    "verification_notes": "Searched the tray and back table; Mayo-Hegar Needle Holder could not be located before the incision count was finalized.",
}

# Supervisor Session Details (matches the approved "supervisor-session-details" reference: WS-024)
PREVIEW_SUPERVISOR_SESSION_DETAILS = {
    "session_id": "WS-024", "procedure_name": "Hernia Repair", "operating_room": "OR-2",
    "patient_name": "Carlos Mendez", "physician_name": "Dr. Sarah Chen",
    "kit_name": "General Surgery Kit", "operator_name": "Alex Morgan", "capture_station_name": "Station CDE-01",
    "status_label": "Active", "status_variant": "info",
}

PREVIEW_SUPERVISOR_PRE_POST_STATUS = {
    "expected_count": 9, "counted_count": 8, "difference": -1, "difference_variant": "warning",
}

PREVIEW_SUPERVISOR_COUNT_SUMMARY = [
    {"instrument_name": "Kelly Clamp", "expected_quantity": 4, "counted_quantity": 4, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Foerster Sponge Forceps", "expected_quantity": 2, "counted_quantity": 2, "difference": 0, "status_label": "Match", "status_variant": "success"},
    {"instrument_name": "Mayo-Hegar Needle Holder", "expected_quantity": 2, "counted_quantity": 1, "difference": -1, "status_label": "Discrepancy", "status_variant": "danger"},
    {"instrument_name": "Mayo Scissors", "expected_quantity": 1, "counted_quantity": 1, "difference": 0, "status_label": "Match", "status_variant": "success"},
]

PREVIEW_SUPERVISOR_CORRECTION_LOG = [
    {"timestamp": "14:45", "instrument_name": "Kelly Clamp", "previous_quantity": 3, "corrected_quantity": 4, "reason": "Counting error"},
]

# Discrepancy Details' exact Figma content is cropped out of the approved reference (see
# templates/supervisor/sessions/details.html) — composed from the same discrepancy concept
# used elsewhere (Discrepancies table / Discrepancy Review) rather than invented freely.
PREVIEW_SUPERVISOR_DISCREPANCY_DETAILS = {
    "instrument_name": "Mayo-Hegar Needle Holder",
    "expected_quantity": 2, "counted_quantity": 1, "difference": -1,
    "reason": "Missing instrument",
    "status_label": "Pending Review", "status_variant": "pending",
    "reviewed_by": "Not yet reviewed",
}

PREVIEW_SUPERVISOR_SESSION_ACTIVITY = [
    {"timestamp": "14:32", "event_label": "Session Started", "actor_name": "Alex Morgan", "details": "Delivery Kit loaded"},
    {"timestamp": "14:38", "event_label": "Count Recorded", "actor_name": "Alex Morgan", "details": "Initial instrument count"},
    {"timestamp": "14:42", "event_label": "Discrepancy Detected", "actor_name": "Alex Morgan", "details": "Mayo-Hegar difference -1"},
]

# Supervisor Reports (matches the approved "supervisor-reports" reference)
PREVIEW_SUPERVISOR_REPORTS = [
    {
        "icon": "document", "title": "Session Summary",
        "description": "Overview of completed, active, and cancelled counting sessions.",
        "last_generated_label": "Last generated: 10 mins ago", "export_url": "#", "view_report_url": "#",
    },
    {
        "icon": "alert_triangle", "title": "Discrepancy Summary",
        "description": "Summary of unresolved and resolved discrepancies.",
        "last_generated_label": "Last generated: 1 hour ago", "export_url": "#", "view_report_url": "#",
    },
    {
        "icon": "clipboard", "title": "Kit Activity",
        "description": "Review counting activity grouped by kit.",
        "last_generated_label": "Last generated: 3 hours ago", "export_url": "#", "view_report_url": "#",
    },
    {
        "icon": "user", "title": "Operator Activity",
        "description": "Review counting activity and discrepancy patterns by operator.",
        "last_generated_label": "Last generated: Yesterday", "export_url": "#", "view_report_url": "#",
    },
]

# Supervisor Indicators (matches the approved "supervisor-indicators" reference)
PREVIEW_INDICATOR_STATS = {
    "total_sessions": 156, "closed_sessions": 142, "sessions_with_discrepancies": 8, "pending_reviews": 2,
}

PREVIEW_DISCREPANCIES_BY_REASON = [
    {"label": "Missing instrument", "value": 4, "variant": "danger"},
    {"label": "Counting error", "value": 2, "variant": "warning"},
    {"label": "Instrument mismatch", "value": 1, "variant": "neutral"},
    {"label": "Other", "value": 1, "variant": "primary"},
]

PREVIEW_SESSION_STATUS_CHART = {
    "total_label": 156,
    "segments": [
        {"label": "Closed", "percent": 91, "variant": "success"},
        {"label": "Active", "percent": 2, "variant": "info"},
        {"label": "With Discrepancies", "percent": 5, "variant": "danger"},
        {"label": "Pending Review", "percent": 2, "variant": "warning"},
    ],
}

PREVIEW_DISCREPANCIES_BY_KIT = [
    {"label": "Delivery Kit", "count": 4},
    {"label": "Suture Kit", "count": 2},
    {"label": "General Surgery Kit", "count": 2},
]

# Supervisor Audit Log (matches the approved "supervisor-audit-log" reference)
PREVIEW_AUDIT_LOG_ENTRIES = [
    {"timestamp": "15:20", "user_name": "Alex Morgan", "role_label": "Operator", "event_label": "Session Started", "event_variant": "default", "resource_id": "WS-026", "details": "Delivery Kit loaded"},
    {"timestamp": "15:34", "user_name": "Alex Morgan", "role_label": "Operator", "event_label": "Count Recorded", "event_variant": "default", "resource_id": "WS-026", "details": "Initial instrument count"},
    {"timestamp": "15:38", "user_name": "Alex Morgan", "role_label": "Operator", "event_label": "Discrepancy Detected", "event_variant": "danger", "resource_id": "WS-026", "details": "Mayo-Hegar difference -1"},
    {"timestamp": "15:42", "user_name": "Alex Morgan", "role_label": "Operator", "event_label": "Submitted for Review", "event_variant": "default", "resource_id": "WS-026", "details": "Missing instrument"},
    {"timestamp": "15:48", "user_name": "Sophia Turner", "role_label": "Supervisor", "event_label": "Review Approved", "event_variant": "success", "resource_id": "WS-026", "details": "Discrepancy reviewed"},
]


def build_supervisor_nav_urls():
    """
    Shared Supervisor sidebar hrefs, preview-only — same purpose as build_nav_urls() below,
    kept as a separate function since the Supervisor sidebar has a different set of nav keys.
    """
    return {
        "dashboard": url_for("supervisor_dashboard_preview"),
        "sessions": url_for("supervisor_sessions_preview"),
        "discrepancies": url_for("supervisor_discrepancies_preview"),
        "session_history": url_for("supervisor_session_history_preview"),
        "profile": url_for("supervisor_profile_preview"),
        "reports": url_for("supervisor_reports_preview"),
        "indicators": url_for("supervisor_indicators_preview"),
        "audit_log": url_for("supervisor_audit_log_preview"),
    }


def build_nav_urls():
    """
    Shared Operator sidebar hrefs, preview-only.

    partials/sidebar_operator.html reads an optional `nav_urls` context dict and falls back
    to "#" for any key it doesn't find — so passing this here does not require any change
    to how the production application will eventually use that same template; it only makes
    the sidebar clickable between the pages implemented so far in this preview.
    """
    return {
        "dashboard": url_for("operator_dashboard_preview"),
        "counting_sessions": url_for("operator_sessions_preview"),
        "new_session": url_for("operator_new_session_preview"),
        "session_history": url_for("operator_session_history_preview"),
        "profile": url_for("operator_profile_preview"),
    }


# ---------------------------------------------------------------------------
# Preview-only routes
# ---------------------------------------------------------------------------
# url_for() usage was checked across every template involved in this preview (base.html,
# auth/sign_in.html, operator/dashboard.html, every operator/sessions/*.html page, the two
# components/modals/*.html partials, and their shared partials/macros): the only url_for()
# calls made *from Jinja* are url_for('static', ...), which Flask resolves automatically
# from static_folder above. The sidebar's nav_urls, and every other preview-to-preview link
# (Start Session, Validate Count, Confirm Validation's modals, Close Session, Submit for
# Review, Save Progress/Review, Back to Sessions, etc.) are resolved in Python and handed to
# templates as plain context strings, so no additional dummy endpoints are required beyond
# the preview routes defined below.

@app.route("/")
def index():
    """Preview-only convenience redirect so http://127.0.0.1:5001/ goes somewhere useful."""
    return redirect(url_for("sign_in_preview"))


@app.route("/preview/sign-in", methods=["GET", "POST"])
def sign_in_preview():
    """
    Renders templates/auth/sign_in.html.

    The real template's form has action="#", so submitting it POSTs back to this same
    route. That POST is handled here only to redirect to the dashboard preview — it does
    NOT authenticate anyone and must not be mistaken for real sign-in behavior.
    """
    if request.method == "POST":
        return redirect(url_for("operator_dashboard_preview"))

    return render_template(
        "auth/sign_in.html",
        current_locale=PREVIEW_LOCALE,
    )


@app.route("/preview/operator/profile")
def operator_profile_preview():
    """Renders the shared My Profile page with Operator CDE context."""
    return render_template(
        "shared/profile.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        profile_role="operator",
        nav_urls=build_nav_urls(),
    )


@app.route("/preview/operator/dashboard")
def operator_dashboard_preview():
    """Renders templates/operator/dashboard.html with local demo context only."""
    return render_template(
        "operator/dashboard.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        dashboard_stats=PREVIEW_DASHBOARD_STATS,
        sessions_by_day=PREVIEW_SESSIONS_BY_DAY,
        session_status_breakdown=PREVIEW_SESSION_STATUS_BREAKDOWN,
        recent_sessions=PREVIEW_RECENT_SESSIONS,
    )


@app.route("/preview/operator/sessions")
def operator_sessions_preview():
    """
    Renders templates/operator/sessions/list.html (Counting Sessions) with demo context.

    ?feedback=review_submitted (linked from the Supervisor Review Required modal's "Submit
    for Review", via the Count Validation discrepancy preview route) shows the same
    "Review submitted successfully." banner as the approved Figma reference and swaps in the
    matching demo list state (WS-026 becomes Pending Review) — preview-only, no real
    cross-role data persistence happens here.
    """
    feedback_banner = None
    sessions = PREVIEW_SESSIONS
    if request.args.get("feedback") == "review_submitted":
        feedback_banner = {
            "variant": "success",
            "title": "Review submitted successfully.",
            "description": "Session WS-026 has been sent to the Supervisor for review.",
        }
        sessions = PREVIEW_SESSIONS_AFTER_REVIEW_SUBMITTED

    return render_template(
        "operator/sessions/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        feedback_banner=feedback_banner,
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=8,
        current_page=1,
        total_pages=3,
    )


@app.route("/preview/operator/sessions/new", methods=["GET", "POST"])
def operator_new_session_preview():
    """
    Renders templates/operator/sessions/new.html (New Counting Session) with demo context.

    The real template's Start Session button submits action="{{ start_session_url }}", which
    is set here to this same route (mirroring sign_in_preview's GET/POST pattern) — a POST
    redirects to the Active Counting Session preview. It does NOT create a WorkSession.
    """
    if request.method == "POST":
        return redirect(url_for("operator_active_session_preview"))

    return render_template(
        "operator/sessions/new.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        operations=PREVIEW_OPERATIONS,
        selected_operation=PREVIEW_OPERATIONS[0],
        available_kits=PREVIEW_AVAILABLE_KITS,
        selected_kit=PREVIEW_AVAILABLE_KITS[0],
        available_capture_stations=PREVIEW_AVAILABLE_CAPTURE_STATIONS,
        selected_capture_station=PREVIEW_AVAILABLE_CAPTURE_STATIONS[0],
        expected_inventory=PREVIEW_EXPECTED_INVENTORY,
        instrument_readiness=PREVIEW_INSTRUMENT_READINESS,
        start_session_url=url_for("operator_new_session_preview"),
    )


@app.route("/preview/operator/session-history")
def operator_session_history_preview():
    """Renders templates/operator/sessions/history.html (Session History) with demo context."""
    return render_template(
        "operator/sessions/history.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        session_history=PREVIEW_SESSION_HISTORY,
        sessions_shown_count=len(PREVIEW_SESSION_HISTORY),
        sessions_total_count=12,
        current_page=1,
        total_pages=4,
    )


@app.route("/preview/operator/sessions/active")
def operator_active_session_preview():
    """
    Renders templates/operator/sessions/active.html with demo context.

    ?feedback=progress_saved (linked from "Save Progress") shows a "Progress saved
    successfully." banner on reload — preview-only; nothing is actually persisted.
    """
    feedback_banner = None
    if request.args.get("feedback") == "progress_saved":
        feedback_banner = {"variant": "success", "title": "Progress saved successfully."}

    return render_template(
        "operator/sessions/active.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        feedback_banner=feedback_banner,
        session=PREVIEW_ACTIVE_SESSION,
        instrument_counts=PREVIEW_ACTIVE_INSTRUMENT_COUNTS,
        session_summary=PREVIEW_ACTIVE_SESSION_SUMMARY,
        current_alerts=PREVIEW_ACTIVE_ALERTS,
        cancel_session_url=url_for("operator_sessions_preview"),
        save_progress_url=url_for("operator_active_session_preview") + "?feedback=progress_saved",
        validate_count_url=url_for("operator_validation_discrepancy_preview"),
    )


@app.route("/preview/operator/sessions/validation")
def operator_validation_preview():
    """
    Renders templates/operator/sessions/validation.html in its No Discrepancy state
    (has_discrepancy=False), matching the approved Count Validation Figma reference (WS-024).
    """
    return render_template(
        "operator/sessions/validation.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        has_discrepancy=False,
        session=PREVIEW_VALIDATION_SESSION_NO_DISCREPANCY,
        pre_post_comparison=PREVIEW_PRE_POST_NO_DISCREPANCY,
        count_comparison=PREVIEW_COUNT_COMPARISON_NO_DISCREPANCY,
        edit_count_url=url_for("operator_active_session_preview"),
        close_session_url=url_for("operator_closed_session_details_preview"),
    )


@app.route("/preview/operator/sessions/validation-discrepancy")
def operator_validation_discrepancy_preview():
    """
    Renders templates/operator/sessions/validation.html in its Discrepancy state
    (has_discrepancy=True), matching the approved Count Validation with Discrepancy Figma
    reference (WS-026, Mayo-Hegar Needle Holder).

    ?feedback=review_saved (linked from "Save Review") shows a "Review saved successfully."
    banner on reload — preview-only; nothing is actually persisted.
    """
    feedback_banner = None
    if request.args.get("feedback") == "review_saved":
        feedback_banner = {"variant": "success", "title": "Review saved successfully."}

    return render_template(
        "operator/sessions/validation.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        feedback_banner=feedback_banner,
        has_discrepancy=True,
        session=PREVIEW_VALIDATION_SESSION_DISCREPANCY,
        pre_post_comparison=PREVIEW_PRE_POST_DISCREPANCY,
        count_comparison=PREVIEW_COUNT_COMPARISON_DISCREPANCY,
        discrepancy_summary=PREVIEW_DISCREPANCY_SUMMARY,
        edit_count_url=url_for("operator_active_session_preview"),
        save_review_url=url_for("operator_validation_discrepancy_preview") + "?feedback=review_saved",
        submit_for_review_url=url_for("operator_sessions_preview") + "?feedback=review_submitted",
    )


@app.route("/preview/operator/sessions/closed")
def operator_closed_session_details_preview():
    """Renders templates/operator/sessions/closed_details.html with demo context."""
    return render_template(
        "operator/sessions/closed_details.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_CURRENT_USER,
        nav_urls=build_nav_urls(),
        session=PREVIEW_CLOSED_SESSION,
        pre_procedure_status=PREVIEW_PRE_PROCEDURE_STATUS,
        post_procedure_status=PREVIEW_POST_PROCEDURE_STATUS,
        closure_summary=PREVIEW_CLOSURE_SUMMARY,
        validated_count=PREVIEW_VALIDATED_COUNT,
        activity_log=PREVIEW_ACTIVITY_LOG,
        back_to_sessions_url=url_for("operator_sessions_preview"),
        export_summary_url="#",
    )


# ---------------------------------------------------------------------------
# Supervisor CDE / Quality preview routes
# ---------------------------------------------------------------------------

@app.route("/preview/supervisor/profile")
def supervisor_profile_preview():
    """Renders the shared My Profile page with Supervisor CDE / Quality context."""
    return render_template(
        "shared/profile.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        profile_role="supervisor",
        nav_urls=build_supervisor_nav_urls(),
    )


@app.route("/preview/supervisor/dashboard")
def supervisor_dashboard_preview():
    """Renders templates/supervisor/dashboard.html with local demo context only."""
    return render_template(
        "supervisor/dashboard.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        dashboard_stats=PREVIEW_SUPERVISOR_DASHBOARD_STATS,
        sessions_by_day=PREVIEW_SESSIONS_BY_DAY,
        review_status=PREVIEW_SUPERVISOR_REVIEW_STATUS,
        sessions_requiring_attention=[
            dict(item, action_url=url_for("supervisor_discrepancy_review_preview"))
            if item["action_label"] == "Review"
            else dict(item, action_url=url_for("supervisor_session_details_preview"))
            for item in PREVIEW_SESSIONS_REQUIRING_ATTENTION
        ],
    )


@app.route("/preview/supervisor/sessions")
def supervisor_sessions_preview():
    """Renders templates/supervisor/sessions/list.html (Sessions) with demo context."""
    sessions = [
        dict(item, action_url=url_for("supervisor_discrepancy_review_preview"))
        if item["action_label"] == "Review"
        else dict(item, action_url=url_for("supervisor_session_details_preview"))
        for item in PREVIEW_SUPERVISOR_SESSIONS
    ]
    return render_template(
        "supervisor/sessions/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=24,
        current_page=1,
        total_pages=6,
    )


@app.route("/preview/supervisor/session-history")
def supervisor_session_history_preview():
    """Renders templates/supervisor/sessions/history.html (Session History) with demo context."""
    session_history = [
        dict(item, action_url=url_for("supervisor_session_details_preview"))
        for item in PREVIEW_SUPERVISOR_SESSION_HISTORY
    ]
    return render_template(
        "supervisor/sessions/history.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        session_history=session_history,
        sessions_shown_count=len(session_history),
        sessions_total_count=124,
        current_page=1,
        total_pages=31,
    )


@app.route("/preview/supervisor/sessions/details")
def supervisor_session_details_preview():
    """Renders templates/supervisor/sessions/details.html (read-only) with demo context."""
    return render_template(
        "supervisor/sessions/details.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        session=PREVIEW_SUPERVISOR_SESSION_DETAILS,
        pre_post_status=PREVIEW_SUPERVISOR_PRE_POST_STATUS,
        count_summary=PREVIEW_SUPERVISOR_COUNT_SUMMARY,
        correction_log=PREVIEW_SUPERVISOR_CORRECTION_LOG,
        discrepancy_details=PREVIEW_SUPERVISOR_DISCREPANCY_DETAILS,
        activity_log=PREVIEW_SUPERVISOR_SESSION_ACTIVITY,
        back_to_sessions_url=url_for("supervisor_sessions_preview"),
    )


@app.route("/preview/supervisor/discrepancies")
def supervisor_discrepancies_preview():
    """
    Renders templates/supervisor/discrepancies/list.html (Discrepancies) with demo context.

    This ONE template drives the default listing plus the Post Approval and Post Correction
    result states (section 26-28 of the implementation prompt) via ?feedback=review_approved
    or ?feedback=correction_requested — linked from the Approve Review / Request Correction
    modals on the Discrepancy Review preview. No real cross-role persistence happens here,
    matching the same preview-only pattern already used by the Operator harness.
    """
    feedback_banner = None
    discrepancies = PREVIEW_SUPERVISOR_DISCREPANCIES
    feedback = request.args.get("feedback")
    if feedback == "review_approved":
        feedback_banner = {
            "variant": "success",
            "title": "Review approved successfully.",
            "description": "Session WS-026 has been approved and may proceed to closure.",
        }
        discrepancies = PREVIEW_SUPERVISOR_DISCREPANCIES_POST_APPROVAL
    elif feedback == "correction_requested":
        feedback_banner = {
            "variant": "warning",
            "title": "Correction request sent successfully.",
            "description": "Session WS-026 has been returned to the Operator for correction.",
        }
        discrepancies = PREVIEW_SUPERVISOR_DISCREPANCIES_POST_CORRECTION

    discrepancies = [
        dict(item, action_url=url_for("supervisor_discrepancy_review_preview"))
        if item["action_label"] == "Review"
        else dict(item, action_url=url_for("supervisor_session_details_preview"))
        for item in discrepancies
    ]

    return render_template(
        "supervisor/discrepancies/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        feedback_banner=feedback_banner,
        discrepancies_summary=PREVIEW_SUPERVISOR_DISCREPANCIES_SUMMARY,
        discrepancies=discrepancies,
    )


@app.route("/preview/supervisor/discrepancies/post-approval")
def supervisor_discrepancies_post_approval_preview():
    """Convenience preview URL (section 42) that redirects to the Post Approval state."""
    return redirect(url_for("supervisor_discrepancies_preview", feedback="review_approved"))


@app.route("/preview/supervisor/discrepancies/post-correction")
def supervisor_discrepancies_post_correction_preview():
    """Convenience preview URL (section 42) that redirects to the Post Correction state."""
    return redirect(url_for("supervisor_discrepancies_preview", feedback="correction_requested"))


@app.route("/preview/supervisor/discrepancies/review")
def supervisor_discrepancy_review_preview():
    """
    Renders templates/supervisor/discrepancies/review.html with demo context.

    Approve Review / Request Correction open their respective modals client-side
    (data-modal-open); each modal's primary action is a plain link to the Discrepancies
    preview route with the matching ?feedback= flag — see supervisor_discrepancies_preview.
    """
    return render_template(
        "supervisor/discrepancies/review.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        session=PREVIEW_DISCREPANCY_REVIEW_SESSION,
        pre_post_comparison=PREVIEW_DISCREPANCY_REVIEW_PRE_POST,
        count_comparison=PREVIEW_DISCREPANCY_REVIEW_COUNT_COMPARISON,
        operator_submission=PREVIEW_OPERATOR_SUBMISSION,
        approve_review_url=url_for("supervisor_discrepancies_post_approval_preview"),
        request_correction_url=url_for("supervisor_discrepancies_post_correction_preview"),
    )


@app.route("/preview/supervisor/reports")
def supervisor_reports_preview():
    """Renders templates/supervisor/reports.html with demo context."""
    return render_template(
        "supervisor/reports.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        reports=PREVIEW_SUPERVISOR_REPORTS,
    )


@app.route("/preview/supervisor/indicators")
def supervisor_indicators_preview():
    """Renders templates/supervisor/indicators.html with demo context."""
    return render_template(
        "supervisor/indicators.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        indicator_stats=PREVIEW_INDICATOR_STATS,
        sessions_by_day=PREVIEW_SESSIONS_BY_DAY,
        discrepancies_by_reason=PREVIEW_DISCREPANCIES_BY_REASON,
        session_status_chart=PREVIEW_SESSION_STATUS_CHART,
        discrepancies_by_kit=PREVIEW_DISCREPANCIES_BY_KIT,
    )


@app.route("/preview/supervisor/audit-log")
def supervisor_audit_log_preview():
    """Renders templates/supervisor/audit_log.html with demo context."""
    return render_template(
        "supervisor/audit_log.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_SUPERVISOR_CURRENT_USER,
        nav_urls=build_supervisor_nav_urls(),
        audit_log_entries=PREVIEW_AUDIT_LOG_ENTRIES,
        events_shown_count=len(PREVIEW_AUDIT_LOG_ENTRIES),
        events_total_count=342,
        current_page=1,
        total_pages=69,
    )


# ---------------------------------------------------------------------------
# IT Administrator preview demo data
# ---------------------------------------------------------------------------

PREVIEW_ADMIN_CURRENT_USER = {
    "name": "Daniel Brooks",
    "role_label": "IT Administrator",
    "profile_role_label": "IT Administrator",
    "email": "daniel.brooks@institution.edu",
    "institution": "Hospital Institution",
    "status_label": "Active",
    "status_variant": "success",
    "avatar_url": None,
}

PREVIEW_ADMIN_DASHBOARD_STATS = {
    "active_users": 18,
    "instrument_families": 11,
    "registered_instruments": 96,
    "active_kits": 8,
}

PREVIEW_ADMIN_CATALOG_OVERVIEW = [
    {"label": "Instrument Families", "value": "11 Active"},
    {"label": "Instruments", "value": "96 Registered"},
    {"label": "Kits", "value": "8 Active"},
    {"label": "Capture Stations", "value": "3 Configured"},
]

PREVIEW_ADMIN_SYSTEM_OVERVIEW = [
    {"label": "Inactive Users", "value": 0},
    {"label": "Configured Roles", "value": 3},
    {"label": "Capture Stations", "value": 3},
    {"label": "Active Institution", "value": 1},
]

# Instrument Families (matches the approved "it-admin-instrument-families" reference)
PREVIEW_ADMIN_INSTRUMENT_FAMILY_CATEGORIES = [
    {"value": "hemostasis", "label": "Hemostasis"},
    {"value": "grasping", "label": "Grasping"},
    {"value": "suturing", "label": "Suturing"},
    {"value": "cutting", "label": "Cutting"},
    {"value": "retracting", "label": "Retracting"},
]

PREVIEW_ADMIN_INSTRUMENT_FAMILIES = [
    {"code": "FAM-001", "name": "Kelly Clamp", "category": "Hemostasis", "function": "Clamping blood vessels", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "FAM-002", "name": "Foerster Sponge Forceps", "category": "Grasping", "function": "Holding sponges and materials", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "FAM-003", "name": "Mayo-Hegar Needle Holder", "category": "Suturing", "function": "Holding surgical needles", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
]

# Edit Instrument Family (matches the approved "it-admin-edit-instrument-family" reference: FAM-001)
PREVIEW_ADMIN_INSTRUMENT_FAMILY_EDIT = {
    "code": "FAM-001",
    "name": "Kelly Clamp",
    "category": "hemostasis",
    "how_to_identify": "Look for serrated blades, straight or curved, with a locking ratchet mechanism near the finger rings. Kelly clamps have serrations on only the distal half of the jaws.",
    "classification_characteristics": "Surgical grade stainless steel, serrated jaws, finger-ring handles with locking ratchet.",
    "function": "Clamping blood vessels",
    "status": "active",
}

PREVIEW_ADMIN_INSTRUMENT_FAMILY_NEW = {
    "code": "", "name": "", "category": "", "how_to_identify": "",
    "classification_characteristics": "", "function": "", "status": "active",
}

# Instruments (matches the approved "it-admin-instruments" reference)
PREVIEW_ADMIN_INSTRUMENT_FAMILIES_FILTER = [
    {"value": "kelly-clamp", "label": "Kelly Clamp"},
    {"value": "foerster-sponge-forceps", "label": "Foerster Sponge Forceps"},
    {"value": "mayo-hegar-needle-holder", "label": "Mayo-Hegar Needle Holder"},
]

PREVIEW_ADMIN_INSTRUMENTS = [
    {"internal_code": "INS-001", "instrument_family_name": "Kelly Clamp", "cycle_status_label": "Available", "cycle_status_variant": "info", "active_status_label": "Active", "active_status_variant": "success", "edit_url": "#"},
    {"internal_code": "INS-002", "instrument_family_name": "Kelly Clamp", "cycle_status_label": "Sterilization", "cycle_status_variant": "warning", "active_status_label": "Active", "active_status_variant": "success", "edit_url": "#"},
    {"internal_code": "INS-003", "instrument_family_name": "Foerster Sponge Forceps", "cycle_status_label": "Available", "cycle_status_variant": "info", "active_status_label": "Active", "active_status_variant": "success", "edit_url": "#"},
    {"internal_code": "INS-004", "instrument_family_name": "Mayo-Hegar Needle Holder", "cycle_status_label": "Unavailable", "cycle_status_variant": "danger", "active_status_label": "Inactive", "active_status_variant": "neutral", "edit_url": "#"},
]

# New Instrument (matches the approved "it-admin-new-instrument" reference: INS-005 pre-suggested)
PREVIEW_ADMIN_INSTRUMENT_NEW = {"internal_code": "INS-005", "instrument_family": "kelly-clamp", "cycle_status": "available", "active_status": "active"}

# Edit Instrument (matches the approved "it-admin-edit-instrument" reference: INS-001)
PREVIEW_ADMIN_INSTRUMENT_EDIT = {"internal_code": "INS-001", "instrument_family": "kelly-clamp", "cycle_status": "available", "active_status": "active"}

# Kits (matches the approved "it-admin-kits" reference)
PREVIEW_ADMIN_KITS = [
    {"name": "Delivery Kit", "version": 1, "instrument_types": 5, "total_expected_instruments": 11, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"name": "Suture Kit", "version": 2, "instrument_types": 4, "total_expected_instruments": 8, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"name": "General Surgery Kit", "version": 1, "instrument_types": 8, "total_expected_instruments": 18, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
]

PREVIEW_ADMIN_KIT_INSTRUMENT_FAMILIES = [
    {"value": "kelly-clamp", "label": "Kelly Clamp"},
    {"value": "foerster-sponge-forceps", "label": "Foerster Sponge Forceps"},
    {"value": "mayo-hegar-needle-holder", "label": "Mayo-Hegar Needle Holder"},
    {"value": "mayo-scissors", "label": "Mayo Scissors"},
    {"value": "allis-tissue-forceps", "label": "Allis Tissue Forceps"},
]

# New Kit (matches the approved "it-admin-new-kit" reference: empty composition)
PREVIEW_ADMIN_KIT_NEW = {"name": "", "version": 1, "status": "active"}
PREVIEW_ADMIN_KIT_NEW_COMPOSITION = []

# Edit Kit (matches the approved "it-admin-edit-kit" reference: Delivery Kit, 5 rows)
PREVIEW_ADMIN_KIT_EDIT = {"name": "Delivery Kit", "version": 1, "status": "active"}
PREVIEW_ADMIN_KIT_EDIT_COMPOSITION = [
    {"instrument_family_value": "kelly-clamp", "expected_quantity": 4},
    {"instrument_family_value": "foerster-sponge-forceps", "expected_quantity": 2},
    {"instrument_family_value": "mayo-hegar-needle-holder", "expected_quantity": 2},
    {"instrument_family_value": "mayo-scissors", "expected_quantity": 1},
    {"instrument_family_value": "allis-tissue-forceps", "expected_quantity": 2},
]

# Users (matches the approved "it-admin-users" reference; same demo identities already used
# across the Operator and Supervisor previews)
PREVIEW_ADMIN_INSTITUTIONS = [{"value": "hospital-institution", "label": "Hospital Institution"}]
PREVIEW_ADMIN_ROLE_OPTIONS = [
    {"value": "operator", "label": "Operator CDE"},
    {"value": "supervisor", "label": "Supervisor CDE / Quality"},
    {"value": "admin", "label": "IT Administrator"},
]

PREVIEW_ADMIN_USERS = [
    {"name": "Alex Morgan", "email": "alex.morgan@institution.edu", "institution": "Hospital Institution", "role_label": "Operator CDE", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"name": "Sophia Turner", "email": "sophia.turner@institution.edu", "institution": "Hospital Institution", "role_label": "Supervisor CDE / Quality", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"name": "Daniel Brooks", "email": "daniel.brooks@institution.edu", "institution": "Hospital Institution", "role_label": "IT Administrator", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
]

# New User default (role defaults to Operator CDE, matching the approved reference)
PREVIEW_ADMIN_USER_NEW = {"name": "", "email": "", "institution": "hospital-institution", "role": "operator", "status": "active"}

# Edit User (matches the approved "it-admin-edit-user" reference: Alex Morgan)
PREVIEW_ADMIN_USER_EDIT = {
    "name": "Alex Morgan", "email": "alex.morgan@institution.edu", "institution": "hospital-institution",
    "role": "operator", "role_label": "Operator CDE", "status": "active",
}

# Roles (matches the approved "it-admin-roles" reference)
PREVIEW_ADMIN_ROLES = [
    {"code": "OPERATOR", "name": "Operator CDE", "description": "Performs instrument counting and validation activities.", "assigned_users": 10, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "SUPERVISOR", "name": "Supervisor CDE / Quality", "description": "Reviews sessions, discrepancies, and authorizes closure.", "assigned_users": 5, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "ADMIN", "name": "IT Administrator", "description": "Manages users, roles, catalogs, and system configuration.", "assigned_users": 3, "status_label": "Active", "status_variant": "success", "edit_url": "#"},
]

# Edit Role (matches the approved "it-admin-edit-role" reference: OPERATOR)
PREVIEW_ADMIN_ROLE_EDIT = {
    "code": "OPERATOR", "name": "Operator CDE",
    "description": "Performs instrument counting sessions and validates operational results.",
    "status": "active", "assigned_users": 10,
}

# Configuration (matches the approved "it-admin-configuration" reference)
PREVIEW_ADMIN_INSTITUTION = {"name": "Hospital Institution", "code": "HOSP-001", "status_label": "Active", "status_variant": "success"}
PREVIEW_ADMIN_INSTITUTION_EDIT = {"name": "Hospital Institution", "code": "HOSP-001", "status": "active"}

PREVIEW_ADMIN_CAPTURE_STATIONS = [
    {"code": "CDE-01", "name": "Station CDE-01", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "CDE-02", "name": "Station CDE-02", "status_label": "Active", "status_variant": "success", "edit_url": "#"},
    {"code": "CDE-03", "name": "Station CDE-03", "status_label": "Inactive", "status_variant": "neutral", "edit_url": "#"},
]

# New Capture Station (matches the approved "it-admin-new-capture-station" reference: CDE-04 pre-suggested)
PREVIEW_ADMIN_CAPTURE_STATION_NEW = {"code": "CDE-04", "name": "Station CDE-04", "status": "active"}

# Edit Capture Station (matches the approved "it-admin-edit-capture-station" reference: CDE-01)
PREVIEW_ADMIN_CAPTURE_STATION_EDIT = {"code": "CDE-01", "name": "Station CDE-01", "status": "active"}

PREVIEW_ADMIN_FEEDBACK_BANNERS = {
    "family_created": {"variant": "success", "title": "Instrument family created successfully."},
    "family_updated": {"variant": "success", "title": "Instrument family updated successfully."},
    "instrument_created": {"variant": "success", "title": "Instrument created successfully."},
    "instrument_updated": {"variant": "success", "title": "Instrument updated successfully."},
    "kit_created": {"variant": "success", "title": "Kit created successfully."},
    "kit_updated": {"variant": "success", "title": "Kit updated successfully."},
    "user_created": {"variant": "success", "title": "User created successfully."},
    "user_updated": {"variant": "success", "title": "User updated successfully."},
    "user_deactivated": {"variant": "warning", "title": "User deactivated successfully.", "description": "Alex Morgan no longer has access to the system. Historical records are preserved."},
    "password_changed": {"variant": "success", "title": "Password changed successfully."},
    "role_updated": {"variant": "success", "title": "Role updated successfully."},
    "institution_updated": {"variant": "success", "title": "Institution information updated successfully."},
    "capture_station_created": {"variant": "success", "title": "Capture station created successfully."},
    "capture_station_updated": {"variant": "success", "title": "Capture station updated successfully."},
}


def build_admin_nav_urls():
    """
    Shared IT Administrator sidebar hrefs, preview-only — same purpose as build_nav_urls()
    and build_supervisor_nav_urls() above, kept as a separate function since the Administrator
    sidebar has its own set of nav keys (section 6 of the implementation prompt).
    """
    return {
        "dashboard": url_for("admin_dashboard_preview"),
        "instrument_families": url_for("admin_instrument_families_preview"),
        "instruments": url_for("admin_instruments_preview"),
        "kits": url_for("admin_kits_preview"),
        "users": url_for("admin_users_preview"),
        "roles": url_for("admin_roles_preview"),
        "configuration": url_for("admin_configuration_preview"),
        "profile": url_for("admin_profile_preview"),
    }


# ---------------------------------------------------------------------------
# IT Administrator preview routes
# ---------------------------------------------------------------------------

@app.route("/preview/admin/profile")
def admin_profile_preview():
    """Renders the shared My Profile page with IT Administrator context."""
    return render_template(
        "shared/profile.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        profile_role="admin",
        nav_urls=build_admin_nav_urls(),
    )


@app.route("/preview/admin/dashboard")
def admin_dashboard_preview():
    """Renders templates/admin/dashboard.html with local demo context only."""
    return render_template(
        "admin/dashboard.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        dashboard_stats=PREVIEW_ADMIN_DASHBOARD_STATS,
        catalog_overview=PREVIEW_ADMIN_CATALOG_OVERVIEW,
        system_overview=PREVIEW_ADMIN_SYSTEM_OVERVIEW,
    )


@app.route("/preview/admin/instrument-families")
def admin_instrument_families_preview():
    """Renders templates/admin/instrument_families/list.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/instrument_families/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        instrument_families=PREVIEW_ADMIN_INSTRUMENT_FAMILIES,
        families_shown_count=len(PREVIEW_ADMIN_INSTRUMENT_FAMILIES),
        families_total_count=11,
        current_page=1,
        total_pages=4,
        new_instrument_family_url=url_for("admin_new_instrument_family_preview"),
    )


@app.route("/preview/admin/instrument-families/new", methods=["GET", "POST"])
def admin_new_instrument_family_preview():
    """Renders templates/admin/instrument_families/form.html in create mode."""
    if request.method == "POST":
        return redirect(url_for("admin_instrument_families_preview", feedback="family_created"))

    return render_template(
        "admin/instrument_families/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="create",
        instrument_family=PREVIEW_ADMIN_INSTRUMENT_FAMILY_NEW,
        categories=PREVIEW_ADMIN_INSTRUMENT_FAMILY_CATEGORIES,
        save_url=url_for("admin_new_instrument_family_preview"),
        cancel_url=url_for("admin_instrument_families_preview"),
    )


@app.route("/preview/admin/instrument-families/edit", methods=["GET", "POST"])
def admin_edit_instrument_family_preview():
    """Renders templates/admin/instrument_families/form.html in edit mode (FAM-001)."""
    if request.method == "POST":
        return redirect(url_for("admin_instrument_families_preview", feedback="family_updated"))

    return render_template(
        "admin/instrument_families/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="edit",
        instrument_family=PREVIEW_ADMIN_INSTRUMENT_FAMILY_EDIT,
        categories=PREVIEW_ADMIN_INSTRUMENT_FAMILY_CATEGORIES,
        save_url=url_for("admin_edit_instrument_family_preview"),
        cancel_url=url_for("admin_instrument_families_preview"),
    )


@app.route("/preview/admin/instruments")
def admin_instruments_preview():
    """Renders templates/admin/instruments/list.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/instruments/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        instruments=PREVIEW_ADMIN_INSTRUMENTS,
        instrument_families_filter=PREVIEW_ADMIN_INSTRUMENT_FAMILIES_FILTER,
        instruments_shown_count=len(PREVIEW_ADMIN_INSTRUMENTS),
        instruments_total_count=96,
        current_page=1,
        total_pages=24,
        new_instrument_url=url_for("admin_new_instrument_preview"),
    )


@app.route("/preview/admin/instruments/new", methods=["GET", "POST"])
def admin_new_instrument_preview():
    """Renders templates/admin/instruments/form.html in create mode (INS-005 pre-suggested)."""
    if request.method == "POST":
        return redirect(url_for("admin_instruments_preview", feedback="instrument_created"))

    return render_template(
        "admin/instruments/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="create",
        instrument=PREVIEW_ADMIN_INSTRUMENT_NEW,
        instrument_families=PREVIEW_ADMIN_INSTRUMENT_FAMILIES_FILTER,
        save_url=url_for("admin_new_instrument_preview"),
        cancel_url=url_for("admin_instruments_preview"),
    )


@app.route("/preview/admin/instruments/edit", methods=["GET", "POST"])
def admin_edit_instrument_preview():
    """Renders templates/admin/instruments/form.html in edit mode (INS-001)."""
    if request.method == "POST":
        return redirect(url_for("admin_instruments_preview", feedback="instrument_updated"))

    return render_template(
        "admin/instruments/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="edit",
        instrument=PREVIEW_ADMIN_INSTRUMENT_EDIT,
        instrument_families=PREVIEW_ADMIN_INSTRUMENT_FAMILIES_FILTER,
        save_url=url_for("admin_edit_instrument_preview"),
        cancel_url=url_for("admin_instruments_preview"),
    )


@app.route("/preview/admin/kits")
def admin_kits_preview():
    """Renders templates/admin/kits/list.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/kits/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        kits=PREVIEW_ADMIN_KITS,
        kits_shown_count=len(PREVIEW_ADMIN_KITS),
        kits_total_count=8,
        current_page=1,
        total_pages=1,
        new_kit_url=url_for("admin_new_kit_preview"),
    )


@app.route("/preview/admin/kits/new", methods=["GET", "POST"])
def admin_new_kit_preview():
    """Renders templates/admin/kits/form.html in create mode (empty composition)."""
    if request.method == "POST":
        return redirect(url_for("admin_kits_preview", feedback="kit_created"))

    return render_template(
        "admin/kits/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="create",
        kit=PREVIEW_ADMIN_KIT_NEW,
        kit_composition=PREVIEW_ADMIN_KIT_NEW_COMPOSITION,
        instrument_families=PREVIEW_ADMIN_KIT_INSTRUMENT_FAMILIES,
        save_url=url_for("admin_new_kit_preview"),
        cancel_url=url_for("admin_kits_preview"),
    )


@app.route("/preview/admin/kits/edit", methods=["GET", "POST"])
def admin_edit_kit_preview():
    """Renders templates/admin/kits/form.html in edit mode (Delivery Kit, 5 composition rows)."""
    if request.method == "POST":
        return redirect(url_for("admin_kits_preview", feedback="kit_updated"))

    return render_template(
        "admin/kits/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="edit",
        kit=PREVIEW_ADMIN_KIT_EDIT,
        kit_composition=PREVIEW_ADMIN_KIT_EDIT_COMPOSITION,
        instrument_families=PREVIEW_ADMIN_KIT_INSTRUMENT_FAMILIES,
        save_url=url_for("admin_edit_kit_preview"),
        cancel_url=url_for("admin_kits_preview"),
    )


@app.route("/preview/admin/users")
def admin_users_preview():
    """Renders templates/admin/users/list.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/users/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        users=PREVIEW_ADMIN_USERS,
        users_shown_count=len(PREVIEW_ADMIN_USERS),
        users_total_count=len(PREVIEW_ADMIN_USERS),
        current_page=1,
        total_pages=1,
        new_user_url=url_for("admin_new_user_preview"),
    )


@app.route("/preview/admin/users/new", methods=["GET", "POST"])
def admin_new_user_preview():
    """
    Renders templates/admin/users/form.html in create mode.

    The real template's Password/Confirm Password match check runs client-side
    (static/js/pages/user-form.js); this route does not receive or store either value.
    """
    if request.method == "POST":
        return redirect(url_for("admin_users_preview", feedback="user_created"))

    return render_template(
        "admin/users/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="create",
        user=PREVIEW_ADMIN_USER_NEW,
        institutions=PREVIEW_ADMIN_INSTITUTIONS,
        roles=PREVIEW_ADMIN_ROLE_OPTIONS,
        save_url=url_for("admin_new_user_preview"),
        cancel_url=url_for("admin_users_preview"),
    )


@app.route("/preview/admin/users/edit", methods=["GET", "POST"])
def admin_edit_user_preview():
    """
    Renders templates/admin/users/form.html in edit mode (Alex Morgan), including the Change
    Password and Deactivate User modals.

    ?feedback=password_changed (linked from the Change Password modal's primary action) shows
    a one-off success banner on reload — preview-only, nothing is actually hashed or stored.
    """
    if request.method == "POST":
        return redirect(url_for("admin_users_preview", feedback="user_updated"))

    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/users/form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        form_mode="edit",
        user=PREVIEW_ADMIN_USER_EDIT,
        institutions=PREVIEW_ADMIN_INSTITUTIONS,
        roles=PREVIEW_ADMIN_ROLE_OPTIONS,
        save_url=url_for("admin_edit_user_preview"),
        cancel_url=url_for("admin_users_preview"),
        change_password_url=url_for("admin_edit_user_preview", feedback="password_changed"),
        deactivate_user_url=url_for("admin_users_preview", feedback="user_deactivated"),
    )


@app.route("/preview/admin/roles")
def admin_roles_preview():
    """Renders templates/admin/roles/list.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/roles/list.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        roles=PREVIEW_ADMIN_ROLES,
        roles_shown_count=len(PREVIEW_ADMIN_ROLES),
        roles_total_count=len(PREVIEW_ADMIN_ROLES),
    )


@app.route("/preview/admin/roles/edit", methods=["GET", "POST"])
def admin_edit_role_preview():
    """Renders templates/admin/roles/edit.html (Operator CDE)."""
    if request.method == "POST":
        return redirect(url_for("admin_roles_preview", feedback="role_updated"))

    return render_template(
        "admin/roles/edit.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        role=PREVIEW_ADMIN_ROLE_EDIT,
        save_url=url_for("admin_edit_role_preview"),
        cancel_url=url_for("admin_roles_preview"),
    )


@app.route("/preview/admin/configuration")
def admin_configuration_preview():
    """Renders templates/admin/configuration/index.html with demo context."""
    feedback_banner = PREVIEW_ADMIN_FEEDBACK_BANNERS.get(request.args.get("feedback"))
    return render_template(
        "admin/configuration/index.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        feedback_banner=feedback_banner,
        institution=PREVIEW_ADMIN_INSTITUTION,
        capture_stations=PREVIEW_ADMIN_CAPTURE_STATIONS,
        edit_institution_url=url_for("admin_edit_institution_preview"),
        new_capture_station_url=url_for("admin_new_capture_station_preview"),
    )


@app.route("/preview/admin/configuration/institution/edit", methods=["GET", "POST"])
def admin_edit_institution_preview():
    """Renders templates/admin/configuration/institution_form.html."""
    if request.method == "POST":
        return redirect(url_for("admin_configuration_preview", feedback="institution_updated"))

    return render_template(
        "admin/configuration/institution_form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        institution=PREVIEW_ADMIN_INSTITUTION_EDIT,
        save_url=url_for("admin_edit_institution_preview"),
        cancel_url=url_for("admin_configuration_preview"),
    )


@app.route("/preview/admin/configuration/capture-stations/new", methods=["GET", "POST"])
def admin_new_capture_station_preview():
    """Renders templates/admin/configuration/capture_station_form.html in create mode."""
    if request.method == "POST":
        return redirect(url_for("admin_configuration_preview", feedback="capture_station_created"))

    return render_template(
        "admin/configuration/capture_station_form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="create",
        capture_station=PREVIEW_ADMIN_CAPTURE_STATION_NEW,
        save_url=url_for("admin_new_capture_station_preview"),
        cancel_url=url_for("admin_configuration_preview"),
    )


@app.route("/preview/admin/configuration/capture-stations/edit", methods=["GET", "POST"])
def admin_edit_capture_station_preview():
    """Renders templates/admin/configuration/capture_station_form.html in edit mode (CDE-01)."""
    if request.method == "POST":
        return redirect(url_for("admin_configuration_preview", feedback="capture_station_updated"))

    return render_template(
        "admin/configuration/capture_station_form.html",
        current_locale=PREVIEW_LOCALE,
        current_user=PREVIEW_ADMIN_CURRENT_USER,
        nav_urls=build_admin_nav_urls(),
        form_mode="edit",
        capture_station=PREVIEW_ADMIN_CAPTURE_STATION_EDIT,
        save_url=url_for("admin_edit_capture_station_preview"),
        cancel_url=url_for("admin_configuration_preview"),
    )


if __name__ == "__main__":
    print("=" * 72)
    print("Frontend preview only - not production application")
    print("=" * 72)
    print("Sign In:                    http://127.0.0.1:5001/preview/sign-in")
    print("Operator Dashboard:         http://127.0.0.1:5001/preview/operator/dashboard")
    print("Operator My Profile:        http://127.0.0.1:5001/preview/operator/profile")
    print("Counting Sessions:          http://127.0.0.1:5001/preview/operator/sessions")
    print("New Counting Session:       http://127.0.0.1:5001/preview/operator/sessions/new")
    print("Session History:            http://127.0.0.1:5001/preview/operator/session-history")
    print("Active Counting Session:    http://127.0.0.1:5001/preview/operator/sessions/active")
    print("Count Validation:           http://127.0.0.1:5001/preview/operator/sessions/validation")
    print("Count Validation (discrep): http://127.0.0.1:5001/preview/operator/sessions/validation-discrepancy")
    print("Closed Session Details:     http://127.0.0.1:5001/preview/operator/sessions/closed")
    print("-" * 72)
    print("Supervisor Dashboard:       http://127.0.0.1:5001/preview/supervisor/dashboard")
    print("Supervisor My Profile:      http://127.0.0.1:5001/preview/supervisor/profile")
    print("Supervisor Sessions:        http://127.0.0.1:5001/preview/supervisor/sessions")
    print("Supervisor Session History: http://127.0.0.1:5001/preview/supervisor/session-history")
    print("Supervisor Session Details: http://127.0.0.1:5001/preview/supervisor/sessions/details")
    print("Discrepancies:              http://127.0.0.1:5001/preview/supervisor/discrepancies")
    print("Discrepancies (approved):   http://127.0.0.1:5001/preview/supervisor/discrepancies/post-approval")
    print("Discrepancies (correction): http://127.0.0.1:5001/preview/supervisor/discrepancies/post-correction")
    print("Discrepancy Review:         http://127.0.0.1:5001/preview/supervisor/discrepancies/review")
    print("Reports:                    http://127.0.0.1:5001/preview/supervisor/reports")
    print("Indicators:                 http://127.0.0.1:5001/preview/supervisor/indicators")
    print("Audit Log:                  http://127.0.0.1:5001/preview/supervisor/audit-log")
    print("-" * 72)
    print("Admin Dashboard:             http://127.0.0.1:5001/preview/admin/dashboard")
    print("Admin My Profile:            http://127.0.0.1:5001/preview/admin/profile")
    print("Instrument Families:         http://127.0.0.1:5001/preview/admin/instrument-families")
    print("New Instrument Family:       http://127.0.0.1:5001/preview/admin/instrument-families/new")
    print("Edit Instrument Family:      http://127.0.0.1:5001/preview/admin/instrument-families/edit")
    print("Instruments:                 http://127.0.0.1:5001/preview/admin/instruments")
    print("New Instrument:              http://127.0.0.1:5001/preview/admin/instruments/new")
    print("Edit Instrument:             http://127.0.0.1:5001/preview/admin/instruments/edit")
    print("Kits:                        http://127.0.0.1:5001/preview/admin/kits")
    print("New Kit:                     http://127.0.0.1:5001/preview/admin/kits/new")
    print("Edit Kit:                    http://127.0.0.1:5001/preview/admin/kits/edit")
    print("Users:                       http://127.0.0.1:5001/preview/admin/users")
    print("New User:                    http://127.0.0.1:5001/preview/admin/users/new")
    print("Edit User:                   http://127.0.0.1:5001/preview/admin/users/edit")
    print("Roles:                       http://127.0.0.1:5001/preview/admin/roles")
    print("Edit Role:                   http://127.0.0.1:5001/preview/admin/roles/edit")
    print("Configuration:               http://127.0.0.1:5001/preview/admin/configuration")
    print("Edit Institution:            http://127.0.0.1:5001/preview/admin/configuration/institution/edit")
    print("New Capture Station:         http://127.0.0.1:5001/preview/admin/configuration/capture-stations/new")
    print("Edit Capture Station:        http://127.0.0.1:5001/preview/admin/configuration/capture-stations/edit")
    print("=" * 72)
    app.run(host="127.0.0.1", port=5001, debug=True)
