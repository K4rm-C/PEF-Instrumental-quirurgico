"""Smoke Fase 3.3: SP-06 resolve + SP-05 confirm close on seed sessions D/E."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1] / "apps" / "BackendWebFlask"
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models.CatSessionStatus import CatSessionStatus  # noqa: E402
from models.Discrepancy import Discrepancy  # noqa: E402
from models.WorkSession import WorkSession  # noqa: E402
from services.rf_session import confirm_spd_close, resolve_discrepancy  # noqa: E402

SESSION_D = UUID("c1000004-0000-4000-8000-000000000001")  # awaiting_spd_review
SESSION_E = UUID("c1000005-0000-4000-8000-000000000001")  # correction_required
SUP = UUID("22222222-2222-2222-2222-222222222222")
INST = UUID("11111111-1111-1111-1111-111111111111")


def _status_code(session_id: UUID) -> str:
    work = db.session.get(WorkSession, session_id)
    status = db.session.get(CatSessionStatus, work.status_id)
    return status.code


def main() -> None:
    with app.app_context():
        print("D before", _status_code(SESSION_D))
        print("E before", _status_code(SESSION_E))

        open_disc = db.session.scalar(
            select(Discrepancy).where(
                Discrepancy.session_id == SESSION_E,
                Discrepancy.resolved.is_(False),
            )
        )
        if open_disc is None:
            raise SystemExit("Seed E missing open discrepancy — recreate volumes (down -v).")

        resolved = resolve_discrepancy(
            discrepancy_id=open_disc.id,
            supervisor_user_id=SUP,
            institution_id=INST,
            notes="Smoke 3.3: reconciled against tray photo",
            mark_lost=False,
        )
        print("resolve E", resolved)

        closed = confirm_spd_close(
            session_id=SESSION_D,
            supervisor_user_id=SUP,
            institution_id=INST,
            material_recovered=True,
        )
        print("close D", closed)

        # After all open discs resolved, E should be awaiting_spd_review
        if _status_code(SESSION_E) == "awaiting_spd_review":
            closed_e = confirm_spd_close(
                session_id=SESSION_E,
                supervisor_user_id=SUP,
                institution_id=INST,
                material_recovered=True,
            )
            print("close E", closed_e)

        print("D after", _status_code(SESSION_D))
        print("E after", _status_code(SESSION_E))


if __name__ == "__main__":
    main()
