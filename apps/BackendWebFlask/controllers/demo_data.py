"""
V3 demo fixtures (ui_reference_v3/operator + ui_reference_v3/supervisor).

FIXTURES ONLY: plain Python data plus small pure projection helpers. No database queries,
no Flask request/app objects. Templates must never re-declare these values as literals;
pages read them through the V3 accessors in controllers/view_data.py, which own the
demo-vs-RF boundary. Replacing this module with real backend data later should only
require changing those accessors.

Canonical cases:
- WS-026: the shared Operator/Supervisor end-to-end case (escalated, two review cases).
- WS-027: the Supervisor New Session wizard demo — a different, newly scheduled session.
- WS-021..WS-028 supporting rows for list screens.

The Operator `scenario` (escalated | clean) is a presentation-only projection of WS-026.
It is not a lifecycle state and must not leak into services/rf_session.py.
"""

from copy import deepcopy


DEMO_DATE = '2026-10-02'
DEMO_DATE_DISPLAY = 'Oct 2, 2026'
WS026_ID = 'WS-026'
WS027_ID = 'WS-027'

SCENARIO_ESCALATED = 'escalated'
SCENARIO_CLEAN = 'clean'
SCENARIOS = (SCENARIO_ESCALATED, SCENARIO_CLEAN)
DEFAULT_SCENARIO = SCENARIO_ESCALATED

# Supervisor Review demo outcomes (Final Validation "ready" vs "blocking").
REVIEW_OUTCOME_RESOLVED = 'resolved'
REVIEW_OUTCOME_UNRESOLVED = 'unresolved'
REVIEW_OUTCOMES = (REVIEW_OUTCOME_RESOLVED, REVIEW_OUTCOME_UNRESOLVED)


# ----------------------------------------------------------------------------------------
# Presentation vocabulary (V3). Kept separate from view_data.SESSION_STATUS_UI, which maps
# RF lifecycle codes and must not change.
# ----------------------------------------------------------------------------------------

V3_SESSION_STATUS = {
    'assigned': ('Assigned', 'success'),
    'ready_to_start': ('Ready to Start', 'info'),
    'in_progress': ('In Progress', 'info'),
    'verifying': ('Verifying', 'info'),
    'escalation_prepared': ('Escalation Prepared', 'warning'),
    'escalated': ('Escalated', 'warning'),
    'ready_to_close': ('Ready to Close', 'success'),
    'closed': ('Closed', 'neutral'),
}

V3_REVIEW_STATUS = {
    'no_review_needed': ('No Review Needed', 'neutral'),
    'review_required': ('Review Required', 'warning'),
    'reviewed_unresolved': ('Reviewed – Unresolved', 'info'),
    'resolved': ('Resolved', 'success'),
}

V3_SEVERITY = {
    'critical': ('Critical', 'danger'),
    'info': ('Info', 'warning'),
}


def session_status(code):
    label, variant = V3_SESSION_STATUS[code]
    return {'code': code, 'label': label, 'variant': variant}


def review_status(code):
    label, variant = V3_REVIEW_STATUS[code]
    return {'code': code, 'label': label, 'variant': variant}


def severity(code):
    label, variant = V3_SEVERITY[code]
    return {'code': code, 'label': label, 'variant': variant}


# ----------------------------------------------------------------------------------------
# People. PRESENTATION_USERS is an optional display identity for V3 demo pages only — it
# never replaces the authenticated current_user or the auth seeds.
# ----------------------------------------------------------------------------------------

PRESENTATION_USERS = {
    'operator': {'name': 'Alex Morgan', 'role_label': 'Operator CDE', 'avatar_url': None},
    'supervisor': {'name': 'Sophia Turner', 'role_label': 'Supervisor CDE / Quality', 'avatar_url': None},
}

SURGICAL_TEAM = [
    {'name': 'Dr. James Wilson', 'role': 'Lead Surgeon'},
    {'name': 'Dr. Olivia Chen', 'role': 'Assistant Surgeon'},
    {'name': 'Dr. Priya Nair', 'role': 'Anesthesiologist'},
    {'name': 'Jordan Brooks', 'role': 'Scrub Nurse'},
    {'name': 'Casey Reed', 'role': 'Circulating Nurse'},
]

SURGICAL_ROLES = ['Lead Surgeon', 'Assistant Surgeon', 'Anesthesiologist', 'Scrub Nurse', 'Circulating Nurse',
                  'Other clinical personnel']


# ----------------------------------------------------------------------------------------
# Delivery Kit — canonical V3 inventory (total 11).
# ----------------------------------------------------------------------------------------

DELIVERY_KIT = {
    'name': 'Delivery Kit',
    'items': [
        {'key': 'kelly_clamp', 'instrument_name': 'Kelly Clamp', 'expected_quantity': 4},
        {'key': 'foerster_clamp', 'instrument_name': 'Foerster Clamp', 'expected_quantity': 2},
        {'key': 'mayo_hegar', 'instrument_name': 'Mayo-Hegar Needle Holder', 'expected_quantity': 2},
        {'key': 'mayo_scissors', 'instrument_name': 'Mayo Scissors', 'expected_quantity': 1},
        {'key': 'suture_needle_set', 'instrument_name': 'Suture Needle Set', 'expected_quantity': 2},
    ],
    'total_expected': 11,
}


# ----------------------------------------------------------------------------------------
# WS-026 session header.
# ----------------------------------------------------------------------------------------

WS026_SESSION = {
    'session_id': WS026_ID,
    'procedure_name': 'Appendectomy',
    'operating_room': 'OR-3',
    'procedure_label': 'Appendectomy - OR-3',
    'patient_name': 'Maria García',
    'patient_record': 'PT-10482',
    'kit_name': 'Delivery Kit',
    'capture_station': 'CDE-01',
    'operator_name': 'Alex Morgan',
    'supervisor_name': 'Sophia Turner',
    'assigned_by': 'Sophia Turner',
    'scheduled_at': f'{DEMO_DATE} 15:15',
    'scheduled_at_display': 'October 2, 2026 15:15',
    'scheduled_time': '15:15',
    'started_time': '15:20',
    'closed_time': '15:42',
    'escalated_time': '15:41',
    'escalated_at_display': 'Oct 2, 2026 • 15:41',
    'surgical_team_summary': '5 clinical members',
    # Status as seen by the Supervisor at review time (operational session already closed).
    'session_status': 'closed',
    'review_status': 'review_required',
}


# ----------------------------------------------------------------------------------------
# WS-026 timeline (single source for Session Activity, Traceability and Audit Log).
# ----------------------------------------------------------------------------------------

WS026_TIMELINE = [
    {'time': '15:15', 'datetime': f'{DEMO_DATE} 15:15:00', 'code': 'SESSION_CREATED',
     'label': 'Session Created', 'actor': 'Sophia Turner', 'actor_role': 'Supervisor',
     'target': None, 'entity': 'WorkSession', 'detail': 'New session created for OR-3'},
    {'time': '15:16', 'datetime': f'{DEMO_DATE} 15:16:00', 'code': 'OPERATOR_ASSIGNED',
     'label': 'Operator Assigned', 'actor': 'Sophia Turner', 'actor_role': 'Supervisor',
     'target': 'Alex Morgan', 'entity': 'WorkSession', 'detail': 'Alex Morgan assigned as Operator'},
    {'time': '15:20', 'datetime': f'{DEMO_DATE} 15:20:00', 'code': 'SESSION_STARTED',
     'label': 'Session Started', 'actor': 'Alex Morgan', 'actor_role': 'Operator',
     'target': None, 'entity': 'WorkSession', 'detail': 'Session started in OR-3'},
    {'time': '15:39:30', 'datetime': f'{DEMO_DATE} 15:39:30', 'code': 'FINAL_TRAY_VERIFICATION_REQUESTED',
     'label': 'Final Tray Verification Requested', 'actor': None, 'actor_role': 'System',
     'target': None, 'entity': 'WorkSession', 'detail': 'Final tray verification requested'},
    {'time': '15:39:45', 'datetime': f'{DEMO_DATE} 15:39:45', 'code': 'FINAL_CAPTURE_CREATED', 'traceability_label': 'Final Capture Created',
     'label': 'Final Capture Created', 'actor': None, 'actor_role': 'System',
     'target': None, 'entity': 'Evidence', 'detail': 'Original capture uploaded from CDE-01'},
    {'time': '15:40:00', 'datetime': f'{DEMO_DATE} 15:40:00', 'code': 'AI_ANALYSIS_COMPLETED', 'traceability_label': 'YOLO Detection Overlay Generated / AI Analysis Completed',
     'label': 'AI Analysis Completed', 'actor': None, 'actor_role': 'System',
     'target': None, 'entity': 'Evidence', 'detail': 'YOLO detection overlay generated'},
    {'time': '15:40:30', 'datetime': f'{DEMO_DATE} 15:40:30', 'code': 'HUMAN_VALIDATION_COMPLETED', 'traceability_label': 'Operator Human Validation Completed',
     'label': 'Human Validation Completed', 'actor': 'Alex Morgan', 'actor_role': 'Operator',
     'target': None, 'entity': 'WorkSession', 'detail': 'Operator validation completed'},
    {'time': '15:41', 'datetime': f'{DEMO_DATE} 15:41:00', 'code': 'DISCREPANCIES_ESCALATED', 'traceability_label': '2 Review Cases Escalated',
     'label': 'Review Cases Escalated', 'actor': 'Alex Morgan', 'actor_role': 'Operator',
     'target': 'Sophia Turner', 'entity': 'Review Cases', 'detail': '2 review cases escalated to Supervisor'},
    {'time': '15:42', 'datetime': f'{DEMO_DATE} 15:42:00', 'code': 'SESSION_CLOSED', 'traceability_label': 'Operational Session Closed / Evidence Locked',
     'label': 'Session Closed', 'actor': 'Alex Morgan', 'actor_role': 'Operator',
     'target': None, 'entity': 'WorkSession', 'detail': 'Session closed and submitted for review'},
    {'time': '15:45', 'datetime': f'{DEMO_DATE} 15:45:00', 'code': 'SUPERVISOR_REVIEW_STARTED', 'traceability_label': 'Supervisor Review Started',
     'label': 'Supervisor Review Started', 'actor': 'Sophia Turner', 'actor_role': 'Supervisor',
     'target': None, 'entity': 'Review Cases', 'detail': 'Supervisor review of 2 review cases started'},
]

# Events shown in Review Detail -> Validation & Correction Traceability (in timeline order).
TRACEABILITY_CODES = ('FINAL_CAPTURE_CREATED', 'AI_ANALYSIS_COMPLETED', 'HUMAN_VALIDATION_COMPLETED',
                      'DISCREPANCIES_ESCALATED', 'SESSION_CLOSED', 'SUPERVISOR_REVIEW_STARTED')

# Timeline codes that do not exist in the escalated path when the clean scenario is shown.
CLEAN_SCENARIO_EXCLUDED_EVENTS = {'DISCREPANCIES_ESCALATED', 'SUPERVISOR_REVIEW_STARTED'}


# ----------------------------------------------------------------------------------------
# AI analysis + final-tray verification metadata.
# ----------------------------------------------------------------------------------------

AI_ANALYSIS = {
    'vision_model': 'Surgical Instrument Tracker v4',
    'model_version': '4.2.1',
    'confidence_threshold': '0.92',
    'final_capture_at': f'{DEMO_DATE} 15:39:45',
    'analysis_at': f'{DEMO_DATE} 15:40:00',
    'human_validation_at': f'{DEMO_DATE} 15:40:30',
}

TRAY_VERIFICATION = {
    'requested_at': '15:39:30',
    'final_capture_target': '15:39:45',
    'verified_at': f'{DEMO_DATE} 15:39:45',
    'station': 'CDE-01',
    'expected_instruments': 11,
    # Failed attempt: the instrument not yet confirmed on the final tray.
    'failed_instrument_key': 'mayo_hegar',
    # Demo-only progress shown while 'verifying' (presentation, not a measured value).
    'verifying_progress_percent': 45,
}

# Latest live (non-final) tray capture shown on Active Session Capture. This is NOT the
# final evidence capture (FINAL_CAPTURE_CREATED, 15:39:45).
LATEST_LIVE_CAPTURE = {'captured_at': f'{DEMO_DATE} 15:38:50', 'station': 'CDE-01'}

# Instrument Event Log (Active Session Capture), chronological, inside the WS-026 procedure
# window (Session Started 15:20 -> Final Tray Verification Requested 15:39:30). Mayo-Hegar is
# removed and never returned, which is why the first final tray verification fails.
WS026_INSTRUMENT_EVENTS = [
    {'time': '15:20:30', 'event': 'Detected on Tray', 'instrument': 'Suture Needle Set', 'detail': 'Detected by YOLO'},
    {'time': '15:21:08', 'event': 'Detected on Tray', 'instrument': 'Kelly Clamp', 'detail': 'Detected by YOLO'},
    {'time': '15:23:14', 'event': 'Removed from Tray', 'instrument': 'Kelly Clamp', 'detail': 'Instrument in use'},
    {'time': '15:24:02', 'event': 'Removed from Tray', 'instrument': 'Mayo-Hegar Needle Holder', 'detail': 'Instrument in use'},
    {'time': '15:26:40', 'event': 'In Use', 'instrument': 'Mayo Scissors', 'detail': 'Outside tray area'},
    {'time': '15:31:55', 'event': 'Returned to Tray', 'instrument': 'Kelly Clamp', 'detail': 'Returned to tray area'},
    {'time': '15:34:10', 'event': 'Returned to Tray', 'instrument': 'Mayo Scissors', 'detail': 'Returned to tray area'},
    {'time': '15:36:22', 'event': 'Detected Again', 'instrument': 'Kelly Clamp', 'detail': 'Re-detected by YOLO'},
    {'time': '15:38:50', 'event': 'Detected Again', 'instrument': 'Foerster Clamp', 'detail': 'Latest live capture'},
]


# ----------------------------------------------------------------------------------------
# Shared final evidence (Operator AI Detection / Human Validation / Escalation, Supervisor
# Counts & Evidence / Evidence & Traceability). The asset may not exist yet; view_data
# resolves image_url / image_available at request time. Overlay coordinates are percentages
# of the image frame and must be re-measured once the real asset is supplied.
# ----------------------------------------------------------------------------------------

EVIDENCE_IMAGE_BASENAME = 'img/demo/ws-026-final-tray'
EVIDENCE_IMAGE_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp')

WS026_EVIDENCE = {
    'image_basename': EVIDENCE_IMAGE_BASENAME,
    'alt': 'Final tray capture for session WS-026 at capture station CDE-01',
    'capture_timestamp': f'{DEMO_DATE} 15:39:45',
    'capture_timestamp_display': 'Oct 2, 2026 • 15:39:45',
    'station': 'CDE-01',
    'overlay_boxes': [
        {'label': 'Kelly Clamp', 'confidence': 98.0, 'confidence_label': '98%',
         'x': 3.7, 'y': 10.0, 'width': 21.0, 'height': 40.0},
        {'label': 'Foerster Clamp', 'confidence': 96.0, 'confidence_label': '96%',
         'x': 28.7, 'y': 10.0, 'width': 21.0, 'height': 40.0},
        {'label': 'Mayo-Hegar Needle Holder', 'confidence': 94.0, 'confidence_label': '94%',
         'x': 53.0, 'y': 10.0, 'width': 21.5, 'height': 40.0},
        {'label': 'Mayo Scissors', 'confidence': 92.0, 'confidence_label': '92%',
         'x': 3.7, 'y': 58.0, 'width': 21.0, 'height': 31.0},
    ],
}


# ----------------------------------------------------------------------------------------
# WS-026 per-instrument counts. Confidence values are display-ready; templates must not
# recompute them.
# ----------------------------------------------------------------------------------------

WS026_COUNTS_ESCALATED = {
    'kelly_clamp': {
        'ai_detected': 4, 'validated': 4,
        'avg_confidence': 96.5, 'avg_confidence_label': '96.5%',
        'instance_confidences': ['98%', '96%', '94%', '98%'],
        'ai_result': 'matched', 'validation_status': 'matched',
        'correction_reason': 'None', 'correction_notes': 'Verified visually',
    },
    'foerster_clamp': {
        'ai_detected': 2, 'validated': 2,
        'avg_confidence': 96.0, 'avg_confidence_label': '96.0%',
        'instance_confidences': [],
        'ai_result': 'matched', 'validation_status': 'matched',
        'correction_reason': 'None', 'correction_notes': 'Verified visually',
    },
    'mayo_hegar': {
        'ai_detected': 1, 'validated': 1,
        'avg_confidence': 94.0, 'avg_confidence_label': '94.0%',
        'instance_confidences': ['94%'],
        'ai_result': 'missing', 'validation_status': 'unresolved',
        'correction_reason': 'Missing Instrument', 'correction_notes': 'Second instrument not detected',
    },
    'mayo_scissors': {
        'ai_detected': 1, 'validated': 1,
        'avg_confidence': 92.0, 'avg_confidence_label': '92.0%',
        'instance_confidences': [],
        'ai_result': 'matched', 'validation_status': 'matched',
        'correction_reason': 'None', 'correction_notes': 'Verified visually',
    },
    'suture_needle_set': {
        'ai_detected': 2, 'validated': 2,
        'avg_confidence': 97.5, 'avg_confidence_label': '97.5%',
        'instance_confidences': [],
        'ai_result': 'matched', 'validation_status': 'evidence_verification_required',
        'correction_reason': 'Detection evidence requires verification',
        'correction_notes': 'Verify evidence before finalizing',
    },
}

# Clean projection overrides (Operator scenario=clean only). Everything not listed here is
# taken from the escalated counts.
WS026_COUNTS_CLEAN_OVERRIDES = {
    'mayo_hegar': {
        'ai_detected': 2, 'validated': 2,
        'instance_confidences': ['95%', '93%'],
        'ai_result': 'matched', 'validation_status': 'matched',
        'correction_reason': 'None', 'correction_notes': 'Verified visually',
    },
    'suture_needle_set': {
        'validation_status': 'matched',
        'correction_reason': 'None', 'correction_notes': 'Verified visually',
    },
}

AI_RESULT_UI = {
    'matched': ('Matched', 'success'),
    'missing': ('Missing', 'warning'),
}

VALIDATION_STATUS_UI = {
    'matched': ('Matched', 'success'),
    'unresolved': ('Unresolved', 'danger'),
    'evidence_verification_required': ('Evidence Verification Required', 'warning'),
}


# ----------------------------------------------------------------------------------------
# WS-026 review cases. Base state: both Review Required. Demo outcomes:
#   resolved   -> both Resolved (Final Validation ready)
#   unresolved -> Mayo-Hegar Resolved, Suture Needle Set Reviewed – Unresolved (blocking)
# ----------------------------------------------------------------------------------------

WS026_REVIEW_CASES = [
    {
        'case_key': 'mayo-hegar',
        'instrument_key': 'mayo_hegar',
        'instrument_name': 'Mayo-Hegar Needle Holder',
        'kit_label': 'Delivery Kit CDE-01',
        'expected_quantity': 2, 'ai_detected': 1, 'validated': 1, 'difference': -1,
        'type_label': 'Missing Instrument', 'type_short': 'Missing',
        'severity': 'critical',
        'escalated_time': '15:41',
        'review_status': 'review_required',
        'ai_evidence': {'detected_count': 1, 'avg_confidence_label': '94.0%', 'timestamp': '15:40:00'},
        'operator_validation': {
            'validated_count': 1, 'operator_name': 'Alex Morgan',
            'reason': 'instrument not detected by camera', 'timestamp': '15:40:30',
            'notes': 'Physical recount confirmed only one Mayo-Hegar Needle Holder present.',
        },
        'summary_title': 'Missing instrument detected',
        'summary_description': ('One Mayo-Hegar Needle Holder was missing post-procedure. Final '
                                'validated count of 1 does not match expected 2.'),
        'resolution': {
            'resolution_type': 'Confirmed Missing Instrument',
            'notes': ('Physical recount confirmed one missing Mayo-Hegar Needle Holder. Review '
                      'completed using captured evidence and Operator validation.'),
            'resolved_by': 'Sophia Turner',
        },
        'unresolved_resolution': None,
    },
    {
        'case_key': 'suture-needle-set',
        'instrument_key': 'suture_needle_set',
        'instrument_name': 'Suture Needle Set',
        'kit_label': 'Delivery Kit CDE-01',
        'expected_quantity': 2, 'ai_detected': 2, 'validated': 2, 'difference': 0,
        'type_label': 'Evidence Verification', 'type_short': 'Evidence Verification',
        'severity': 'info',
        'escalated_time': '15:41',
        'review_status': 'review_required',
        'ai_evidence': {'detected_count': 2, 'avg_confidence_label': '97.5%', 'timestamp': '15:40:00'},
        'operator_validation': {
            'validated_count': 2, 'operator_name': 'Alex Morgan',
            'reason': 'detection evidence requires verification', 'timestamp': '15:40:30',
            'notes': 'Verify evidence before finalizing.',
        },
        'summary_title': 'Evidence verification required',
        'summary_description': ('Suture Needle Set counts match, but the detection evidence '
                                'requires Supervisor verification.'),
        'resolution': {
            'resolution_type': 'Evidence Verified / Count Confirmed',
            'notes': 'Captured evidence reviewed; count of 2 confirmed.',
            'resolved_by': 'Sophia Turner',
        },
        'unresolved_resolution': {
            'resolution_type': 'No valid resolution',
            'notes': 'Available evidence is insufficient to approve the current resolution.',
            'reviewed_by': 'Sophia Turner',
        },
    },
]

# Case that remains open in the Reviewed – Unresolved demonstration.
UNRESOLVED_DEMO_CASE_KEY = 'suture-needle-set'

WS026_VALIDATION_HISTORY_EVENT = {
    'event': 'Operator validation completed',
    'description': 'Human validation completed for the final WS-026 evidence.',
    'actor': 'Alex Morgan', 'actor_role': 'Operator', 'timestamp': '15:40:30',
}

FINAL_VALIDATION = {
    'reviewed_by': 'Sophia Turner',
    'review_timestamp': f'{DEMO_DATE} 15:48:00',
}


# ----------------------------------------------------------------------------------------
# WS-027 — Supervisor New Session wizard demo. A different session from WS-026; do not reuse
# the WS-026 timeline. Field values follow the approved wizard references.
# ----------------------------------------------------------------------------------------

WS027_NEW_SESSION = {
    'session_id': WS027_ID,
    'procedure_name': 'Appendectomy',
    'operating_room': 'OR-3',
    'scheduled_at': '2026-10-03 08:30',
    'scheduled_at_display': '2026-10-03 08:30:00',
    'kit_name': 'Delivery Kit',
    'capture_station': 'CDE-01',
    'capture_station_option_label': 'Station CDE-01',
    'operator_name': 'Alex Morgan',
    'patient_name': 'Maria García',
    'patient_record': 'PT-10482',
    'surgical_team': SURGICAL_TEAM,
    'surgical_team_summary': '5 clinical members',
    'inventory_note': 'Expected inventory will be frozen when the session starts.',
    'success_message': 'Session WS-027 created and assigned successfully.',
    'session_status': 'assigned',
    'review_status': 'no_review_needed',
    'options': {
        'procedures': ['Appendectomy', 'Cholecystectomy', 'Hernia Repair',
                       'Laparoscopic Cholecystectomy', 'Knee Arthroplasty'],
        'operating_rooms': ['OR-1', 'OR-2', 'OR-3', 'OR-4'],
        'kits': ['Delivery Kit', 'Suture Kit', 'General Surgery Kit', 'Ortho Kit'],
        'capture_stations': ['CDE-01', 'CDE-02', 'CDE-03', 'CDE-04'],
        'operators': ['Alex Morgan', 'Emily Carter', 'Jordan Brooks', 'Daniel Lee', 'Olivia Chen'],
        'surgical_roles': SURGICAL_ROLES,
    },
}


# ----------------------------------------------------------------------------------------
# Supporting session rows (Supervisor Sessions / History / Dashboard, Operator Assigned).
# One registry keyed by display id; each role screen projects from it.
# ----------------------------------------------------------------------------------------

SESSION_ROWS = {
    'WS-021': {'session_id': 'WS-021', 'procedure_name': 'Cholecystectomy', 'operating_room': 'OR-2',
               'kit_name': 'Suture Kit', 'operator_name': 'Olivia Chen', 'capture_station': 'CDE-02',
               'patient_name': None, 'scheduled_time': '07:00', 'started_time': '07:15', 'closed_time': '07:35',
               'session_status': 'closed', 'review_status': 'no_review_needed'},
    'WS-022': {'session_id': 'WS-022', 'procedure_name': 'Knee Arthroplasty', 'operating_room': 'OR-4',
               'kit_name': 'Ortho Kit', 'operator_name': 'Daniel Lee', 'capture_station': 'CDE-04',
               'patient_name': None, 'scheduled_time': '07:45', 'started_time': '08:00', 'closed_time': '08:30',
               'session_status': 'closed', 'review_status': 'resolved'},
    'WS-023': {'session_id': 'WS-023', 'procedure_name': 'Laparoscopic Cholecystectomy', 'operating_room': 'OR-4',
               'kit_name': 'Delivery Kit', 'operator_name': 'Jordan Brooks', 'capture_station': 'CDE-03',
               'patient_name': None, 'scheduled_time': '11:00', 'started_time': '11:15', 'closed_time': None,
               'session_status': 'in_progress', 'review_status': 'no_review_needed'},
    'WS-024': {'session_id': 'WS-024', 'procedure_name': 'Hernia Repair', 'operating_room': 'OR-2',
               'kit_name': 'General Surgery Kit', 'operator_name': 'Priya Nair', 'capture_station': 'CDE-01',
               'patient_name': None, 'scheduled_time': '17:00', 'started_time': None, 'closed_time': None,
               'session_status': 'assigned', 'review_status': 'no_review_needed'},
    'WS-025': {'session_id': 'WS-025', 'procedure_name': 'Cholecystectomy', 'operating_room': 'OR-1',
               'kit_name': 'Suture Kit', 'operator_name': 'Emily Carter', 'capture_station': 'CDE-02',
               'patient_name': None, 'scheduled_time': '14:00', 'started_time': '14:10', 'closed_time': '14:30',
               'session_status': 'closed', 'review_status': 'reviewed_unresolved',
               'issue_label': 'Counting error', 'issue_detail': '1 review case'},
    'WS-026': {'session_id': WS026_ID, 'procedure_name': 'Appendectomy', 'operating_room': 'OR-3',
               'kit_name': 'Delivery Kit', 'operator_name': 'Alex Morgan', 'capture_station': 'CDE-01',
               'patient_name': 'Maria García', 'scheduled_time': '15:15', 'started_time': '15:20',
               'closed_time': '15:42', 'session_status': 'closed', 'review_status': 'review_required',
               'issue_label': 'Missing Instrument', 'issue_detail': '2 review cases'},
    'WS-027': {'session_id': WS027_ID, 'procedure_name': 'Appendectomy', 'operating_room': 'OR-3',
               'kit_name': 'Delivery Kit', 'operator_name': 'Alex Morgan', 'capture_station': 'CDE-01',
               'patient_name': 'Maria García', 'scheduled_time': '2026-10-03 08:30', 'started_time': None,
               'closed_time': None, 'session_status': 'assigned', 'review_status': 'no_review_needed'},
    'WS-028': {'session_id': 'WS-028', 'procedure_name': 'Hernia Repair', 'operating_room': 'OR-2',
               'kit_name': 'General Surgery Kit', 'operator_name': 'Alex Morgan', 'capture_station': 'CDE-01',
               'patient_name': 'Priya Nair', 'scheduled_time': '14:30', 'started_time': '14:30',
               'closed_time': None, 'session_status': 'in_progress', 'review_status': 'no_review_needed'},
}

# Display order per screen (newest first where the references show it that way).
SUPERVISOR_SESSIONS_ORDER = ['WS-026', 'WS-025', 'WS-024', 'WS-023', 'WS-022', 'WS-021']
SUPERVISOR_HISTORY_ORDER = ['WS-026', 'WS-025', 'WS-022', 'WS-021']
# The Operator Assigned Sessions list shows WS-026 before it starts (Assigned), as in the
# approved Operator reference. The status override is a per-screen projection only.
OPERATOR_ASSIGNED_ROWS = [
    {'session_id': 'WS-026', 'session_status': 'assigned'},
    {'session_id': 'WS-027', 'session_status': 'ready_to_start'},
    {'session_id': 'WS-028', 'session_status': 'in_progress'},
]

# Supervisor Review Queue rows not belonging to WS-026 (WS-026 rows come from its cases).
OTHER_REVIEW_QUEUE_ROWS = [
    {'session_id': 'WS-025', 'instrument_name': 'Suture Needle Set', 'kit_label': 'Suture Kit CDE-02',
     'expected_quantity': 6, 'ai_detected': 5, 'validated': 5, 'difference': -1,
     'type_short': 'Missing', 'review_status': 'reviewed_unresolved'},
    {'session_id': 'WS-022', 'instrument_name': 'Retractor Blade', 'kit_label': 'Ortho Kit CDE-04',
     'expected_quantity': 3, 'ai_detected': 2, 'validated': 2, 'difference': -1,
     'type_short': 'Missing', 'review_status': 'resolved'},
]

# Operator Assigned Sessions banners (demo only; the list itself never changes state).
OPERATOR_BANNERS = {
    'closed_clean': {'variant': 'success', 'title': 'Session {session} closed.',
                     'description': 'Session Status: Closed · Review Status: No Review Needed.'},
    'closed_escalated': {'variant': 'success', 'title': 'Session {session} closed.',
                         'description': ('Session Status: Closed · Review Status: Review Required. Supervisor '
                                         'review continues independently.')},
    'walkthrough': {'variant': 'info', 'title': 'Demo walkthrough is available for WS-026.',
                    'description': 'Session {session} is shown for assignment only in this demo.'},
}

SUPERVISOR_DASHBOARD = {
    'kpis': {'sessions_today': 12, 'in_progress_sessions': 3, 'reviews_required': 2,
             'unresolved_discrepancies': 3},
    'sessions_by_day': [
        {'label': 'Mon', 'count': 8}, {'label': 'Tue', 'count': 12}, {'label': 'Wed', 'count': 10},
        {'label': 'Thu', 'count': 15}, {'label': 'Fri', 'count': 11},
    ],
    'review_status_counts': [
        {'code': 'review_required', 'value': 2},
        {'code': 'reviewed_unresolved', 'value': 1},
        {'code': 'resolved', 'value': 4},
    ],
    'review_queue_summary': {'review_required': 2, 'reviewed_unresolved': 1, 'resolved_today': 1},
}


# Applied when a case without a case-specific unresolved text is marked Reviewed – Unresolved.
GENERIC_UNRESOLVED_RESOLUTION = {
    'resolution_type': 'No valid resolution',
    'notes': 'Available evidence is insufficient to approve the current resolution.',
    'reviewed_by': 'Sophia Turner',
}

RESOLUTION_VALIDATED_BANNER = {
    'variant': 'success',
    'title': 'Resolution validated for WS-026.',
    'description': 'Demo confirmation only: review state is not persisted.',
}

RESOLUTION_TYPES = [
    'Confirmed Missing Instrument',
    'Evidence Verified / Count Confirmed',
    'Instrument Located',
    'Counting Error Corrected',
    'Other',
]


# ----------------------------------------------------------------------------------------
# Supervisor list metadata (pagination totals shown by the references) and filter options.
# Filters are presentation-only, like the existing V2 list filters.
# ----------------------------------------------------------------------------------------

SUPERVISOR_LIST_TOTALS = {
    'sessions': {'total': 24, 'pages': 4},
    'history': {'total': 123, 'pages': 31},
    'audit_log': {'total': 342, 'pages': 35},
}

SUPERVISOR_FILTER_OPTIONS = {
    'session_statuses': ['assigned', 'in_progress', 'closed'],
    'review_statuses': ['no_review_needed', 'review_required', 'reviewed_unresolved', 'resolved'],
    'kits': ['Delivery Kit', 'Suture Kit', 'General Surgery Kit', 'Ortho Kit'],
    'operators': ['Alex Morgan', 'Emily Carter', 'Jordan Brooks', 'Daniel Lee', 'Olivia Chen', 'Priya Nair'],
    'dates': ['Today', 'Last 7 Days', 'Last 30 Days'],
    'audit_users': ['Sophia Turner', 'Alex Morgan', 'System'],
    'audit_sessions': ['WS-026', 'WS-025', 'WS-022'],
}


# ----------------------------------------------------------------------------------------
# Supervisor Reports and Indicators (aggregate presentation data; no backing analytics yet).
# ----------------------------------------------------------------------------------------

SUPERVISOR_REPORTS = {
    'filters': {
        'date_ranges': ['Last 30 Days', 'Last 7 Days', 'Today'],
        'session_statuses': ['All Statuses', 'Assigned', 'In Progress', 'Closed'],
    },
    'cards': [
        {'icon': 'document', 'title': 'Session Summary',
         'description': 'Overview of completed counting sessions.', 'last_generated': '10 mins ago'},
        {'icon': 'alert_triangle', 'title': 'Discrepancy Summary',
         'description': 'Summary of unresolved and resolved discrepancies.', 'last_generated': '1 hour ago'},
        {'icon': 'box', 'title': 'Kit Activity',
         'description': 'Review counting activity grouped by kit.', 'last_generated': '3 hours ago'},
        {'icon': 'user', 'title': 'Operator Activity',
         'description': 'Review counting activity and discrepancy patterns by operator.',
         'last_generated': 'Yesterday'},
    ],
}

SUPERVISOR_INDICATORS = {
    'period': 'October 2026',
    'periods': ['October 2026', 'September 2026', 'August 2026'],
    'kpis': {
        'total_sessions': 156,
        'total_discrepancies': 8,
        'ai_human_agreement': '94.2%',
        'ai_human_agreement_target': '≥ 95%',
        'reviewed_unresolved': 1,
        'average_resolution_time': '18 min',
    },
    'sessions_by_day': SUPERVISOR_DASHBOARD['sessions_by_day'],
    'discrepancies_by_instrument': [
        {'label': 'Mayo-Hegar Needle Holder', 'value': 3, 'variant': 'danger'},
        {'label': 'Allis Tissue Forceps', 'value': 2, 'variant': 'warning'},
        {'label': 'Foerster Sponge Forceps', 'value': 1, 'variant': 'neutral'},
        {'label': 'Other', 'value': 2, 'variant': 'primary'},
    ],
    'discrepancies_by_family': [
        {'label': 'Needle Holders', 'value': 4, 'variant': 'danger'},
        {'label': 'Forceps', 'value': 3, 'variant': 'warning'},
        {'label': 'Scissors', 'value': 1, 'variant': 'neutral'},
    ],
    'discrepancies_by_type': [
        {'label': 'Missing', 'value': 3, 'variant': 'danger'},
        {'label': 'Extra', 'value': 2, 'variant': 'warning'},
        {'label': 'Low Confidence', 'value': 1, 'variant': 'neutral'},
        {'label': 'Unrecognized', 'value': 1, 'variant': 'primary'},
        {'label': 'Uncertain Classification', 'value': 1, 'variant': 'info'},
    ],
}


# ----------------------------------------------------------------------------------------
# Pure helpers (no I/O). Always return copies so callers cannot mutate the fixtures.
# ----------------------------------------------------------------------------------------

DEMO_SESSION_IDS = frozenset(SESSION_ROWS)
DETAIL_SESSION_IDS = frozenset({WS026_ID, WS027_ID})


def normalize_session_id(session_id):
    return str(session_id or '').strip().upper()


def is_demo_session_id(session_id):
    """True for any V3 demo display id (WS-021..WS-028)."""
    return normalize_session_id(session_id) in DEMO_SESSION_IDS


def normalize_scenario(value):
    value = (value or '').strip().lower()
    return value if value in SCENARIOS else DEFAULT_SCENARIO


def normalize_review_outcome(value):
    value = (value or '').strip().lower()
    return value if value in REVIEW_OUTCOMES else None


def ws026_count_rows(scenario=DEFAULT_SCENARIO):
    """Per-instrument count rows for WS-026 under the given presentation scenario."""
    scenario = normalize_scenario(scenario)
    rows = []
    for item in DELIVERY_KIT['items']:
        counts = deepcopy(WS026_COUNTS_ESCALATED[item['key']])
        if scenario == SCENARIO_CLEAN:
            counts.update(deepcopy(WS026_COUNTS_CLEAN_OVERRIDES.get(item['key'], {})))
        ai_label, ai_variant = AI_RESULT_UI[counts['ai_result']]
        status_label, status_variant = VALIDATION_STATUS_UI[counts['validation_status']]
        expected = item['expected_quantity']
        rows.append({
            'key': item['key'],
            'instrument_name': item['instrument_name'],
            'expected_quantity': expected,
            'ai_detected': counts['ai_detected'],
            'validated': counts['validated'],
            'ai_difference': counts['ai_detected'] - expected,
            'difference': counts['validated'] - expected,
            'avg_confidence': counts['avg_confidence'],
            'avg_confidence_label': counts['avg_confidence_label'],
            'instance_confidences': counts['instance_confidences'],
            'ai_result': {'code': counts['ai_result'], 'label': ai_label, 'variant': ai_variant},
            'validation_status': {'code': counts['validation_status'], 'label': status_label,
                                  'variant': status_variant},
            'needs_review': counts['validation_status'] != 'matched',
            'correction_reason': counts['correction_reason'],
            'correction_notes': counts['correction_notes'],
        })
    return rows


def count_totals(rows):
    expected = sum(row['expected_quantity'] for row in rows)
    ai_detected = sum(row['ai_detected'] for row in rows)
    validated = sum(row['validated'] for row in rows)
    return {
        'expected': expected,
        'ai_detected': ai_detected,
        'validated': validated,
        'difference': validated - expected,
    }


REVIEW_CASE_KEYS = tuple(case['case_key'] for case in WS026_REVIEW_CASES)


def review_case_states(outcome=None, resolved=(), unresolved=()):
    """
    Per-case demo review states {case_key: 'resolved' | 'reviewed_unresolved'}.

    `outcome` is the canonical shortcut (resolved: both resolved; unresolved: Mayo-Hegar
    resolved + Suture Needle Set Reviewed – Unresolved). `resolved` / `unresolved` are the
    case keys decided step by step in the Resolution tab; they override the shortcut.
    Unknown keys are ignored. Cases not listed stay Review Required.
    """
    outcome = normalize_review_outcome(outcome)
    states = {}
    if outcome == REVIEW_OUTCOME_RESOLVED:
        states = {key: 'resolved' for key in REVIEW_CASE_KEYS}
    elif outcome == REVIEW_OUTCOME_UNRESOLVED:
        states = {key: ('reviewed_unresolved' if key == UNRESOLVED_DEMO_CASE_KEY else 'resolved')
                  for key in REVIEW_CASE_KEYS}
    for key in resolved or ():
        if key in REVIEW_CASE_KEYS:
            states[key] = 'resolved'
    for key in unresolved or ():
        if key in REVIEW_CASE_KEYS:
            states[key] = 'reviewed_unresolved'
    return states


def ws026_review_cases(outcome=None, states=None):
    """WS-026 review cases with the Supervisor demo outcome / per-case states applied."""
    if states is None:
        states = review_case_states(outcome)
    cases = deepcopy(WS026_REVIEW_CASES)
    for case in cases:
        case['review_status'] = review_status(states.get(case['case_key'], case['review_status']))
        case['severity'] = severity(case['severity'])
        if case['review_status']['code'] == 'reviewed_unresolved':
            case['applied_resolution'] = case['unresolved_resolution'] or deepcopy(GENERIC_UNRESOLVED_RESOLUTION)
        elif case['review_status']['code'] == 'resolved':
            case['applied_resolution'] = case['resolution']
        else:
            case['applied_resolution'] = None
    return cases


def final_validation_summary(cases):
    reviewed = sum(case['review_status']['code'] != 'review_required' for case in cases)
    resolved = sum(case['review_status']['code'] == 'resolved' for case in cases)
    unresolved = len(cases) - resolved
    return {
        'reviewed': reviewed,
        'resolved': resolved,
        'unresolved': unresolved,
        'blocking': unresolved,
        'ready': reviewed == len(cases) and unresolved == 0,
        **deepcopy(FINAL_VALIDATION),
    }


def ws026_timeline(scenario=DEFAULT_SCENARIO):
    events = deepcopy(WS026_TIMELINE)
    if normalize_scenario(scenario) == SCENARIO_CLEAN:
        events = [event for event in events if event['code'] not in CLEAN_SCENARIO_EXCLUDED_EVENTS]
    return events


def session_row(session_id, **overrides):
    row = deepcopy(SESSION_ROWS[normalize_session_id(session_id)])
    row.update(overrides)
    row['procedure_label'] = f"{row['procedure_name']} - {row['operating_room']}"
    row['session_status'] = session_status(row['session_status'])
    row['review_status'] = review_status(row['review_status'])
    return row
