"""Helpers to present RF/UUID session data in V3 (and schedule) templates."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from extensions import db
from i18n import N_
from models.CatOperationPhase import CatOperationPhase
from models.ProcedurePhase import ProcedurePhase


def initial_phase_code_for_procedure(procedure_type_id: UUID | str | None) -> str:
    """First active procedure_phase by sort_order, else catalog 'start'."""
    if procedure_type_id:
        row = db.session.execute(
            select(CatOperationPhase.code)
            .join(ProcedurePhase, ProcedurePhase.phase_id == CatOperationPhase.id)
            .where(
                ProcedurePhase.procedure_type_id == UUID(str(procedure_type_id)),
                ProcedurePhase.active.is_(True),
            )
            .order_by(ProcedurePhase.sort_order)
            .limit(1)
        ).first()
        if row:
            return row[0]
    return 'start'


def confirmation_from_rf_detail(detail: dict) -> dict:
    """Shape expected by operator/sessions/begin.html from operator_session_detail()."""
    expected = [
        {
            'instrument_name': item.get('family_name'),
            'family_code': item.get('family_code'),
            'expected_quantity': item.get('expected_quantity'),
            'source': item.get('source'),
            'source_label': item.get('source_label'),
            'category_label': item.get('category_label'),
            'is_additional': item.get('is_additional'),
        }
        for item in detail.get('expected_items') or []
    ]
    groups = []
    for group in detail.get('expected_groups') or []:
        # Prefer "lines"; accept legacy "items" list only if it is a sequence (not dict.items).
        raw_lines = group.get('lines')
        if raw_lines is None:
            legacy = group.get('items')
            raw_lines = legacy if isinstance(legacy, (list, tuple)) else []
        groups.append({
            'key': group.get('key'),
            'label': group.get('label'),
            'lines': [
                {
                    'instrument_name': item.get('family_name') or item.get('instrument_name'),
                    'family_code': item.get('family_code'),
                    'expected_quantity': item.get('expected_quantity'),
                    'source_label': item.get('source_label'),
                }
                for item in raw_lines
            ],
        })
    if not groups and expected:
        groups = [{'key': 'all', 'label': N_('Expected inventory'), 'lines': expected}]
    total = sum(int(item.get('expected_quantity') or 0) for item in expected)
    privacy = detail.get('privacy') or {}
    team = detail.get('surgical_team') or []
    if not team:
        team = [{'role': N_('Surgeon'), 'name': detail.get('physician_name') or '—'}]
    return {
        'is_full_case': True,
        'session': {
            'id': detail.get('id'),
            'session_id': detail.get('session_id'),
            'procedure_name': detail.get('procedure_name'),
            'operating_room': detail.get('operating_room'),
            'scheduled_at_display': detail.get('scheduled_at') or '—',
            'kit_name': detail.get('kit_name'),
            'capture_station': detail.get('capture_station_name'),
            'operator_name': detail.get('operator_name'),
            'assigned_by': 'SPD Supervisor',
            'status_code': detail.get('status_code'),
            'capture_mode': detail.get('capture_mode'),
            'privacy_label': detail.get('privacy_label'),
            'privacy_variant': detail.get('privacy_variant'),
            'has_privacy_agreement': detail.get('has_privacy_agreement'),
            # begin.html reads patient/team from the session object (same as V3 fixture shape).
            'patient_name': detail.get('patient_name') or '—',
            'patient_record': detail.get('patient_record') or '—',
            'surgical_team': team,
        },
        'patient': {
            'name': detail.get('patient_name') or '—',
            'record': detail.get('patient_record') or '—',
        },
        'surgical_team': team,
        'inventory': {
            'kit_name': detail.get('kit_name'),
            'items': expected,
            'groups': groups,
            'total_expected': total,
        },
        'privacy': privacy,
    }


def past_session_rows(rows: list[dict], *, for_role: str = 'operator') -> list[dict]:
    """
    History lists.
    - Operator: anything no longer in active counting (submitted / closed / aborted).
    - Supervisor: terminal sessions only (closed / aborted); awaiting review stays in Sessions.
    """
    if for_role == 'supervisor':
        past_codes = {'closed', 'aborted'}
    else:
        past_codes = {
            'closed', 'aborted', 'awaiting_spd_review', 'correction_required',
        }
    return [row for row in rows if row.get('status_code') in past_codes]


def active_session_rows(rows: list[dict]) -> list[dict]:
    """Assigned / in-progress / SPD-actionable list (exclude terminal closed/aborted)."""
    skip = {'closed', 'aborted'}
    return [row for row in rows if row.get('status_code') not in skip]


def _id_options(rows: list[dict], id_key: str, label_key: str) -> list[dict]:
    """Select options: value = stable id (survives a language switch), label = shown name."""
    pairs = {(r[id_key], r.get(label_key)) for r in rows if r.get(id_key)}
    return [{'value': value, 'label': label} for value, label in sorted(pairs, key=lambda p: p[1] or '')]


def history_filter_options(rows: list[dict]) -> dict:
    return {
        'session_ids': sorted({r.get('session_id') for r in rows if r.get('session_id')}),
        'procedures': _id_options(rows, 'procedure_type_id', 'procedure_name'),
        'kits': _id_options(rows, 'kit_id', 'kit_name'),
        'operating_rooms': sorted({r.get('operating_room') for r in rows if r.get('operating_room')}),
        'stations': _id_options(rows, 'station_id', 'capture_station_name'),
    }


def apply_history_filters(rows: list[dict], filters: dict) -> list[dict]:
    """General + field-specific filters for OP/SPD history lists."""
    search = (filters.get('search') or '').strip().lower()
    session_id = (filters.get('session_id') or '').strip().lower()
    procedure = (filters.get('procedure') or '').strip()
    kit = (filters.get('kit') or '').strip()
    operating_room = (filters.get('operating_room') or '').strip()
    station = (filters.get('station') or '').strip()
    privacy = (filters.get('privacy') or '').strip().lower()
    closed_date = (filters.get('closed_date') or '').strip()
    status = (filters.get('status') or '').strip()

    out = []
    for row in rows:
        if search:
            hay = row.get('search_text') or str(row.get('session_id') or '').lower()
            if search not in hay:
                continue
        if session_id and session_id not in str(row.get('session_id') or '').lower():
            continue
        if procedure and row.get('procedure_type_id') != procedure:
            continue
        if kit and row.get('kit_id') != kit:
            continue
        if operating_room and row.get('operating_room') != operating_room:
            continue
        if station and row.get('station_id') != station:
            continue
        if privacy and privacy not in str(row.get('privacy_label') or '').lower():
            continue
        if closed_date and closed_date not in str(row.get('closed_at') or ''):
            continue
        code = row.get('status_code') or ''
        if status == 'closed' and code != 'closed':
            continue
        if status == 'aborted' and code != 'aborted':
            continue
        if status == 'discrepancy' and code != 'correction_required':
            continue
        if status == 'pending_review' and code != 'awaiting_spd_review':
            continue
        out.append(row)
    return out
