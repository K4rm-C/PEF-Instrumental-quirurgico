"""MS-12 (ms-tracking-instrumental): the complete tracking event log of the ACCEPTED final run is persisted in
tracking_event, linked to its final count event. Real PostgreSQL (rolled back); the worker is simulated at the
vision_bridge boundary. Traceability only: the count is still the last valid frame and a rejected count stores nothing."""
import uuid

import pytest
import requests
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services import vision_bridge

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401  (pytest fixture)
from .test_single_video_flow import _lots, _post, _register_initial, flow, frame, worker  # noqa: F401

EVENTS = [{'t': 0.5, 'frame': 15, 'track_id': 1, 'event': 'confirmed', 'family': 'KELLY', 'conf': 0.91},
          {'t': 2.0, 'frame': 60, 'track_id': 1, 'event': 'lost', 'family': 'KELLY', 'conf': 0.88},
          {'t': 2.4, 'frame': 72, 'track_id': 1, 'event': 'recovered', 'family': 'KELLY', 'conf': 0.9, 'gap_s': 0.4},
          {'t': 3.0, 'frame': 90, 'track_id': 2, 'event': 'class_change', 'family': 'CRILE', 'conf': 0.7,
           'from': 'KELLY'},
          {'t': 3.1, 'frame': 93, 'track_id': 3, 'event': 'bogus'}]                  # unknown kinds are not stored


def _stored(tray):
    return tray.c.execute(text("SELECT seq, event, track_id, family_code, detail, count_event_id FROM tracking_event "
                               "WHERE session_id = :s ORDER BY seq"), {'s': tray.session.id}).mappings().all()


def _final(flow, events_in_payload):
    assert _register_initial(flow).status_code == 200
    last = frame(flow.tray, 8, 7, position='last', index=297, run_id='run-1')
    last['tracking'] = {'events_recorded': len(EVENTS), 'events': events_in_payload}
    flow.state = worker(flow.tray, status='ready', run_id='run-1', first=flow.state['initial_frame'], last=last)
    return _post(flow, '/capture/final')


def test_the_complete_log_of_the_accepted_run_is_persisted(flow, monkeypatch):
    asked = []
    monkeypatch.setattr(vision_bridge, 'fetch_tracking_events', lambda sid, run_id: asked.append(run_id) or EVENTS)
    assert _final(flow, EVENTS[-1:]).status_code == 200
    rows = _stored(flow.tray)
    final = [lot for lot in _lots(flow.tray) if lot['payload'].get('trigger') == 'final']
    assert asked == ['run-1'] and final[0]['detected_quantity'] == 15            # the count is the last frame
    assert [r['event'] for r in rows] == ['confirmed', 'lost', 'recovered', 'class_change']
    assert rows[2]['detail'] == {'gap_s': 0.4} and rows[3]['detail'] == {'from': 'KELLY'}
    count_id = flow.tray.c.execute(text("SELECT id FROM count_event WHERE session_id = :s AND payload->>'source' = "
                                        "'last_frame'"), {'s': flow.tray.session.id}).scalar_one()
    assert {r['count_event_id'] for r in rows} == {count_id}


def test_without_the_worker_the_bounded_copy_of_the_payload_is_persisted(flow):
    assert _final(flow, EVENTS[:2]).status_code == 200                          # fixture: worker gives nothing
    assert [r['event'] for r in _stored(flow.tray)] == ['confirmed', 'lost']


def test_a_rejected_final_count_persists_no_tracking(flow, monkeypatch):
    monkeypatch.setattr(vision_bridge, 'fetch_tracking_events', lambda sid, run_id: EVENTS)
    assert _register_initial(flow).status_code == 200
    last = frame(flow.tray, 8, 6, position='last', index=297, run_id='run-1')
    last['tracking'] = {'events': EVENTS}
    flow.state = worker(flow.tray, status='ready', run_id='run-1', first=flow.state['initial_frame'], last=last)
    assert _post(flow, '/capture/final').status_code == 409
    assert _stored(flow.tray) == []


def test_tracking_events_are_immutable(flow, monkeypatch):
    monkeypatch.setattr(vision_bridge, 'fetch_tracking_events', lambda sid, run_id: EVENTS)
    assert _final(flow, []).status_code == 200
    with pytest.raises(DBAPIError, match='immutable'):
        with flow.tray.c.begin_nested():
            flow.tray.c.execute(text("UPDATE tracking_event SET event = 'lost' WHERE session_id = :s"),
                                {'s': flow.tray.session.id})


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_fetch_paginates_and_refuses_another_run(monkeypatch):
    pages = {0: {'run_id': 'r1', 'events': [{'event': 'confirmed'}] * 500, 'events_total': 700},
             500: {'run_id': 'r1', 'events': [{'event': 'lost'}] * 200, 'events_total': 700}}
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params['offset'])
        return _Resp(200, pages[params['offset']])
    monkeypatch.setattr(requests, 'get', fake_get)
    events = vision_bridge.fetch_tracking_events(uuid.uuid4(), 'r1')
    assert len(events) == 700 and calls == [0, 500]
    assert vision_bridge.fetch_tracking_events(uuid.uuid4(), 'r2') is None       # another run: never mixed
    assert vision_bridge.fetch_tracking_events(uuid.uuid4(), None) is None

    def down(*a, **k):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(requests, 'get', down)
    assert vision_bridge.fetch_tracking_events(uuid.uuid4(), 'r1') is None
