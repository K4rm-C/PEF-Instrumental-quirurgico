"""Smoke Fase 3.2: schedule + privacy Via A + vision Start. Run from BackendWebFlask with venv."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1] / "apps" / "BackendWebFlask"
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models.CaptureStation import CaptureStation  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from models.OperatingRoom import OperatingRoom  # noqa: E402
from models.Patient import Patient  # noqa: E402
from models.PrivacyNoticeVersion import PrivacyNoticeVersion  # noqa: E402
from services.rf_session import (  # noqa: E402
    RfSessionError,
    confirm_privacy_via_a,
    kit_expected_lines,
    schedule_session,
    start_session,
)


def main() -> None:
    op = UUID("22222222-2222-2222-2222-222222222221")
    sup = UUID("22222222-2222-2222-2222-222222222222")
    inst = UUID("11111111-1111-1111-1111-111111111111")
    kit = UUID("51515151-0000-4000-8000-000000000001")

    with app.app_context():
        lines = kit_expected_lines(kit)
        expected = []
        for line in lines:
            qty = 1 if line["family_code"] == "FARABEUF" else line["expected_quantity"]
            expected.append({"family_id": line["family_id"], "expected_quantity": qty})
            print(line["family_code"], qty, "avail", line["stock_available"])

        pt = db.session.scalar(select(CatProcedureType).where(CatProcedureType.code == "lap_chole"))
        room = db.session.scalar(select(OperatingRoom).limit(1))
        station = db.session.scalar(select(CaptureStation).limit(1))
        patient = db.session.scalar(select(Patient).limit(1))

        created = schedule_session(
            supervisor_user_id=sup,
            institution_id=inst,
            operator_user_id=op,
            procedure_type_id=pt.id,
            room_id=room.id,
            station_id=station.id,
            kit_id=kit,
            patient_id=patient.id,
            physician_id=None,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
            phase_code="setup",
            expected_lines=expected,
        )
        print("scheduled", created)

        notice = db.session.scalar(select(PrivacyNoticeVersion).where(PrivacyNoticeVersion.active.is_(True)))
        privacy = confirm_privacy_via_a(
            session_id=UUID(created["session_id"]),
            supervisor_user_id=sup,
            institution_id=inst,
            privacy_notice_version_id=notice.id,
            purpose_model_improvement=False,
        )
        print("privacy", privacy)

        try:
            vision = start_session(
                session_id=UUID("c1000002-0000-4000-8000-000000000001"),
                operator_user_id=op,
                institution_id=inst,
            )
            print("vision_start", vision)
        except RfSessionError as exc:
            print("vision_start_err", exc.message)

        print("OK smoke_rf_32")


if __name__ == "__main__":
    main()
