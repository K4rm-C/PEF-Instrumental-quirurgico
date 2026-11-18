"""RF-AD-10 application parameters, persisted per institution in `app_setting` (migration 010).

A key without a row uses its documented default (RF defaults: privacy notice 10 MB pdf/html,
family example 5 MB image, password 8+ characters). Each administrator reads and edits only the
parameters of their own institution; every changed key is audited (UPDATE_SETTING). Readers in
other RF modules call `value(institution_id, key)`.
"""
from dataclasses import dataclass, field

from sqlalchemy import select

from i18n import N_

from . import audit
from .common import utc_now
from .database import db
from .errors import bad_request
from .models import AppSetting


@dataclass(frozen=True)
class Setting:
    kind: str                 # int | bool | mime_list | text
    default: object
    group: str
    label: str
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple = field(default_factory=tuple)
    max_length: int | None = None


IMAGE_MIME = ('image/jpeg', 'image/png', 'image/webp')
NOTICE_MIME = ('application/pdf', 'text/html')
# The only weights format the vision worker loads (RF-AD-08 "extensiones"); shown, not editable.
MODEL_EXTENSIONS = ('.pt',)

SETTINGS = {
    # KPI colour thresholds of the dashboard (RF-AD-01): warning from / danger from (session counts).
    'kpi_or_hold_warning': Setting('int', 1, 'kpi', N_('OR hold sessions: warning from'), 0, 10000),
    'kpi_or_hold_danger': Setting('int', 3, 'kpi', N_('OR hold sessions: danger from'), 0, 10000),
    'kpi_correction_required_warning': Setting('int', 1, 'kpi', N_('Released to SPD review: warning from'), 0, 10000),
    'kpi_correction_required_danger': Setting('int', 5, 'kpi', N_('Released to SPD review: danger from'), 0, 10000),
    'kpi_awaiting_review_warning': Setting('int', 5, 'kpi', N_('Awaiting SPD review: warning from'), 0, 10000),
    'kpi_awaiting_review_danger': Setting('int', 10, 'kpi', N_('Awaiting SPD review: danger from'), 0, 10000),
    # RF-AD-05 password policy.
    'password_min_length': Setting('int', 8, 'security', N_('Minimum password length'), 8, 128),
    # RF-AD-02 family example photos.
    'family_example_max_mb': Setting('int', 5, 'uploads', N_('Family example image: maximum size (MB)'), 1, 20),
    'family_example_allowed_mime': Setting('mime_list', list(IMAGE_MIME[:2]), 'uploads',
                                           N_('Family example image: allowed types'), choices=IMAGE_MIME),
    # RF-AD-04 / RF-SP-02 privacy notices.
    'privacy_notice_max_mb': Setting('int', 10, 'uploads', N_('Privacy notice: maximum size (MB)'), 1, 50),
    'privacy_notice_allowed_mime': Setting('mime_list', list(NOTICE_MIME), 'uploads', N_('Privacy notice: allowed types'),
                                           choices=NOTICE_MIME),
    'privacy_notice_single_active': Setting('bool', False, 'uploads', N_('Privacy notice: only one active version')),
    # RF-AD-08 vision model weights.
    'model_weights_max_mb': Setting('int', 500, 'uploads', N_('Vision model weights: maximum size (MB)'), 1, 4096),
    'missing_families_overlay_text': Setting(
        'text', 'These active instrument families are not detected by this model. Sessions that expect them will '
                'need manual counting for them.', 'uploads', N_('Vision model: missing families warning text'),
        max_length=500),
}


def _validate(key, raw):
    spec = SETTINGS[key]
    if spec.kind == 'int':
        try:
            number = int(str(raw).strip())
        except (TypeError, ValueError):
            raise bad_request('invalid_setting', f'{spec.label} must be a whole number.', key)
        if number < spec.minimum or number > spec.maximum:
            raise bad_request('invalid_setting', f'{spec.label} must be between {spec.minimum} and {spec.maximum}.', key)
        return number
    if spec.kind == 'bool':
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {'1', 'true', 'on', 'yes'}
    if spec.kind == 'mime_list':
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        chosen = [value for value in dict.fromkeys(str(v).strip() for v in values) if value]
        if not chosen or any(value not in spec.choices for value in chosen):
            raise bad_request('invalid_setting', f'{spec.label}: choose at least one allowed type.', key)
        return chosen
    value = str(raw or '').strip()
    if not value or len(value) > spec.max_length:
        raise bad_request('invalid_setting', f'{spec.label} is required (at most {spec.max_length} characters).', key)
    return value


def values(institution_id):
    stored = {row.key: row.value for row in db.execute(
        select(AppSetting).where(AppSetting.institution_id == institution_id)).scalars()}
    return {key: stored.get(key, spec.default) for key, spec in SETTINGS.items()}


def value(institution_id, key):
    row = db.get(AppSetting, (institution_id, key))
    return row.value if row is not None else SETTINGS[key].default


def describe(institution_id):
    current = values(institution_id)
    return {
        'items': [{'key': key, 'kind': spec.kind, 'group': spec.group, 'label': spec.label, 'value': current[key],
                   'default': spec.default, 'minimum': spec.minimum, 'maximum': spec.maximum,
                   'choices': list(spec.choices)} for key, spec in SETTINGS.items()],
        'model_extensions': list(MODEL_EXTENSIONS),
    }


def update(actor, payload):
    """Validate every submitted key first, then save the changed ones in one transaction."""
    unknown = [key for key in payload if key not in SETTINGS]
    if unknown:
        raise bad_request('invalid_setting', f'Unknown parameter: {unknown[0]}.', unknown[0])
    clean = {key: _validate(key, raw) for key, raw in payload.items()}
    merged = {**values(actor.institution_id), **clean}
    for prefix in ('kpi_or_hold', 'kpi_correction_required', 'kpi_awaiting_review'):
        if merged[f'{prefix}_warning'] > merged[f'{prefix}_danger']:
            raise bad_request('invalid_setting', 'A warning threshold cannot be higher than its danger threshold.',
                              f'{prefix}_warning')
    current = values(actor.institution_id)
    now = utc_now()
    for key, new in clean.items():
        if new == current[key]:
            continue
        row = db.get(AppSetting, (actor.institution_id, key))
        if row is None:
            db.add(AppSetting(institution_id=actor.institution_id, key=key, value=new, updated_at=now,
                              updated_by_user_id=actor.id))
        else:
            row.value, row.updated_at, row.updated_by_user_id = new, now, actor.id
        audit.record('UPDATE_SETTING', 'app_setting', actor.institution_id)
    db.commit()
    return describe(actor.institution_id)
