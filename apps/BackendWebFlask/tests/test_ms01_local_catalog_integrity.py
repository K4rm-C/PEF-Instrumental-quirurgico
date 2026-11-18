"""MS-01 / P4 on the portal's LOCAL SPD path (services/admin_local/instruments.py, kits.py).

That path serves the SPD supervisor's pieces and kits only when CATALOG_SERVICE_URL is not configured
(development fallback); with it, CatalogService answers (and applies the same rule). Same behaviour in both:
an inactive family cannot be introduced; historical references to a later-deactivated family keep working.
"""
import uuid

import pytest

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, pg, tenants  # noqa: F401


@pytest.fixture
def catalog(tenants, monkeypatch):
    monkeypatch.delenv('CATALOG_SERVICE_URL', raising=False)
    tag = tenants.tag
    category = uuid.uuid4()
    tenants.q("INSERT INTO cat_instrument_category (id, code, name) VALUES (:i, :c, 'MS-01 category')",
              i=category, c=f'zz_ms01_cat_{tag}')
    cycle = f'zz_ms01_cy_{tag}'
    tenants.q("INSERT INTO cat_instrument_cycle_status (id, code, name) VALUES (:i, :c, 'MS-01 status')",
              i=uuid.uuid4(), c=cycle)
    families = {}
    for key, active in (('active', True), ('inactive', False), ('other', True)):
        families[key] = uuid.uuid4()
        tenants.q('INSERT INTO instrument_family (id, code, name, category_id, active) VALUES (:i, :c, :n, :cat, :a)',
                  i=families[key], c=f'ZZMS01{key[:3].upper()}{tag}', n=f'MS-01 {key}', cat=category, a=active)
    return {'cycle': cycle, 'families': families, 'tag': tag}


def _instrument(catalog, family, code=None):
    body = {'family_id': str(catalog['families'][family]), 'cycle_status': catalog['cycle']}
    return {**body, 'internal_code': code} if code else body


def _refused(as_user, method, path, body, reason):
    with as_user('supervisor', 'A', method), pytest.raises(AdminDomainError) as exc:
        facade.call(method, path, json=body)
    assert (exc.value.status, exc.value.code) == (400, reason), (exc.value.status, exc.value.code)


def test_new_instrument_with_inactive_family_is_refused(as_user, tenants, catalog):
    _refused(as_user, 'POST', '/instruments', _instrument(catalog, 'inactive', f'IN-{catalog["tag"]}'), 'inactive_family')
    assert tenants.q('SELECT count(*) FROM instrument WHERE internal_code = :c',
                     c=f'IN-{catalog["tag"]}').scalar_one() == 0


def test_unknown_family_keeps_its_answer(as_user, catalog):
    body = {**_instrument(catalog, 'active', f'UN-{catalog["tag"]}'), 'family_id': str(uuid.uuid4())}
    _refused(as_user, 'POST', '/instruments', body, 'invalid_family_or_cycle')


def test_instrument_family_changes_and_history(as_user, tenants, catalog):
    with as_user('supervisor', 'A', 'POST'):
        created = facade.post('/instruments', _instrument(catalog, 'active', f'OK-{catalog["tag"]}'))
    _refused(as_user, 'PUT', f"/instruments/{created['id']}", _instrument(catalog, 'inactive'), 'inactive_family')
    # The family it already has is deactivated later: editing the piece without changing it keeps working.
    tenants.q('UPDATE instrument_family SET active = FALSE WHERE id = :i', i=catalog['families']['active'])
    with as_user('supervisor', 'A', 'PUT'):
        updated = facade.call('PUT', f"/instruments/{created['id']}", json=_instrument(catalog, 'active'))
    assert updated['id'] == created['id']


def _kit(catalog, families, name=None):
    return {'name': name or f'MS-01 kit {catalog["tag"]}',
            'items': [{'family_id': str(catalog['families'][f]), 'quantity': q} for f, q in families]}


def test_kits_refuse_new_inactive_families_but_keep_history(as_user, tenants, catalog):
    _refused(as_user, 'POST', '/kits', _kit(catalog, [('active', 1), ('inactive', 1)]), 'inactive_family')
    with as_user('supervisor', 'A', 'POST'):
        kit = facade.post('/kits', _kit(catalog, [('active', 2), ('other', 1)]))
    _refused(as_user, 'PUT', f"/kits/{kit['id']}", _kit(catalog, [('active', 2), ('inactive', 1)]), 'inactive_family')
    # A family of the kit is deactivated later: a new version that keeps it is accepted, nothing is rewritten.
    tenants.q('UPDATE instrument_family SET active = FALSE WHERE id = :i', i=catalog['families']['other'])
    with as_user('supervisor', 'A', 'PUT'):
        version = facade.call('PUT', f"/kits/{kit['id']}", json=_kit(catalog, [('active', 3), ('other', 1)]))
    assert version['id'] != kit['id']
    assert tenants.q('SELECT count(*) FROM kit_item WHERE kit_id = :k AND family_id = :f', k=kit['id'],
                     f=catalog['families']['other']).scalar_one() == 1


def test_administration_still_cannot_write_pieces_or_kits(as_user, catalog):
    for path, body in (('/instruments', _instrument(catalog, 'active', f'AD-{catalog["tag"]}')),
                       ('/kits', _kit(catalog, [('active', 1)]))):
        with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
            facade.post(path, body)
        assert exc.value.status == 403
