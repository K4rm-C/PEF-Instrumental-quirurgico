"""RF-AD-03 Procedures (cat_procedure_type) and their counting phases (procedure_phase).

D-4: kit links of a procedure (procedure_kit) belong to the SPD supervisor (glossary of
rf_admin_app.md: "Kit (receta) = kit + kit_item + procedure_kit, SPD, no Admin"). Administration
shows them read-only and never writes them: a payload with `kits` is rejected, and saving a
procedure keeps its existing links untouched. No DELETE (operations reference procedures).
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit
from .database import db
from .errors import bad_request, conflict, not_found
from .models import CatOperationPhase, CatProcedureType, Kit, ProcedureKit, ProcedurePhase
from .common import integer, matches_search, paginate, parse_uuid, text, utc_now


ENTITY = 'PROCEDURE'
RESOURCE = 'procedure'


def list_procedures(actor, *, search='', page=1, per_page=50, matches=()):
    kits = _own_kits(actor)
    phases = {row.id: row for row in db.execute(select(CatOperationPhase)).scalars()}
    kit_links, phase_links = {}, {}
    for link in db.execute(select(ProcedureKit).where(ProcedureKit.kit_id.in_(list(kits) or [None]))).scalars():
        kit_links.setdefault(link.procedure_type_id, []).append(link)
    for link in db.execute(select(ProcedurePhase).order_by(ProcedurePhase.sort_order)).scalars():
        phase_links.setdefault(link.procedure_type_id, []).append(link)

    rows = []
    for procedure in db.execute(select(CatProcedureType).order_by(CatProcedureType.code)).scalars():
        if not matches_search(search, procedure.code, procedure.name, matches=matches,
                              keys=(procedure.code,)):
            continue
        links = kit_links.get(procedure.id, [])
        default = next((link for link in links if link.is_default and link.active), None)
        default_kit = kits.get(default.kit_id) if default else None
        rows.append({
            'id': str(procedure.id),
            'code': procedure.code,
            'name': procedure.name,
            'kit_count': len(links),
            'default_kit': {'id': str(default_kit.id), 'name': default_kit.name,
                            'version': default_kit.version} if default_kit else None,
            'phases': [{'id': str(link.phase_id), 'code': phases[link.phase_id].code,
                        'name': phases[link.phase_id].name}
                       for link in phase_links.get(procedure.id, []) if link.phase_id in phases],
        })
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or ''}
    return result


def _get(procedure_id):
    procedure = db.get(CatProcedureType, parse_uuid(procedure_id))
    if procedure is None:
        raise not_found('procedure_not_found', 'Procedure not found.')
    return procedure


def _own_kits(actor):
    """D-2: kits (and so procedure_kit links) are shown only for the administrator's institution."""
    return {row.id: row for row in db.execute(select(Kit).where(Kit.institution_id == actor.institution_id)).scalars()}


def get_procedure(actor, procedure_id):
    procedure = _get(procedure_id)
    kits = _own_kits(actor)
    phases = {row.id: row for row in db.execute(select(CatOperationPhase)).scalars()}
    kit_rows = db.execute(select(ProcedureKit).where(ProcedureKit.procedure_type_id == procedure.id,
                                                     ProcedureKit.kit_id.in_(list(kits) or [None]))).scalars()
    phase_rows = db.execute(
        select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id)
        .order_by(ProcedurePhase.sort_order)
    ).scalars()
    return {
        'id': str(procedure.id),
        'code': procedure.code,
        'name': procedure.name,
        'kits': [{
            'kit': ({'id': str(kits[link.kit_id].id), 'name': kits[link.kit_id].name,
                     'version': kits[link.kit_id].version, 'active': kits[link.kit_id].active}
                    if link.kit_id in kits else {'id': str(link.kit_id), 'name': None}),
            'technique_label': link.technique_label or '',
            'is_default': link.is_default,
            'active': link.active,
        } for link in kit_rows],
        'phases': [{
            'phase': ({'id': str(link.phase_id), 'code': phases[link.phase_id].code,
                       'name': phases[link.phase_id].name}
                      if link.phase_id in phases else {'id': str(link.phase_id), 'code': None, 'name': None}),
            'sort_order': link.sort_order,
            'is_count_required': link.is_count_required,
            'active': link.active,
        } for link in phase_rows],
    }


def _values(actor, payload):
    code = text(payload, 'code', required=True, max_length=64, label='Procedure code')
    name = text(payload, 'name', required=True, max_length=200, label='Procedure name')
    if payload.get('kits'):
        raise bad_request('procedure_kits_owned_by_spd',
                          'Kit links of a procedure are maintained by the SPD supervisor.', 'kits')

    phases = []
    for raw in payload.get('phases') or []:
        phase_id = parse_uuid((raw or {}).get('phase_id'), 'invalid_phase',
                              'Choose a valid phase for every counting phase row.', 'phases')
        order = integer((raw or {}).get('sort_order'), name='sort_order', minimum=1, maximum=32767,
                        message='Phase order must be between 1 and 32767.')
        phases.append({'phase_id': phase_id, 'sort_order': order,
                       'is_count_required': bool(raw.get('is_count_required', True)),
                       'active': bool(raw.get('active', True))})
    if len({item['phase_id'] for item in phases}) != len(phases):
        raise bad_request('duplicate_phase', 'A phase can only be added once.', 'phases')
    if len({item['sort_order'] for item in phases}) != len(phases):
        raise bad_request('duplicate_phase_order', 'Each counting phase must have a unique order.', 'phases')
    if phases:
        found = set(db.execute(
            select(CatOperationPhase.id).where(CatOperationPhase.id.in_([item['phase_id'] for item in phases]))
        ).scalars())
        if found != {item['phase_id'] for item in phases}:
            raise bad_request('invalid_catalog_rows', 'Choose kits and phases from the available catalogs.', 'phases')
    return code, name, phases


def _sync(procedure, phases):
    """Replace the counting phases only; procedure_kit rows (SPD) are never touched."""
    for link in db.execute(select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id)).scalars():
        db.delete(link)
    db.flush()
    for item in phases:
        db.add(ProcedurePhase(procedure_type_id=procedure.id, **item))


def create_procedure(actor, payload):
    code, name, phases = _values(actor, payload)
    procedure = CatProcedureType(code=code, name=name)
    db.add(procedure)
    try:
        db.flush()
        _sync(procedure, phases)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('procedure_code_exists', 'A procedure with this code already exists.', 'code')
    audit.record(f'CREATE_{ENTITY}', RESOURCE, procedure.id)
    db.commit()
    return get_procedure(actor, procedure.id)


def update_procedure(actor, procedure_id, payload):
    procedure = _get(procedure_id)
    code, name, phases = _values(actor, payload)
    procedure.code = code
    procedure.name = name
    try:
        db.flush()
        _sync(procedure, phases)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('procedure_code_exists', 'A procedure with this code already exists.', 'code')
    audit.record(f'UPDATE_{ENTITY}', RESOURCE, procedure.id)
    db.commit()
    return get_procedure(actor, procedure.id)
