"""Presentation localization of system-controlled DB values (i18n.localize_*)."""
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from controllers import rf_ui, view_data
from i18n import localize_db_label, localize_db_text, localize_known_text

KIT_ID = uuid.UUID('51515151-0000-4000-8000-000000000001')
STATION_ID = uuid.UUID('13131313-0000-4000-8000-000000000001')


@pytest.fixture
def in_locale(app):
    def enter(locale):
        return app.test_request_context('/', headers={'Cookie': f'pef_locale={locale}'})
    return enter


@pytest.mark.parametrize('locale, expected', [
    ('en', 'Laparoscopic cholecystectomy'),
    ('es-MX', 'Colecistectomía laparoscópica'),
])
def test_procedure_by_code(in_locale, locale, expected):
    with in_locale(locale):
        assert localize_db_label('procedure', 'lap_chole', 'Colecistectomia laparoscopica') == expected


@pytest.mark.parametrize('entity, key, stored, english, spanish', [
    ('kit', KIT_ID, 'Kit laparoscopico basico', 'Basic laparoscopic kit', 'Kit laparoscópico básico'),
    ('kit', KIT_ID, 'Video Demo Kit 1', 'Video Demo Kit 1', 'Kit demo de video 1'),
    ('capture_station', STATION_ID, 'Estacion cenital OR-01',
     'Overhead Capture Station OR-01', 'Estación cenital OR-01'),
    ('operating_room', 'OR-01', 'Quirofano 1', 'Operating Room 1', 'Quirófano 1'),
    ('instrument_family', 'MAYO_CURVED', 'Curved Mayo Scissor', 'Curved Mayo Scissor', 'Tijera Mayo curva'),
    ('instrument_family', 'KELLY', 'Pinza Kelly', 'Kelly forceps', 'Pinza Kelly'),
    ('role', 'station_operator', 'Operador de estacion. Ejecuta sesiones de conteo asignadas.',
     'Station operator. Runs assigned counting sessions.',
     'Operador de estación. Ejecuta sesiones de conteo asignadas.'),
    ('instrument_family.function', 'KELLY',
     'Ocluir vasos sanguineos de calibre pequeno y mediano durante la diseccion.',
     'Occlude small and medium vessels during dissection.',
     'Ocluir vasos sanguíneos de calibre pequeño y mediano durante la disección.'),
])
def test_known_records(in_locale, entity, key, stored, english, spanish):
    with in_locale('en'):
        assert localize_db_text(entity, key, stored) == english
    with in_locale('es-MX'):
        assert localize_db_text(entity, key, stored) == spanish


@pytest.mark.parametrize('entity, code, stored, english, spanish', [
    ('surgical_role', 'surgeon', 'Cirujano', 'Surgeon', 'Cirujano'),
    ('operation_phase', 'intraop', 'Transoperatorio', 'Intraoperative', 'Transoperatorio'),
    ('operation_phase', 'pre_incision', 'Conteo inicial previo a incision',
     'Initial count before incision', 'Conteo inicial previo a incisión'),
    ('event_type', 'phase_change', 'Cambio de fase quirurgica', 'Surgical phase change', 'Cambio de fase quirúrgica'),
    ('instrument_category', 'grasping', 'Prension', 'Grasping', 'Prensión'),
    ('cycle_status', 'available', 'Disponible', 'Available', 'Disponible'),
    ('discrepancy_reason', 'shortage', 'Faltante respecto al inventario esperado',
     'Shortage against expected inventory', 'Faltante respecto al inventario esperado'),
])
def test_catalogs_by_code(in_locale, entity, code, stored, english, spanish):
    with in_locale('en'):
        assert localize_db_label(entity, code, stored) == english
    with in_locale('es-MX'):
        assert localize_db_label(entity, code, stored) == spanish


def test_technique_label(in_locale):
    with in_locale('en'):
        assert localize_known_text('technique_label', 'Laparoscopica') == 'Laparoscopic'
        assert localize_known_text('technique_label', 'Robotic, surgeon choice') == 'Robotic, surgeon choice'
    with in_locale('es-MX'):
        assert localize_known_text('technique_label', 'Compact demo') == 'Demo compacta'


@pytest.mark.parametrize('locale', ['en', 'es-MX'])
def test_unknown_values_are_returned_exactly(in_locale, locale):
    with in_locale(locale):
        # unknown catalog code / entity
        assert localize_db_label('procedure', 'robotic_x', 'Cirugía robótica X') == 'Cirugía robótica X'
        assert localize_db_label('no_such_entity', 'lap_chole', 'raw') == 'raw'
        assert localize_db_label('procedure', None, None) is None
        # known key whose stored text was edited by an administrator: not translated
        assert localize_db_text('kit', KIT_ID, 'Kit laparoscopico basico v2') == 'Kit laparoscopico basico v2'
        assert localize_db_text('instrument_family', 'KELLY', 'Pinza Kelly (editada)') == 'Pinza Kelly (editada)'
        assert localize_db_text('instrument_family.function', 'KELLY', 'Texto libre') == 'Texto libre'
        # unknown record, free text, empty
        assert localize_db_text('capture_station', uuid.uuid4(), 'Estación sótano') == 'Estación sótano'
        assert localize_db_text('kit', KIT_ID, '') == ''
        # a msgid that is NOT a known DB text of that entity is not translated by accident
        assert localize_db_text('kit', KIT_ID, 'Cancel') == 'Cancel'


def test_known_text_with_other_uuid_uses_alias(in_locale):
    """Older demo DBs: same system name under another id still localizes."""
    with in_locale('en'):
        assert localize_db_text('kit', uuid.uuid4(), 'Kit laparoscopico basico') == 'Basic laparoscopic kit'


# --- Session rows: the Assigned Sessions bug ---------------------------------------------

def _session_row(for_role='operator'):
    procedure = SimpleNamespace(id=uuid.uuid4(), code='lap_chole', name='Colecistectomia laparoscopica')
    kit = SimpleNamespace(id=KIT_ID, name='Kit laparoscopico basico')
    station = SimpleNamespace(id=STATION_ID, name='Estacion cenital OR-01')
    room = SimpleNamespace(id=uuid.uuid4(), code='OR-01', name='Quirofano 1')
    operation = SimpleNamespace(id=uuid.uuid4(), procedure_type_id=procedure.id, room_id=room.id,
                                scheduled_at=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc))
    status = SimpleNamespace(id=uuid.uuid4(), code='scheduled', name='Programada')
    item = SimpleNamespace(id=uuid.uuid4(), operation_id=operation.id, status_id=status.id, kit_id=kit.id,
                           station_id=station.id, user_id=uuid.uuid4(), capture_mode=None,
                           started_at=None, ended_at=None, updated_at=None)
    return view_data._build_session_row(
        item, status_by_id={status.id: status}, operations={operation.id: operation},
        users={item.user_id: 'Operador Demo'}, kits={kit.id: kit}, stations={station.id: station},
        procedures={procedure.id: procedure}, rooms={room.id: room},
        open_discrepancy_counts={}, agreement_session_ids=set(), for_role=for_role,
    )


def test_session_row_names_follow_ui_language(in_locale):
    with in_locale('en'):
        row = _session_row()
        assert row['procedure_name'] == 'Laparoscopic cholecystectomy'
        assert row['kit_name'] == 'Basic laparoscopic kit'
        assert row['capture_station_name'] == 'Overhead Capture Station OR-01'
        assert row['operating_room'] == 'OR-01'
        assert row['operator_name'] == 'Operador Demo'  # people are never translated
        assert row['kit_id'] == str(KIT_ID) and row['station_id'] == str(STATION_ID)
        # Search finds the shown name, the stored name and the code.
        for term in ('laparoscopic', 'colecistectomia', 'lap_chole', 'basic laparoscopic', 'cenital'):
            assert term in row['search_text']
    with in_locale('es-MX'):
        row = _session_row()
        assert row['procedure_name'] == 'Colecistectomía laparoscópica'
        assert row['kit_name'] == 'Kit laparoscópico básico'
        assert row['capture_station_name'] == 'Estación cenital OR-01'


def test_history_filters_use_ids_across_languages(in_locale):
    with in_locale('en'):
        rows = [_session_row()]
        options = rf_ui.history_filter_options(rows)
        assert options['kits'] == [{'value': str(KIT_ID), 'label': 'Basic laparoscopic kit'}]
        kit_filter = options['kits'][0]['value']
    with in_locale('es-MX'):
        rows = [_session_row()]
        # The same ?kit=<id> chosen in English still matches after switching to Spanish.
        assert rf_ui.apply_history_filters(rows, {'kit': kit_filter}) == rows
        assert rf_ui.apply_history_filters(rows, {'station': str(STATION_ID)}) == rows
        assert rf_ui.apply_history_filters(rows, {'search': 'laparoscopic'}) == rows
        assert rf_ui.history_filter_options(rows)['kits'][0]['label'] == 'Kit laparoscópico básico'


def test_operator_list_route_shows_localized_names(client, login, monkeypatch, in_locale):
    from controllers import routes

    def rows(_user_id):
        return [_session_row()]
    monkeypatch.setattr(routes, 'operator_my_sessions', rows)
    login(locale='en')
    page = client.get('/operator/sessions').get_data(as_text=True)
    assert 'Laparoscopic cholecystectomy' in page and 'Basic laparoscopic kit' in page
    assert 'Colecistectomia' not in page and 'Kit laparoscopico basico' not in page
    assert 'Laparoscopic cholecystectomy' in client.get('/operator/sessions?search=colecistectomia').get_data(as_text=True)
    login(locale='es-MX')
    page = client.get('/operator/sessions').get_data(as_text=True)
    assert 'Colecistectomía laparoscópica' in page and 'Kit laparoscópico básico' in page
    assert 'Colecistectomía laparoscópica' in client.get('/operator/sessions?search=laparoscopic').get_data(as_text=True)


def test_active_capture_localizes_names_but_keeps_worker_keys(client, login, monkeypatch):
    """Active Capture: shown names follow the UI; the worker summary key stays the stored name."""
    import services.rf_session
    import services.vision_bridge
    from controllers import routes

    def detail(_session_id, _user_id):
        row = _session_row()
        row.update({'status_code': 'in_progress', 'capture_mode': 'vision', 'expected_items': [{
            'family_id': 'f1', 'family_code': 'KELLY', 'family_name': view_data._family_label(
                SimpleNamespace(code='KELLY', name='Pinza Kelly')),
            'expected_quantity': 6, 'category_code': 'hemostasis', 'category_name': 'Hemostasia',
            'category_label': localize_db_label('instrument_category', 'hemostasis', 'Hemostasia'),
        }]})
        return row
    monkeypatch.setattr(routes, 'operator_session_detail', detail)
    monkeypatch.setattr(services.vision_bridge, 'worker_health', lambda: {'ok': False})
    monkeypatch.setattr(services.rf_session, 'schedule_form_options', lambda _i: {'phases': [
        {'code': 'intraop', 'name': localize_db_label('operation_phase', 'intraop', 'Transoperatorio')}]})
    path = '/operator/sessions/33333333-3333-3333-3333-333333333333/capture'
    login(locale='en')
    page = client.get(path).get_data(as_text=True)
    assert 'Kelly forceps' in page
    assert re.search(r'capture-cat-header"[^>]*>\s*<td[^>]*>\s*Hemostasis', page)
    assert 'Intraoperative' in page
    assert 'data-category-name="Hemostasia"' in page
    login(locale='es-MX')
    page = client.get(path).get_data(as_text=True)
    assert 'Pinza Kelly' in page and 'Transoperatorio' in page
    assert 'data-category-name="Hemostasia"' in page
