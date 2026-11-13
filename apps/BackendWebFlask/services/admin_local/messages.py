from flask_babel import gettext

from i18n import N_


# Admin domain error code -> English msgid (translated with gettext when shown).
ERROR_MESSAGES = {
    # MS-12 model regression runs.
    'invalid_regression_report': N_('The report must be the JSON produced by scripts/model_regression.py.'),
    'invalid_reference_set': N_('Name the reference set (letters, digits, spaces, . : / ( ) + -).'),
    'regression_model_mismatch': N_('The candidate weights of the report are not this model.'),
    'regression_baseline_unknown': N_('The baseline weights of the report are not a registered model.'),
    # MS-12 camera calibration.
    'camera_inactive': N_('Activate the camera before calibrating it.'),
    'invalid_calibration_method': N_('Choose a valid calibration method.'),
    'invalid_frame_width': N_('The frame width must be between 160 and 7680 pixels.'),
    'invalid_frame_height': N_('The frame height must be between 120 and 4320 pixels.'),
    'calibration_frame_mismatch': N_('The calibration frame must have the resolution configured for the camera.'),
    'invalid_reference_length_mm': N_('The reference length must be a positive value in millimetres (up to 5000, 3 decimals).'),
    'invalid_measured_pixels': N_('The measured length must be a positive number of pixels (3 decimals at most).'),
    'measured_pixels_outside_frame': N_('The measured length cannot be longer than the diagonal of the frame.'),
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
    'inactive_family': N_('This instrument family is inactive. Choose an active family.'),
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
    'password_too_short': N_('The password is shorter than the minimum length set for your institution.'),
    'password_mismatch': N_('Passwords do not match.'),
    'cannot_deactivate_self': N_('You cannot deactivate your own account.'),
    'cannot_remove_own_admin': N_('You cannot remove the administrator role from your own account.'),
    'last_admin': N_('At least one active administrator must remain.'),
    'foreign_institution': N_('Users can only belong to your institution.'),
    'rooms_need_floor_supervisor': N_('Only floor supervisors (or_charge) are assigned to operating rooms.'),
    # Uploads and object storage
    'file_required': N_('Choose a file to upload.'),
    'file_too_large': N_('The file exceeds the maximum size allowed.'),
    'invalid_file_type': N_('This file type is not allowed.'),
    'empty_file': N_('The file is empty.'),
    'storage_unavailable': N_('Object storage is not available right now. Try again in a moment.'),
    # Families and selection catalogs
    'family_in_active_model': N_('This family is detected by the active vision model. Confirm to deactivate it.'),
    'example_not_found': N_('Record not found.'),
    'procedure_kits_owned_by_spd': N_('Kit links of a procedure are maintained by the SPD supervisor.'),
    'catalog_code_exists': N_('That code already exists in this catalog.'),
    'invalid_catalog_code': N_('Code may only contain lowercase letters, numbers and underscores.'),
    'catalog_code_immutable': N_('The code cannot be changed once created.'),
    # Privacy notices
    'privacy_version_exists': N_('A privacy notice with this version already exists.'),
    'privacy_notice_not_found': N_('Record not found.'),
    'document_not_stored': N_('This document is not stored by the portal.'),
    # Capture stations
    'roi_incomplete': N_('Fill in every ROI value (x, y, w, h, frame width and frame height).'),
    'invalid_roi': N_('ROI values must be whole, positive numbers of pixels.'),
    'roi_outside_frame': N_('The ROI must fit inside the camera frame.'),
    # MS-07 integration clients
    'scopes_required': N_('Choose at least one permission for the client.'),
    'invalid_scope': N_('One of the permissions is not valid.'),
    'integration_client_not_found': N_('Record not found.'),
    # Vision models (upload and mapping)
    'names_required': N_('The class names could not be read from the weights. Upload names.yaml or a JSON {id: name} file.'),
    'invalid_names_file': N_('The names file must be names.yaml or JSON {id: name}.'),
    'names_mismatch': N_('The names file does not match the classes inside the weights.'),
    'unmapped_classes': N_('Map every class of the model to a family or reject it.'),
    'last_active_model': N_('Activate another model first: active capture stations need one.'),
    'model_without_class_names': N_('This model has no class names; use the manual mapping.'),
    'shared_catalog_read_only': N_('Shared catalogs are maintained by the administrators of the steward institution.'),
    # Settings
    'invalid_setting': N_('Check the highlighted parameter: its value is not valid.'),
    # Roles
    'role_code_exists': N_('A role with this code already exists for the institution.'),
    'invalid_role_code': N_('Role code may only contain lowercase letters, numbers and underscores.'),
    'description_required': N_('Description is required.'),
    'system_role': N_('System roles cannot be deleted.'),
    'role_in_use': N_('Remove this role from every user before deleting it.'),
    'role_codes_fixed': N_('Role codes are fixed: roles cannot be created or deleted.'),
    'role_code_immutable': N_('The role code cannot be changed.'),
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


