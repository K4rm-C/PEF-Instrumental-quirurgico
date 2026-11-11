"""MS-12 (ms-validacion-humana): the Operator's close carries an integrity seal of what was validated.
Real PostgreSQL (rolled back), real portal close; the worker is simulated. SessionService recomputes the same seal
(apps/SessionService/tests/test_ms12_validation_seal.py asserts the same test vector)."""
from datetime import datetime, timezone

from sqlalchemy import text

from services import validation_seal

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401  (pytest fixture)
from .test_single_video_flow import _happy_path, flow, frame, worker  # noqa: F401

VECTOR = "0fa4703818ac7f6376f2054199f62cd6aa3e8b4be885b5949ca8aeb2fa05e61c"


def test_shared_test_vector():
    seal = validation_seal.seal(
        session_id="11111111-1111-1111-1111-111111111111", operator_user_id="22222222-2222-2222-2222-222222222222",
        closed_at=datetime(2026, 11, 10, 12, 0, 0, 123456, tzinfo=timezone.utc), close_path="vision_validation",
        lines=[{"family_id": "b", "expected_quantity": 2, "reported_quantity": 1, "reason_code": "shortage",
                "notes": " moved ", "ai_suggested_quantity": 2},
               {"family_id": "a", "expected_quantity": 3, "reported_quantity": 4, "reason_code": "", "notes": None,
                "ai_suggested_quantity": None}],
        final_count_event_id="33333333-3333-3333-3333-333333333333", final_frame_sha256="ab" * 32,
        extra_instruments=None)
    assert seal["sha256"] == VECTOR


def _rows(tray, code):
    return tray.c.execute(text(
        "SELECT ce.id, ce.user_id, ce.family_id, ce.expected_quantity, ce.detected_quantity, ce.payload, ce.occurred_at "
        "FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id WHERE ce.session_id = :s AND t.code = :c "
        "ORDER BY ce.occurred_at"), {'s': tray.session.id, 'c': code}).mappings().all()


def test_the_vision_validation_close_is_sealed_with_the_final_count_and_its_frame(app, flow):
    response = _happy_path(flow, validated=(9, 6), a={'reason': 'misclassified', 'notes': 'one moved to A'},
                           b={'reason': 'misclassified', 'notes': 'one moved to A'})
    assert response.status_code == 302 and '/manual-report/done' in response.headers['Location']
    close = _rows(flow.tray, 'manual_close')[-1]
    seal = close['payload']['validation_seal']
    final = next(r for r in _rows(flow.tray, 'auto_count') if (r['payload'] or {}).get('source') == 'last_frame')
    assert seal['algorithm'] == 'sha256' and seal['final_count_event_id'] == str(final['id'])
    assert seal['final_frame_sha256'] == final['payload']['frame_sha256']
    lines = [{'family_id': r['family_id'], 'expected_quantity': r['expected_quantity'],
              'reported_quantity': r['detected_quantity'], 'reason_code': r['payload'].get('reason_code'),
              'notes': r['payload'].get('notes'), 'ai_suggested_quantity': r['payload'].get('ai_suggested_quantity')}
             for r in _rows(flow.tray, 'manual_count') if r['occurred_at'] == close['occurred_at']]
    recomputed = validation_seal.seal(
        session_id=flow.tray.session.id, operator_user_id=close['user_id'], closed_at=close['occurred_at'],
        close_path=close['payload']['operator_close_path'], lines=lines, final_count_event_id=final['id'],
        final_frame_sha256=final['payload']['frame_sha256'], extra_instruments=close['payload'].get('extra_instruments'))
    assert len(lines) == 2 and recomputed['sha256'] == seal['sha256']           # what SessionService recomputes
    changed = [{**lines[0], 'reported_quantity': lines[0]['reported_quantity'] + 1}, lines[1]]
    assert validation_seal.seal(
        session_id=flow.tray.session.id, operator_user_id=close['user_id'], closed_at=close['occurred_at'],
        close_path='vision_validation', lines=changed, final_count_event_id=final['id'],
        final_frame_sha256=final['payload']['frame_sha256'])['sha256'] != seal['sha256']
