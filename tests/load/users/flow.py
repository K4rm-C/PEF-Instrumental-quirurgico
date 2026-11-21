"""End-to-end session flow without YOLO (manual mode, exact count, so no instrument is marked `lost`).

SPD schedules (portal -> SessionService) -> Operator begins (portal) -> manual quantity report with the
expected quantities -> awaiting_spd_review -> SPD closes. Each Locust user owns one SPD and one Operator
account, so concurrent flows never share a session.

Data written per iteration: one operation + work_session (closed) + audit rows. Start each campaign from a
clean volume (`run_load.ps1 -Reset`).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

from locust import HttpUser, between, task
from locust.clients import HttpSession
from locust.exception import RescheduleTask

import common
from users.base import rows_of

_PROCEDURE_SELECT = re.compile(r'name="procedure_type_id".*?</select>', re.S)
_OPTION = re.compile(r'<option value="([0-9a-fA-F-]{36})"[^>]*>\s*([^<]+?)\s*</option>')


class SessionFlowUser(HttpUser):
    host = common.WEB
    fixed_count = int(os.environ.get('PEF_FLOW_USERS', '1'))
    wait_time = between(15, 30)
    procedure_id: str | None = os.environ.get('PEF_LOCUST_PROCEDURE_ID') or None
    kit_lines: list[tuple[str, int]] | None = None

    def on_start(self):
        self.spd = self.client
        self.op = HttpSession(base_url=common.WEB, request_event=self.environment.events.request, user=self)
        self.spd_email = common.next_account('flow_supervisor')
        self.op_email = common.next_account('flow_operator')
        common.portal_login(self.spd, self.spd_email)
        common.portal_login(self.op, self.op_email)
        self.op_id = common.USER_IDS[self.op_email]
        self._load_references()

    def on_stop(self):
        for client in (self.spd, self.op):
            client.post(f'{common.AUTH}/logout', name='auth POST /logout')

    # --- references: procedure "Demo" from the schedule form, kit lines from CatalogService ---------
    def _load_references(self):
        cls = type(self)
        if cls.procedure_id is None:
            r = common.page(self.spd, '/supervisor/sessions/new')
            block = _PROCEDURE_SELECT.search(r.text or '')
            options = dict((name.strip().lower(), pid) for pid, name in _OPTION.findall(block.group(0) if block else ''))
            cls.procedure_id = options.get('demo')
            if cls.procedure_id is None:
                raise RuntimeError('Procedure "Demo" not found in /supervisor/sessions/new (set PEF_LOCUST_PROCEDURE_ID)')
        if cls.kit_lines is None:
            r = common.api_get(self.spd, f'{common.CATALOG}/api/v1/catalog/kits/{common.KIT}',
                               'catalog GET /api/v1/catalog/kits/[id]', lambda: common.cookie_bearer(self.spd),
                               lambda: common.portal_refresh(self.spd))
            body = r.json() if r is not None and r.status_code == 200 else {}
            cls.kit_lines = [(i['family_id'], int(i['quantity'])) for i in body.get('items', [])]
            if not cls.kit_lines:
                raise RuntimeError(f'Kit {common.KIT} has no items in CatalogService')

    @staticmethod
    def _abort(response, step):
        if response is None or response.status_code >= 400 or (response.status_code == 302 and
                                                                 '/sign-in' in response.headers.get('Location', '')):
            raise RescheduleTask(f'flow aborted at {step}')

    @task
    def full_session(self):
        started = time.perf_counter()

        # 1. SPD opens the wizard and schedules (no privacy agreement -> manual_no_privacy).
        common.page(self.spd, '/supervisor/sessions/new')
        when = (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M')
        r = common.portal_post(self.spd, '/supervisor/sessions/new', name='flow POST /supervisor/sessions/new',
                               ok_location='/supervisor/sessions/', data={
                                   'operator_user_id': self.op_id, 'procedure_type_id': self.procedure_id,
                                   'room_id': common.ROOM, 'station_id': common.STATION, 'kit_id': common.KIT,
                                   'patient_id': common.PATIENT, 'scheduled_at': when,
                                   'family_id[]': [f for f, _ in self.kit_lines],
                                   'expected_quantity[]': [str(n) for _, n in self.kit_lines],
                                   'physician_id[]': [common.PHYSICIAN], 'surgical_role_code[]': ['surgeon']})
        self._abort(r, 'schedule')
        sid = r.headers['Location'].rstrip('/').split('/')[-1]

        # 2. Operator sees it and begins.
        common.page(self.op, '/operator/sessions')
        common.page(self.op, f'/operator/sessions/{sid}/begin', name='/operator/sessions/[id]/begin')
        r = common.portal_post(self.op, f'/operator/sessions/{sid}/begin', data={'instruments_ready': '1'},
                               name='flow POST /operator/sessions/[id]/begin', ok_location='/manual')
        self._abort(r, 'begin')

        # 3. Manual progress and quantity report with the expected quantities (no discrepancy).
        common.page(self.op, f'/operator/sessions/{sid}/manual', name='/operator/sessions/[id]/manual')
        common.page(self.op, f'/operator/sessions/{sid}/manual-report', name='/operator/sessions/[id]/manual-report')
        r = common.portal_post(self.op, f'/operator/sessions/{sid}/manual-report',
                               data={f'reported_{f}': str(n) for f, n in self.kit_lines},
                               name='flow POST /operator/sessions/[id]/manual-report', ok_location='/done')
        self._abort(r, 'manual-report')

        # 4. SPD reviews and closes.
        common.page(self.spd, f'/supervisor/sessions/{sid}', name='/supervisor/sessions/[id]')
        rows_of(common.api_get(self.spd, f'{common.SESSION}/api/v1/sessions/{sid}/counts',
                               'session GET /api/v1/sessions/[id]/counts', lambda: common.cookie_bearer(self.spd),
                               lambda: common.portal_refresh(self.spd)))
        r = common.portal_post(self.spd, f'/supervisor/sessions/{sid}/close', data={'material_recovered': '1'},
                               name='flow POST /supervisor/sessions/[id]/close', ok_location=f'/supervisor/sessions/{sid}')
        self._abort(r, 'close')

        # Whole flow as one synthetic entry in the report.
        self.environment.events.request.fire(
            request_type='FLOW', name='session flow schedule->close', response_length=0, exception=None,
            response_time=(time.perf_counter() - started) * 1000, context={})
