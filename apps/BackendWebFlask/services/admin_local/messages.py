from flask_babel import gettext

from i18n import N_


# Admin domain error code -> English msgid (translated with gettext when shown).
ERROR_MESSAGES = {
    'admin_service_unavailable': N_('Administration is temporarily unavailable. Try again in a moment.'),
    'forbidden': N_('Your role cannot perform this operation.'),
    'token_required': N_('Your session expired. Sign in again.'),
    'invalid_token': N_('Your session expired. Sign in again.'),
    'token_revoked': N_('Your session expired. Sign in again.'),
    'user_inactive': N_('Your session expired. Sign in again.'),
    'database_unavailable': N_('The database is not available right now. Try again in a moment.'),
    'catalog_unavailable': N_('The catalog service is temporarily unavailable. Try again in a moment.'),
    'audit_unavailable': N_('The audit service is temporarily unavailable. Try again in a moment.'),
    'identity_unavailable': N_('The identity service is temporarily unavailable. Try again in a moment.'),
    'not_found': N_('Record not found.'),
    'family_not_found': N_('Record not found.'),
    'instrument_not_found': N_('Record not found.'),
    'kit_not_found': N_('Record not found.'),
    'procedure_not_found': N_('Record not found.'),
    'user_not_found': N_('Record not found.'),
    'role_not_found': N_('Record not found.'),
    'vision_model_not_found': N_('Record not found.'),
    'room_not_found': N_('Record not found.'),
    'station_not_found': N_('Record not found.'),
    'invalid_status': N_('Choose a valid status.'),
    # Families
    'code_required': N_('Code, name and category are required.'),
    'name_required': N_('Name is required.'),
    'category_required': N_('Code, name and category are required.'),
    'invalid_category': N_('Choose a valid category.'),
    'family_code_exists': N_('That family code already exists.'),
    # Instruments
    'internal_code_required': N_('Internal code is required.'),
    'internal_code_too_long': N_('Internal code must contain at most 64 characters.'),
    'family_required': N_('Instrument family and cycle status are required.'),
    'cycle_status_required': N_('Instrument family and cycle status are required.'),
    'invalid_family_or_cycle': N_('Choose a valid instrument family and cycle status.'),
    'instrument_code_exists': N_('An instrument with this internal code already exists for the institution.'),
    'change_reason_required': N_('A change reason is required when cycle status or active status changes.'),
    # Kits
    'kit_exists': N_('A kit with this name and version already exists for the institution.'),
    'invalid_composition': N_('Choose a valid instrument family and quantity for every row.'),
    'invalid_quantity': N_('Expected quantity must be between 1 and 32767.'),
    'duplicate_family': N_('An instrument family can only appear once in the composition.'),
    'empty_composition': N_('Add at least one instrument family to the kit composition.'),
    'invalid_family': N_('Choose instrument families from the available catalog.'),
    # Procedures
    'procedure_code_exists': N_('A procedure with this code already exists.'),
    'invalid_kit': N_('Choose a valid kit for every associated kit row.'),
    'duplicate_kit': N_('A kit can only be associated once.'),
    'multiple_default_kits': N_('Choose at most one default kit.'),
    'inactive_default_kit': N_('The default kit must be an active association.'),
    'invalid_catalog_rows': N_('Choose kits and phases from the available catalogs.'),
    'invalid_phase': N_('Choose a valid phase for every counting phase row.'),
    'invalid_sort_order': N_('Phase order must be between 1 and 32767.'),
    'duplicate_phase': N_('A phase can only be added once.'),
    'duplicate_phase_order': N_('Each counting phase must have a unique order.'),
    # Users
    'email_required': N_('Name, email, and institution are required.'),
    'institution_required': N_('Name, email, and institution are required.'),
    'invalid_email': N_('Enter a valid email address.'),
    'invalid_institution': N_('Choose a valid institution and at least one role assigned to it.'),
    'institution_inactive': N_('Users cannot be added to an inactive institution.'),
    'roles_required': N_('Assign at least one role.'),
    'invalid_roles': N_('Choose a valid institution and at least one role assigned to it.'),
    'email_exists': N_('A user with this email already exists.'),
    'password_too_short': N_('Password must contain at least 8 characters.'),
    'password_mismatch': N_('Passwords do not match.'),
    'cannot_deactivate_self': N_('You cannot deactivate your own account.'),
    'cannot_remove_own_admin': N_('You cannot remove the administrator role from your own account.'),
    'last_admin': N_('At least one active administrator must remain.'),
    # Roles
    'role_code_exists': N_('A role with this code already exists for the institution.'),
    'invalid_role_code': N_('Role code may only contain lowercase letters, numbers and underscores.'),
    'description_required': N_('Description is required.'),
    'system_role': N_('System roles cannot be deleted.'),
    'role_in_use': N_('Remove this role from every user before deleting it.'),
    # Vision models
    'version_tag_required': N_('Version tag is required.'),
    'version_tag_exists': N_('A vision model with this version tag already exists.'),
    'invalid_checksum': N_('Checksum must be a 64-character SHA-256 hex value.'),
    'invalid_yolo_class_id': N_('YOLO class IDs must be whole numbers from 0.'),
    'duplicate_class_id': N_('Each YOLO class ID can only be mapped once.'),
    'duplicate_class_family': N_('Each instrument family can only be mapped once per model.'),
    'classes_required': N_('Map at least one YOLO class before activating a model.'),
    'active_model_classes_locked': N_('The class mapping of the active model cannot be changed. Register a new version instead.'),
    # Configuration
    'cannot_deactivate_own_institution': N_('You cannot deactivate the institution your account belongs to.'),
    'room_code_exists': N_('An operating room with this code already exists.'),
    'invalid_room': N_('Choose a valid operating room.'),
    'room_required': N_('Choose a valid operating room.'),
    'invalid_date': N_('Use the YYYY-MM-DD date format.'),
}


class AdminDomainError(Exception):
    def __init__(self, status, code, message='', field=None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    @property
    def user_message(self):
        msgid = ERROR_MESSAGES.get(self.code)
        if msgid:
            return gettext(msgid)
        if self.code.endswith('_required'):
            return gettext('Fill in every required field.')
        if self.code.endswith('_too_long'):
            return gettext('One of the fields is too long.')
        return self.message or gettext('Unable to save this record right now.')


