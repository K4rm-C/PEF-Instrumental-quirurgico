"""Discrepancy descriptions: rebuilt from structured fields, never from the stored text."""
import copy
import uuid
from types import SimpleNamespace

import pytest

import i18n
from controllers import routes, view_data
from i18n import localize_discrepancy_description

FARABEUF = SimpleNamespace(code='FARABEUF', name='Separador Farabeuf')
SPANISH_STORED = 'Se esperaban 2 separadores Farabeuf conforme al kit y solo se detecto 1 en la charola.'
ENGLISH_STORED = 'Manual report shortfall: Farabeuf expected 2, reported 1.'
FREE_TEXT = 'Pinza encontrada debajo de la mesa; revisar con enfermería.'
NOTES = 'Instrumento encontrado debajo de la charola.'


@pytest.fixture
def in_locale(app):
    def enter(locale):
        return app.test_request_context('/', headers={'Cookie': f'pef_locale={locale}'})
    return enter


def disc(description, expected=2, detected=1):
    return SimpleNamespace(id=uuid.uuid4(), description=description, resolved=False, resolved_at=None,
                           expected_quantity=expected, detected_quantity=detected)


# C1000001: vision rule (origin discrepancy_raised), stored in Spanish.
# C1000005: manual report (origin manual_count), stored in English.
CASES = [
    (SPANISH_STORED, 'discrepancy_raised', 'en',
     'Farabeuf retractor: the kit expects 2, but only 1 was detected on the tray.', 'Detected'),
    (SPANISH_STORED, 'discrepancy_raised', 'es-MX',
     'Separador Farabeuf: el kit contempla 2, pero solo se detectó 1 en la charola.', 'Detectado'),
    (ENGLISH_STORED, 'manual_count', 'en',
     'Farabeuf retractor: the kit expects 2, but only 1 was reported.', 'Reported'),
    (ENGLISH_STORED, 'manual_count', 'es-MX',
     'Separador Farabeuf: el kit contempla 2, pero solo se reportó 1.', 'Reportado'),
]


@pytest.mark.parametrize('stored, origin, locale, expected_text, quantity_label', CASES)
def test_known_automatic_discrepancy_is_rebuilt(in_locale, stored, origin, locale, expected_text,
                                                 quantity_label):
    record = disc(stored)
    before = copy.deepcopy(vars(record))
    with in_locale(locale):
        row = view_data._discrepancy_view(record, FARABEUF, 'shortage', origin)
        from flask_babel import gettext
        assert row['display_description'] == expected_text
        assert gettext(row['quantity_label']) == quantity_label
        assert row['family_name'] == ('Farabeuf retractor' if locale == 'en' else 'Separador Farabeuf')
        assert row['description'] == stored          # stored value passed through untouched
        assert (row['expected_quantity'], row['detected_quantity']) == (2, 1)
    assert vars(record) == before                    # nothing was modified


def test_plurals_and_quantities(in_locale):
    kwargs = dict(reason_code='shortage', source='detected', instrument='Kelly forceps', stored_description='x')
    with in_locale('en'):
        assert localize_discrepancy_description(expected=6, actual=3, **kwargs) == \
            'Kelly forceps: the kit expects 6, but only 3 were detected on the tray.'
        assert localize_discrepancy_description(expected=1, actual=0, **kwargs) == \
            'Kelly forceps: the kit expects 1, but only 0 were detected on the tray.'
    with in_locale('es-MX'):
        assert localize_discrepancy_description(expected=6, actual=3, **kwargs) == \
            'Kelly forceps: el kit contempla 6, pero solo se detectaron 3 en la charola.'


def test_surplus_and_neutral_source(in_locale):
    with in_locale('en'):
        assert localize_discrepancy_description(
            reason_code='surplus', source='reported', instrument='Farabeuf retractor',
            expected=2, actual=3, stored_description='Extra on Farabeuf: expected 2, reported 3.',
        ) == 'Farabeuf retractor: the kit expects 2, but 3 were reported.'
        # Known reason, origin that cannot tell detected from reported -> neutral wording.
        assert localize_discrepancy_description(
            reason_code='shortage', source=None, instrument='Farabeuf retractor',
            expected=2, actual=1, stored_description='whatever',
        ) == 'Farabeuf retractor: the kit expects 2, but 1 was recorded.'
    with in_locale('es-MX'):
        assert localize_discrepancy_description(
            reason_code='surplus', source='reported', instrument='Separador Farabeuf',
            expected=2, actual=3, stored_description='x',
        ) == 'Separador Farabeuf: el kit contempla 2, pero se reportaron 3.'


@pytest.mark.parametrize('locale', ['en', 'es-MX'])
@pytest.mark.parametrize('reason, origin, expected, detected, family', [
    (None, 'manual_count', 2, 1, FARABEUF),            # no reason
    ('misclassified', 'manual_count', 2, 1, FARABEUF), # reason the system never auto-describes
    ('unknown_reason', None, 2, 1, FARABEUF),
    ('shortage', 'manual_count', 2, 3, FARABEUF),      # quantities contradict the reason
    ('surplus', 'discrepancy_raised', 2, 3, FARABEUF), # combination that does not exist
    ('shortage', 'manual_count', None, 1, FARABEUF),   # missing quantity
    ('shortage', 'manual_count', 2, 1, None),          # missing family
])
def test_free_or_unknown_description_is_shown_verbatim(in_locale, locale, reason, origin, expected,
                                                        detected, family):
    record = disc(FREE_TEXT, expected, detected)
    with in_locale(locale):
        row = view_data._discrepancy_view(record, family, reason, origin)
        assert row['display_description'] == FREE_TEXT


def test_stored_text_is_never_used_to_decide(in_locale):
    """Same structured data -> same text, whatever language the stored description is in."""
    with in_locale('en'):
        a = view_data._discrepancy_view(disc(SPANISH_STORED), FARABEUF, 'shortage', 'manual_count')
        b = view_data._discrepancy_view(disc(ENGLISH_STORED), FARABEUF, 'shortage', 'manual_count')
        c = view_data._discrepancy_view(disc(FREE_TEXT), FARABEUF, 'shortage', 'manual_count')
        assert a['display_description'] == b['display_description'] == c['display_description']


def _review_detail(session_id):
    with_free = view_data._discrepancy_view(disc(FREE_TEXT), None, 'operator_error', None)
    return {
        'id': session_id, 'session_id': 'WS-C1000005', 'procedure_name': 'x', 'status_code': 'correction_required',
        'status_label': 'Correction Required', 'status_variant': 'danger', 'discrepancy_count': 2,
        'discrepancies': [view_data._discrepancy_view(disc(ENGLISH_STORED), FARABEUF, 'shortage', 'manual_count'),
                          with_free],
    }


def test_review_page_and_resolution_notes(client, login, monkeypatch):
    session_id = 'c1000005-0000-4000-8000-000000000001'
    import services.rf_session
    monkeypatch.setattr(routes, 'supervisor_session_detail', _review_detail)

    login(role='spd_supervisor', locale='es-MX')
    page = client.get(f'/supervisor/discrepancies/{session_id}/review').get_data(as_text=True)
    assert 'Separador Farabeuf: el kit contempla 2, pero solo se reportó 1.' in page
    assert 'Manual report shortfall' not in page
    assert FREE_TEXT in page                         # free text untouched
    login(role='spd_supervisor', locale='en')
    page = client.get(f'/supervisor/discrepancies/{session_id}/review').get_data(as_text=True)
    assert 'Farabeuf retractor: the kit expects 2, but only 1 was reported.' in page
    assert FREE_TEXT in page

    # Resolution notes reach the service exactly as typed and never go through gettext.
    received, translated = [], []
    monkeypatch.setattr(services.rf_session, 'resolve_discrepancy', lambda **kw: received.append(kw['notes']))
    import flask_babel
    real_gettext = flask_babel.gettext
    monkeypatch.setattr(flask_babel, 'gettext', lambda s, **kw: translated.append(s) or real_gettext(s, **kw))
    monkeypatch.setattr(i18n.flask_babel, 'gettext', flask_babel.gettext)
    for locale in ('en', 'es-MX'):
        login(role='spd_supervisor', locale=locale)
        client.post(f'/supervisor/discrepancies/{session_id}/review', data={
            'discrepancy_id': str(uuid.uuid4()), 'resolution_outcome': 'notes_only', 'notes': NOTES})
    assert received == [NOTES, NOTES]
    assert NOTES not in translated


def test_v3_review_shows_notes_verbatim(client, login):
    login(role='spd_supervisor', locale='es-MX')
    page = client.get('/supervisor/discrepancies/WS-026/review?tab=resolution&case=mayo-hegar').get_data(as_text=True)
    assert 'Physical recount confirmed only one Mayo-Hegar Needle Holder present.' in page
