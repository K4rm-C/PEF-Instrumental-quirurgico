"""Localized presentation labels for stable codes (catalog rows, roles, workflow states).

The translation key is (catalog, code): gettext msgctxt = catalog, msgid = canonical English
label, so `label('session_status', 'open')` is the concept "session_status.open". Display names
stored in the database are never used as keys and never compared to decide behaviour.

Rules
- Unknown code (a row created by an administrator): the stored value is shown as stored.
- USER_EDITABLE catalogs: administrators can rename seeded rows. When the stored value is no
  longer the canonical English seed value it is user content and is shown exactly as stored.
- Missing translation: gettext returns the English msgid (never an error).

Adding a translatable code: add it below, run the extract/update/compile commands documented
in docs/IMPLEMENTATION_STATUS.md and translate the new msgctxt/msgid in translations/es.
"""
from babel import dates, numbers
from flask_babel import gettext, pgettext


def _c(context, message):
    """Extraction marker (babel.cfg keyword _c:1c,2); the value is the canonical English msgid."""
    return message


CATALOGS = {
    # ---- 002_seed_catalogs.sql (cat_* tables; codes are stable, names canonical English)
    'gender': {
        'female': _c('gender', 'Female'),
        'male': _c('gender', 'Male'),
        'other': _c('gender', 'Other'),
        'unknown': _c('gender', 'Unspecified'),
    },
    'specialty': {
        'general_surgery': _c('specialty', 'General Surgery'),
        'orthopedics': _c('specialty', 'Orthopedics and Traumatology'),
        'gynecology': _c('specialty', 'Gynecology and Obstetrics'),
        'urology': _c('specialty', 'Urology'),
        'neurosurgery': _c('specialty', 'Neurosurgery'),
        'anesthesiology': _c('specialty', 'Anesthesiology'),
    },
    'procedure_type': {
        'lap_chole': _c('procedure_type', 'Laparoscopic Cholecystectomy'),
        'open_chole': _c('procedure_type', 'Open Cholecystectomy'),
        'appendectomy': _c('procedure_type', 'Appendectomy'),
        'hernia_repair': _c('procedure_type', 'Inguinal Hernia Repair'),
        'cesarean': _c('procedure_type', 'Cesarean Section'),
        'hip_replace': _c('procedure_type', 'Hip Arthroplasty'),
    },
    'surgical_role': {
        'surgeon': _c('surgical_role', 'Surgeon'),
        'first_assistant': _c('surgical_role', 'First Assistant'),
        'anesthesiologist': _c('surgical_role', 'Anesthesiologist'),
        'resident': _c('surgical_role', 'Resident Physician'),
    },
    'operation_status': {
        'scheduled': _c('operation_status', 'Scheduled'),
        'in_progress': _c('operation_status', 'In Progress'),
        'closed': _c('operation_status', 'Closed'),
        'cancelled': _c('operation_status', 'Cancelled'),
    },
    'session_status': {
        'open': _c('session_status', 'Open'),
        'counting': _c('session_status', 'Counting'),
        'validating': _c('session_status', 'Validating'),
        'blocked': _c('session_status', 'Blocked by Discrepancy'),
        'closed': _c('session_status', 'Closed'),
        'cancelled': _c('session_status', 'Cancelled'),
    },
    'instrument_cycle_status': {
        'available': _c('instrument_cycle_status', 'Available'),
        'reserved': _c('instrument_cycle_status', 'Reserved'),
        'in_use': _c('instrument_cycle_status', 'In Use'),
        'sterilization': _c('instrument_cycle_status', 'In Sterilization'),
        'maintenance': _c('instrument_cycle_status', 'In Maintenance'),
        'retired': _c('instrument_cycle_status', 'Retired'),
    },
    'instrument_category': {
        'hemostasis': _c('instrument_category', 'Hemostasis'),
        'cutting': _c('instrument_category', 'Cutting'),
        'dissection': _c('instrument_category', 'Dissection'),
        'retraction': _c('instrument_category', 'Retraction'),
        'grasping': _c('instrument_category', 'Grasping'),
        'suturing': _c('instrument_category', 'Suturing'),
        'suction': _c('instrument_category', 'Suction'),
    },
    'usage_context': {
        'operative': _c('usage_context', 'Operative use during surgery'),
        'pedagogical': _c('usage_context', 'Teaching or training use'),
    },
    'discrepancy_reason': {
        'shortage': _c('discrepancy_reason', 'Shortage against expected inventory'),
        'surplus': _c('discrepancy_reason', 'Surplus against expected inventory'),
        'unidentified': _c('discrepancy_reason', 'Item present but not identified by the model'),
        'occluded': _c('discrepancy_reason', 'Item occluded or outside the region of interest'),
        'misclassified': _c('discrepancy_reason', 'Item classified into the wrong family'),
        'operator_error': _c('discrepancy_reason', 'Operator capture or handling error'),
        'low_confidence': _c('discrepancy_reason', 'Model confidence below threshold'),
    },
    'checkpoint_reason': {
        'start': _c('checkpoint_reason', 'Session start'),
        'close': _c('checkpoint_reason', 'Session close'),
        'hourly': _c('checkpoint_reason', 'Periodic capture'),
        'state_change': _c('checkpoint_reason', 'Relevant state change'),
        'discrepancy': _c('checkpoint_reason', 'Discrepancy recorded'),
        'manual_pin': _c('checkpoint_reason', 'Operator manual pin'),
    },
    'operation_phase': {
        'setup': _c('operation_phase', 'Table Setup'),
        'pre_incision': _c('operation_phase', 'Initial Count Before Incision'),
        'intraop': _c('operation_phase', 'Intraoperative'),
        'pre_closure': _c('operation_phase', 'Count Before Cavity Closure'),
        'closure': _c('operation_phase', 'Closure'),
        'final_count': _c('operation_phase', 'Final Count'),
        'handover': _c('operation_phase', 'Tray Handover and Removal'),
    },
    'event_type': {
        'session_open': _c('event_type', 'Session opened'),
        'auto_count': _c('event_type', 'Model-suggested count'),
        'manual_count': _c('event_type', 'Manual count'),
        'phase_change': _c('event_type', 'Surgical phase change'),
        'discrepancy_raised': _c('event_type', 'Discrepancy raised'),
        'correction_applied': _c('event_type', 'Human correction applied'),
        'validation_passed': _c('event_type', 'Validation without discrepancies'),
        'close_blocked': _c('event_type', 'Close attempt blocked'),
        'session_close': _c('event_type', 'Session closed'),
        'correction_requested': _c('event_type', 'Correction requested by supervisor'),
    },
    'processing_purpose': {
        'quality_ops': _c('processing_purpose', 'Counting process quality and operations'),
        'model_improvement': _c('processing_purpose', 'Vision model improvement'),
        'external_sharing': _c('processing_purpose', 'Third-party data sharing'),
    },
    # ---- seeded instrument families (004_seed_e2e.sql), keyed by instrument_family.code
    'instrument_family': {
        'KELLY': _c('instrument_family', 'Kelly Forceps'),
        'MOSQUITO': _c('instrument_family', 'Mosquito Forceps'),
        'METZ': _c('instrument_family', 'Metzenbaum Scissors'),
        'MAYOHEG': _c('instrument_family', 'Mayo-Hegar Needle Holder'),
        'FARABEUF': _c('instrument_family', 'Farabeuf Retractor'),
    },
    'instrument_family_function': {
        'KELLY': _c('instrument_family_function', 'Occlude small and medium-caliber vessels.'),
        'MOSQUITO': _c('instrument_family_function', 'Hemostasis of fine vessels.'),
        'METZ': _c('instrument_family_function', 'Cutting and dissection of delicate tissue.'),
        'MAYOHEG': _c('instrument_family_function', 'Hold the needle while suturing.'),
        'FARABEUF': _c('instrument_family_function', 'Retract superficial tissue planes.'),
    },
    # ---- application roles (role.code), never role.description
    'role': {
        'it_admin': _c('role', 'IT Administrator'),
        'operator_cde': _c('role', 'CDE Operator'),
        'supervisor_quality': _c('role', 'CDE Supervisor / Quality Supervisor'),
    },
    'role_short': {
        'it_admin': _c('role_short', 'Administrator'),
        'operator_cde': _c('role_short', 'Operator'),
        'supervisor_quality': _c('role_short', 'Supervisor'),
    },
    # role.description as seeded by 004 (editable free text: shown as stored once changed)
    'role_description': {
        'it_admin': _c('role_description', 'IT Administrator'),
        'operator_cde': _c('role_description', 'Operator CDE'),
        'supervisor_quality': _c('role_description', 'Supervisor CDE / Quality'),
    },
    # ---- application workflow codes (not catalog rows)
    'review_status': {
        'OPEN': _c('review_status', 'Open'),
        'UNDER_REVIEW': _c('review_status', 'Under Review'),
        'CORRECTION_REQUIRED': _c('review_status', 'Correction Required'),
        'APPROVED': _c('review_status', 'Approved'),
    },
    'vision_result': {
        'unidentified': _c('vision_result', 'Unidentified'),
        'shortage': _c('vision_result', 'Shortage'),
        'surplus': _c('vision_result', 'Surplus'),
        'low_confidence': _c('vision_result', 'Low confidence'),
        'match': _c('vision_result', 'Match'),
    },
    'supervisor_decision': {
        'request_correction': _c('supervisor_decision', 'Request correction'),
        'reject': _c('supervisor_decision', 'Reject'),
    },
    'audit_outcome': {
        'success': _c('audit_outcome', 'SUCCESS'),
        'denied': _c('audit_outcome', 'DENIED'),
    },
    'inference_provider': {
        'controlled': _c('inference_provider', 'Controlled inference (development provider)'),
    },
    'validation_kind': {
        'validation': _c('validation_kind', 'Human validation'),
        'supervisor_correction': _c('validation_kind', 'Correction requested by supervisor'),
    },
}

# Rows administrators can rename: a stored value different from the seed is shown as stored.
USER_EDITABLE = {'procedure_type', 'instrument_family', 'instrument_family_function', 'role_description'}


def label(catalog, code, stored=None):
    """Localized label for (catalog, code); the stored value when the code is not a known system value."""
    canonical = CATALOGS.get(catalog, {}).get(code) if code is not None else None
    if canonical is None:
        return stored if stored is not None else (code or '')
    if catalog in USER_EDITABLE and stored is not None and stored != canonical:
        return stored
    return pgettext(catalog, canonical)


def is_localized(catalog, code, stored=None):
    """True when label() would translate this value (it is an unmodified system value)."""
    canonical = CATALOGS.get(catalog, {}).get(code)
    return canonical is not None and not (catalog in USER_EDITABLE and stored is not None and stored != canonical)


def role_name(code, stored_description=None):
    """Role label by role.code; custom roles show their stored description (or code)."""
    return label('role', code, stored_description or code)


def active_label(active):
    return gettext('Active') if active else gettext('Inactive')


def _format_locale():
    # explicit Babel locale so formatting also works outside a request (English there)
    from localization import FORMAT_LOCALES, resolve_locale
    return FORMAT_LOCALES[resolve_locale()]


def format_number(value, pattern='#,##0.0'):
    """Locale-aware decimal (e.g. 80.5); non-numeric values are returned unchanged."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return numbers.format_decimal(value, format=pattern, locale=_format_locale())


def format_percentage(value):
    """95.0 -> '95.0%' (en) / '95.0 %' (es); None -> N/A."""
    if value is None:
        return gettext('N/A')
    return numbers.format_percent(value / 100, format='#,##0.0%', locale=_format_locale())


def format_day(value):
    """Short weekday + day of month for chart axes ('Mon 07' / 'lun 07')."""
    return dates.format_date(value, format='EEE dd', locale=_format_locale())
