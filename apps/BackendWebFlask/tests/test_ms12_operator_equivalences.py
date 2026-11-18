"""MS-12: the Operator's validation screen shows the equivalences the SPD authorized for the session's kit, as
information only (it never changes expected quantities or the count). Real PostgreSQL (rolled back)."""
import uuid

from sqlalchemy import text

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401  (pytest fixture)
from .test_single_video_flow import _finish_run, _post, _register_initial, flow, frame, worker  # noqa: F401


def _equivalence(flow, *, active=True):
    tray = flow.tray
    kit_id = tray.c.execute(text('SELECT kit_id FROM work_session WHERE id = :s'), {'s': tray.session.id}).scalar_one()
    category = tray.c.execute(text('SELECT category_id FROM instrument_family WHERE id = :f'), {'f': tray.a}).scalar_one()
    equivalent = uuid.uuid4()
    tray.c.execute(text("INSERT INTO instrument_family (id, code, name, category_id, active) VALUES (:id, :c, 'Crile forceps', :cat, TRUE)"),
                   {'id': equivalent, 'c': f'CRILE_{uuid.uuid4().hex[:6]}', 'cat': category})
    user = tray.c.execute(text('SELECT id FROM "user" WHERE institution_id = :i LIMIT 1'), {'i': tray.t.institution_id}).scalar_one()
    tray.c.execute(text("""INSERT INTO kit_item_equivalence (institution_id, kit_id, family_id, equivalent_family_id, notes,
                           active, authorized_by_user_id, revoked_at)
                           VALUES (:i, :k, :f, :q, 'Same pattern', :a, :u, CASE WHEN :a THEN NULL ELSE now() END)"""),
                   {'i': tray.t.institution_id, 'k': kit_id, 'f': tray.a, 'q': equivalent, 'a': active, 'u': user})


def _validation_page(flow):
    assert _register_initial(flow).status_code == 200
    _finish_run(flow)
    assert _post(flow, '/capture/final').status_code == 200
    return flow.client.get(flow.base + '/vision-validation').get_data(as_text=True)


def test_active_equivalences_are_shown_as_information(app, flow):
    _equivalence(flow)
    page = _validation_page(flow)
    assert 'data-kit-equivalences' in page and 'Crile forceps' in page and 'Same pattern' in page


def test_revoked_equivalences_are_not_shown(app, flow):
    _equivalence(flow, active=False)
    assert 'data-kit-equivalences' not in _validation_page(flow)
