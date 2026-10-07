"""Validate board-scoped vision auto_count + discrepancy origin (terminal).

Run with Postgres up and BackendWebFlask venv:
  cd apps/BackendWebFlask
  python ../../scripts/smoke_vision_board_events.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1] / "apps" / "BackendWebFlask"
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, func  # noqa: E402

from app import app  # noqa: E402
from controllers.view_data import operator_session_detail  # noqa: E402
from extensions import db  # noqa: E402
from models.CountEvent import CountEvent  # noqa: E402
from models.Discrepancy import Discrepancy  # noqa: E402
from models.ExpectedInventory import ExpectedInventory  # noqa: E402
from models.InstrumentFamily import InstrumentFamily  # noqa: E402
from models.WorkSession import WorkSession  # noqa: E402
from services.rf_session import (  # noqa: E402
    _discrepancy_reason_id,
    clear_auto_count_flush_keys_for_session,
    persist_board_auto_count,
    persist_vision_timeline_windows,
)


SESSION_ID = UUID("c1000002-0000-4000-8000-000000000001")
OPERATOR_ID = UUID("22222222-2222-2222-2222-222222222221")


def _synthetic_counts(expected_rows: list) -> dict:
    counts = {}
    for inv, family in expected_rows:
        eq = int(inv.expected_quantity or 0)
        det = 0 if family.code == "MAYOHEG" else eq
        counts[family.code] = {
            "family_id": str(family.id),
            "family_code": family.code,
            "family_name": family.name,
            "category_name": "Type",
            "expected_quantity": eq,
            "raw_detected_quantity": det,
            "detected_quantity": det,
            "difference": det - eq,
            "best_confidence": 0.81 if det else None,
            "weakest_confidence": 0.81 if det else None,
            "avg_confidence": 0.81 if det else None,
            "held": False,
            "sticky_name": False,
            "reassigned_in": 0,
            "reassigned_out": 0,
            "boxes": [
                {
                    "xyxy": [10, 10, 40, 40],
                    "yolo_top1": {
                        "family_code": family.code,
                        "family_id": str(family.id),
                        "score": 0.81,
                    },
                    "assigned": {
                        "family_code": family.code,
                        "family_id": str(family.id),
                        "score": 0.81,
                        "reason": "kept_raw",
                    },
                    "topk": [
                        {
                            "family_code": family.code,
                            "family_id": str(family.id),
                            "score": 0.81,
                        }
                    ],
                }
            ]
            if det
            else [],
        }
    return counts


def main() -> int:
    run_id = uuid4().hex[:12]
    with app.app_context():
        work = db.session.get(WorkSession, SESSION_ID)
        if work is None:
            print("FAIL: seed session WS-C1000002 not found; bring up docker + seed first")
            return 1

        expected_rows = db.session.execute(
            select(ExpectedInventory, InstrumentFamily)
            .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
            .where(ExpectedInventory.session_id == SESSION_ID)
            .order_by(InstrumentFamily.code)
        ).all()
        if not expected_rows:
            print("FAIL: session has no expected_inventory")
            return 1

        clear_auto_count_flush_keys_for_session(SESSION_ID)
        before = db.session.scalar(
            select(func.count()).select_from(CountEvent).where(
                CountEvent.session_id == SESSION_ID
            )
        )

        counts = _synthetic_counts(expected_rows)
        metrics = {
            "solver": "hungarian",
            "extras_raw": 0,
            "extras_refined": 0,
            "missing_raw": 1,
            "missing_refined": 1,
            "reassign_count": 0,
            "type_degrade_count": 0,
            "family_match_rate_raw": 0.92,
            "family_match_rate_refined": 0.92,
        }
        samples = [
            {
                "run_id": run_id,
                "frame_index": 50,
                "frames_processed": 10,
                "video_t": 1.6,
                "pipeline": "matrix_v1",
                "assign_solver": "hungarian",
                "model_version": "yolo26l-demo",
                "count_sample_every": 10,
                "conf_threshold": 0.70,
                "assignment_metrics": metrics,
                "counts": counts,
                "include_boxes": False,
            },
            {
                "run_id": run_id,
                "frame_index": 100,
                "frames_processed": 20,
                "video_t": 3.3,
                "pipeline": "matrix_v1",
                "assign_solver": "hungarian",
                "model_version": "yolo26l-demo",
                "count_sample_every": 10,
                "counts": counts,
                "include_boxes": False,
            },
        ]

        n_windows = persist_vision_timeline_windows(
            session_id=SESSION_ID,
            operator_user_id=OPERATOR_ID,
            samples=samples,
            model_version="yolo26l-demo",
        )
        n_ready = persist_board_auto_count(
            session_id=SESSION_ID,
            operator_user_id=OPERATOR_ID,
            counts=counts,
            model_version="yolo26l-demo",
            reason="video_ready",
            once_key=f"{SESSION_ID}:video_ready:{run_id}:20",
            include_boxes=True,
            sample_meta={
                "run_id": run_id,
                "frame_index": 100,
                "frames_processed": 20,
                "video_t": 3.3,
                "pipeline": "matrix_v1",
                "assign_solver": "hungarian",
                "model_version": "yolo26l-demo",
                "count_sample_every": 10,
                "conf_threshold": 0.70,
                "assignment_metrics": metrics,
            },
        )

        recent = db.session.execute(
            select(CountEvent)
            .where(CountEvent.session_id == SESSION_ID, CountEvent.family_id.is_(None))
            .order_by(CountEvent.occurred_at.desc())
            .limit(20)
        ).scalars().all()
        board_events = [
            ev for ev in recent if (ev.payload or {}).get("run_id") == run_id
        ]
        board_events.sort(key=lambda e: e.occurred_at or e.id)

        print("--- board persist ---")
        print("windows_written", n_windows, "ready_written", n_ready)
        print("board_events_for_run", len(board_events))
        assert n_windows == 2, n_windows
        assert n_ready == 1, n_ready
        assert len(board_events) == 3, len(board_events)

        for ev in board_events:
            payload = ev.payload or {}
            assert payload.get("scope") == "board"
            assert ev.family_id is None
            families = (payload.get("board") or {}).get("families") or []
            assert len(families) == len(expected_rows), len(families)
            assert "edges" not in payload
            assert "score_matrix" not in payload
            print(
                payload.get("reason"),
                "exp",
                ev.expected_quantity,
                "det",
                ev.detected_quantity,
                "families",
                len(families),
                "boxes_in_payload",
                sum(1 for f in families if f.get("boxes")),
            )

        ready = next(ev for ev in board_events if (ev.payload or {}).get("reason") == "video_ready")
        ready_families = (ready.payload or {}).get("board", {}).get("families") or []
        assert any(f.get("boxes") for f in ready_families)
        window = next(ev for ev in board_events if (ev.payload or {}).get("reason") == "video_window")
        window_families = (window.payload or {}).get("board", {}).get("families") or []
        assert not any(f.get("boxes") for f in window_families)

        mayo = next(f for _inv, f in expected_rows if f.code == "MAYOHEG")
        disc = Discrepancy(
            description="Smoke: shortfall Mayo-Hegar vs board snapshot",
            resolved=False,
            reason_id=_discrepancy_reason_id("shortage"),
            family_id=mayo.id,
            expected_quantity=1,
            detected_quantity=0,
            session_id=SESSION_ID,
            origin_event_id=ready.id,
        )
        db.session.add(disc)
        db.session.commit()

        linked = db.session.execute(
            select(Discrepancy, CountEvent, InstrumentFamily)
            .join(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
            .join(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
            .where(Discrepancy.id == disc.id)
        ).one()
        _d, origin, fam = linked
        assert origin.family_id is None
        assert (origin.payload or {}).get("scope") == "board"
        assert fam.code == "MAYOHEG"
        print("--- discrepancy ---")
        print(
            "disc_family",
            fam.code,
            "origin_scope",
            (origin.payload or {}).get("scope"),
            "origin_reason",
            (origin.payload or {}).get("reason"),
        )

        detail = operator_session_detail(str(SESSION_ID), str(OPERATOR_ID))
        assert detail is not None
        board_tl = [
            e
            for e in detail.get("timeline") or []
            if e.get("scope") == "board" and (e.get("payload") or {}).get("run_id") == run_id
        ]
        assert len(board_tl) >= 3, len(board_tl)
        mayo_item = next(
            (x for x in detail.get("expected_items") or [] if x.get("family_code") == "MAYOHEG"),
            None,
        )
        assert mayo_item is not None
        assert mayo_item.get("ai_detected_quantity") == 0
        assert mayo_item.get("difference") == -1
        disc_rows = detail.get("discrepancies") or []
        assert any(
            str(disc.id) == r.get("id") or "Mayo" in (r.get("family_name") or "")
            for r in disc_rows
        )

        after = db.session.scalar(
            select(func.count()).select_from(CountEvent).where(
                CountEvent.session_id == SESSION_ID
            )
        )
        print("--- ui detail ---")
        print("timeline_board_rows", len(board_tl))
        print("mayo_ai", mayo_item.get("ai_detected_quantity"), mayo_item.get("status_label"))
        print("discrepancies_in_detail", len(disc_rows))
        print("count_event_delta", int(after or 0) - int(before or 0))
        print("OK smoke_vision_board_events")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
