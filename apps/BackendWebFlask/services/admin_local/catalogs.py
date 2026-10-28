from sqlalchemy import func, select

from .database import db
from .models import (
    CatInstrumentCategory,
    CatInstrumentCycleStatus,
    CatOperationPhase,
    CatProcedureType,
    Institution,
    Instrument,
    InstrumentFamily,
    Kit,
    OperatingRoom,
)


# Display order of instrument categories by surgical purpose (same as the web portal).
CATEGORY_PURPOSE_ORDER = (
    'cutting', 'dissection', 'grasping', 'hemostasis', 'retraction', 'suction', 'suturing',
)


def category_rank(code):
    try:
        return CATEGORY_PURPOSE_ORDER.index(code)
    except ValueError:
        return len(CATEGORY_PURPOSE_ORDER)


def _categories():
    rows = db.execute(select(CatInstrumentCategory)).scalars().all()
    rows.sort(key=lambda row: (category_rank(row.code), row.name))
    return [{'id': str(row.id), 'code': row.code, 'name': row.name,
             'rank': category_rank(row.code)} for row in rows]


def _cycle_statuses():
    rows = db.execute(select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)).scalars()
    return [{'id': str(row.id), 'code': row.code, 'name': row.name} for row in rows]


def _phases():
    rows = db.execute(select(CatOperationPhase).order_by(CatOperationPhase.name)).scalars()
    return [{'id': str(row.id), 'code': row.code, 'name': row.name, 'active': row.active} for row in rows]


def _procedure_types():
    rows = db.execute(select(CatProcedureType).order_by(CatProcedureType.name)).scalars()
    return [{'id': str(row.id), 'code': row.code, 'name': row.name} for row in rows]


def _institutions():
    rows = db.execute(select(Institution).order_by(Institution.name)).scalars()
    return [{'id': str(row.id), 'name': row.name, 'active': row.active} for row in rows]


def available_counts():
    """Active instruments currently in the `available` cycle status, per family."""
    available = db.scalar(
        select(CatInstrumentCycleStatus.id).where(CatInstrumentCycleStatus.code == 'available')
    )
    if available is None:
        return {}
    rows = db.execute(
        select(Instrument.family_id, func.count(Instrument.id))
        .where(Instrument.active.is_(True), Instrument.cycle_status_id == available)
        .group_by(Instrument.family_id)
    ).all()
    return {family_id: count for family_id, count in rows}


def _families(active_only=False):
    categories = {row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()}
    counts = available_counts()
    query = select(InstrumentFamily).order_by(InstrumentFamily.name)
    if active_only:
        query = query.where(InstrumentFamily.active.is_(True))
    values = []
    for row in db.execute(query).scalars():
        category = categories.get(row.category_id)
        values.append({
            'id': str(row.id),
            'code': row.code,
            'name': row.name,
            'active': row.active,
            'category': {'id': str(category.id), 'code': category.code, 'name': category.name,
                         'rank': category_rank(category.code)} if category else None,
            'available_count': counts.get(row.id, 0),
        })
    return values


def _kits(actor, active_only=True):
    query = select(Kit).where(Kit.institution_id == actor.institution_id).order_by(Kit.name, Kit.version)
    if active_only:
        query = query.where(Kit.active.is_(True))
    return [{'id': str(row.id), 'name': row.name, 'version': row.version, 'active': row.active}
            for row in db.execute(query).scalars()]


def _operating_rooms(actor):
    rows = db.execute(
        select(OperatingRoom).where(OperatingRoom.institution_id == actor.institution_id)
        .order_by(OperatingRoom.code)
    ).scalars()
    return [{'id': str(row.id), 'code': row.code, 'name': row.name, 'active': row.active} for row in rows]


BUILDERS = {
    'categories': lambda actor: _categories(),
    'cycle_statuses': lambda actor: _cycle_statuses(),
    'phases': lambda actor: _phases(),
    'procedure_types': lambda actor: _procedure_types(),
    'institutions': lambda actor: _institutions(),
    # 'roles' comes from AuthService (facade -> identity_remote.catalog_roles).
    'families': lambda actor: _families(),
    'active_families': lambda actor: _families(active_only=True),
    'kits': lambda actor: _kits(actor),
    'operating_rooms': _operating_rooms,
}


def catalogs(actor, names):
    wanted = [name for name in names if name in BUILDERS] or list(BUILDERS)
    return {name: BUILDERS[name](actor) for name in wanted}
