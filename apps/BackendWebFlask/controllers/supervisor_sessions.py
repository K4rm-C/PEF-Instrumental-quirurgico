"""Supervisor session operations of the RF screens: one entry point, one implementation per request.

- SESSION_SERVICE_URL configured → SessionService over HTTP (services/session_remote.py) for every
  Supervisor operation on sessions (schedule, full edit, privacy agreement, expected inventory and
  confirmed additions, discrepancy queue and resolution, SPD close) and for the session reads of the
  Supervisor screens. No fallback: if SessionService fails the screen shows a controlled error and
  nothing is written locally.
- Not configured → the in-process implementation (services/rf_session.py on PostgreSQL), the
  development fallback.

Both paths return the same structures (RF rows built by controllers/view_data.py from the same
session snapshot), so the templates are unchanged. The Operator's screens never come through here:
they keep reading and writing PostgreSQL locally (rf_session.py, view_data.operator_*), so the
Operator does not depend on SessionService.
"""

from __future__ import annotations

from uuid import UUID

from controllers import view_data
from i18n import N_
from services import rf_session, session_remote
from services.rf_session import RfSessionError
from services.session_snapshot import parse_iso


def remote() -> bool:
    return session_remote.enabled()


# --- Reads -----------------------------------------------------------------------------------------


def session_rows(institution_id):
    """Supervisor list rows (active, history and dashboard screens)."""
    if remote():
        return view_data.session_rows_from_snapshots(session_remote.list_sessions(), for_role="supervisor")
    return view_data.sessions_data(institution_id)


def session_view(session_id, institution_id):
    """{'detail': RF detail row, 'contingency': contingency or None} or None when not found."""
    if remote():
        try:
            UUID(str(session_id))
        except (TypeError, ValueError):
            return None
        snapshot = session_remote.session_detail(session_id)
        if snapshot is None:
            return None
        contingency = snapshot.get("contingency")
        if contingency:
            contingency = {**contingency, "occurred_at": parse_iso(contingency.get("occurred_at"))}
        return {"detail": view_data.session_detail_from_snapshot(snapshot, for_role="supervisor"),
                "contingency": contingency}
    detail = view_data.supervisor_session_detail(session_id, institution_id)
    if detail is None:
        return None
    return {"detail": detail, "contingency": rf_session.contingency_for_session(UUID(str(session_id)))}


def discrepancy_rows(institution_id, *, search="", instrument="", status="", sessions=None):
    if remote():
        rows = sessions if sessions is not None else session_rows(institution_id)
        return view_data.discrepancy_rows_from_snapshots(session_remote.list_discrepancies(), rows,
                                                         search=search, instrument=instrument, status=status)
    return view_data.discrepancies_data(institution_id, search=search, instrument=instrument, status=status)


# --- Writes ----------------------------------------------------------------------------------------


def schedule_session(*, supervisor_user_id, institution_id, privacy_notice_version_id=None,
                     purpose_model_improvement=False, ip=None, **fields):
    """RF-SP-02 schedule (+ optional Via A privacy). Remote: one SessionService transaction."""
    if remote():
        return session_remote.schedule_session(privacy_notice_version_id=privacy_notice_version_id,
                                               purpose_model_improvement=purpose_model_improvement, **fields)
    result = rf_session.schedule_session(supervisor_user_id=supervisor_user_id, institution_id=institution_id,
                                         ip=ip, **fields)
    if privacy_notice_version_id:
        rf_session.confirm_privacy_via_a(
            session_id=UUID(result["session_id"]), supervisor_user_id=supervisor_user_id,
            institution_id=institution_id, privacy_notice_version_id=privacy_notice_version_id,
            purpose_model_improvement=purpose_model_improvement, ip=ip,
        )
    return result


def confirm_privacy(*, session_id, supervisor_user_id, institution_id, privacy_notice_version_id,
                    purpose_model_improvement, ip=None):
    if remote():
        return session_remote.confirm_privacy(session_id, privacy_notice_version_id=privacy_notice_version_id,
                                              purpose_model_improvement=purpose_model_improvement)
    return rf_session.confirm_privacy_via_a(
        session_id=session_id, supervisor_user_id=supervisor_user_id, institution_id=institution_id,
        privacy_notice_version_id=privacy_notice_version_id, purpose_model_improvement=purpose_model_improvement,
        ip=ip,
    )


def update_scheduled_session(*, session_id, supervisor_user_id, institution_id, ip=None, **fields):
    if remote():
        return session_remote.update_scheduled_session(session_id, **fields)
    return rf_session.update_scheduled_session(session_id=session_id, supervisor_user_id=supervisor_user_id,
                                               institution_id=institution_id, ip=ip, **fields)


def update_session_inventory(*, session_id, supervisor_user_id, institution_id, lines, physically_added=False,
                             ip=None, previous_detail=None):
    if remote():
        result = session_remote.update_inventory(session_id, lines=lines, physically_added=physically_added)
        if physically_added and previous_detail and previous_detail.get("status_code") == "in_progress":
            before = {item["family_id"]: item["expected_quantity"] for item in previous_detail.get("expected_items", [])}
            if any(int(line["expected_quantity"]) > before.get(str(line["family_id"]), 0) for line in lines):
                # A frozen final count no longer matches the new reference: the Operator captures again.
                rf_session.clear_vision_snapshot(session_id)
        return {"session_id": str(session_id), "lines": len(result)}
    return rf_session.update_session_inventory(session_id=session_id, supervisor_user_id=supervisor_user_id,
                                               institution_id=institution_id, lines=lines, ip=ip,
                                               physically_added=physically_added)


def resolve_discrepancy(*, discrepancy_id, session_id, supervisor_user_id, institution_id, notes,
                        mark_lost=False, mark_recovered=False, ip=None):
    if remote():
        if mark_lost and mark_recovered:
            raise RfSessionError(N_("Choose Lost or Recovered, not both."), 400)
        outcome = "lost" if mark_lost else "recovered" if mark_recovered else "resolved"
        return session_remote.resolve_discrepancy(session_id, discrepancy_id, notes=notes, outcome=outcome)
    return rf_session.resolve_discrepancy(
        discrepancy_id=discrepancy_id, session_id=session_id, supervisor_user_id=supervisor_user_id,
        institution_id=institution_id, notes=notes, mark_lost=mark_lost, mark_recovered=mark_recovered, ip=ip,
    )


def confirm_spd_close(*, session_id, supervisor_user_id, institution_id, material_recovered,
                      contingency_reviewed=False, unreconciled_shortage_acknowledged=False, ip=None):
    if remote():
        return session_remote.close_session(session_id, material_recovered=material_recovered,
                                            contingency_reviewed=contingency_reviewed,
                                            unreconciled_shortage_acknowledged=unreconciled_shortage_acknowledged)
    return rf_session.confirm_spd_close(
        session_id=session_id, supervisor_user_id=supervisor_user_id, institution_id=institution_id,
        material_recovered=material_recovered, ip=ip, contingency_reviewed=contingency_reviewed,
        unreconciled_shortage_acknowledged=unreconciled_shortage_acknowledged,
    )
