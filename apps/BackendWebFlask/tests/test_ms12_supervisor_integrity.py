"""MS-12: the Supervisor's session detail shows the seal verification of the Operator's validation and the tracking
log of the accepted run (SessionService answers simulated; the service is tested on real PostgreSQL)."""

from tests.test_session_remote import SESSION_ID, service  # noqa: F401  (fixture)

SEAL = {'session_id': SESSION_ID, 'status': 'altered', 'algorithm': 'sha256', 'stored_sha256': 'a' * 64,
        'computed_sha256': 'b' * 64, 'close_event_id': None, 'closed_at': None, 'operator_user_id': None,
        'close_path': 'vision_validation', 'final_count_event_id': None, 'final_frame_sha256': None, 'lines': 2,
        'note': 'x'}
TRACKING = {'session_id': SESSION_ID, 'count_event_id': 'c1', 'run_id': 'run-1', 'accepted_at': None, 'summary': {},
            'events_recorded': 3, 'events_persisted': 2, 'complete': False, 'offset': 0, 'limit': 100,
            'events': [{'seq': 0, 'video_t': 0.5, 'frame_index': 15, 'track_id': 1, 'event': 'confirmed',
                        'family_code': 'KELLY', 'confidence': 0.9, 'detail': {}}]}


def test_the_detail_shows_an_altered_seal_and_an_incomplete_log(client, login, service):
    service.routes[('GET', f'/{SESSION_ID}/validation-seal')] = (200, SEAL)
    service.routes[('GET', f'/{SESSION_ID}/tracking-events')] = (200, TRACKING)
    login('spd_supervisor')
    page = client.get(f'/supervisor/sessions/{SESSION_ID}').get_data(as_text=True)
    assert 'data-seal-status="altered"' in page and 'no longer matches its seal' in page
    assert 'data-tracking-event="confirmed"' in page and 'incomplete log' in page
    assert 'it is not an electronic signature' in page


def test_the_page_still_renders_when_the_new_reads_fail(client, login, service):
    login('spd_supervisor')                                  # routes not configured -> 404 from the fake service
    response = client.get(f'/supervisor/sessions/{SESSION_ID}')
    assert response.status_code == 200 and 'data-session-integrity' in response.get_data(as_text=True)
