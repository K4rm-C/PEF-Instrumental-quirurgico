# UI Reference V2 — Screen Manifest

This directory contains the approved V2 visual references for the Flask web application.

## Rules

- `ui_reference/` contains the original V1 references and must not be deleted.
- `ui_reference_v2/` contains the approved target UI.
- PNG files are visual references, not assets to display directly in the application.
- Existing Jinja templates, components, routes, CSS and JavaScript should be reused and extended whenever possible.
- Do not create a parallel frontend architecture.
- All application UI must remain in English.
- Preserve Flask + Jinja and the existing MVC structure.
- Do not replace working backend/database behavior with hardcoded frontend logic.
- New views must use existing PostgreSQL models and relationships whenever they are available.

## Shared

| Reference | Type | Current implementation | V2 action |
|---|---|---|---|
| public-landing-page.png | Page | None | CREATE |
| sign-in.png | Page | templates/auth/sign_in.html | UPDATE |

## Operator

| Reference | Current implementation | V2 action |
|---|---|---|
| operator-dashboard.png | templates/operator/dashboard.html | UPDATE |
| counting-sessions.png | templates/operator/sessions/list.html | UPDATE |
| new-counting-session.png | templates/operator/sessions/new.html | UPDATE |
| active-session-capture.png | templates/operator/sessions/active.html | UPDATE / SPLIT |
| active-session-ai-detection.png | templates/operator/sessions/active.html | CREATE VIEW |
| human-validation-correction.png | templates/operator/sessions/validation.html | UPDATE |
| count-validation-summary.png | None | CREATE |
| discrepancy-escalation.png | None | CREATE |
| operator-awaiting-supervisor-review.png | None | CREATE |
| operator-correction-requested.png | None | CREATE |
| operator-ready-to-close.png | None | CREATE |
| close-session-confirmation.png | templates/components/modals/close_session.html | UPDATE |
| session-history.png | templates/operator/sessions/history.html | UPDATE |
| closed-session-details.png | templates/operator/sessions/closed_details.html | UPDATE |

## Supervisor

| Reference | Current implementation | V2 action |
|---|---|---|
| supervisor-dashboard.png | templates/supervisor/dashboard.html | UPDATE |
| supervisor-sessions.png | templates/supervisor/sessions/list.html | UPDATE |
| supervisor-discrepancies.png | templates/supervisor/discrepancies/list.html | UPDATE |
| supervisor-review-detail.png | templates/supervisor/discrepancies/review.html | UPDATE |
| supervisor-resolution-approve.png | templates/components/modals/approve_review.html | UPDATE |
| supervisor-request-correction.png | templates/components/modals/request_correction.html | UPDATE |
| supervisor-reject-review.png | None | CREATE MODAL |
| supervisor-session-details.png | templates/supervisor/sessions/details.html | UPDATE |
| supervisor-session-history.png | templates/supervisor/sessions/history.html | UPDATE |
| supervisor-reports.png | templates/supervisor/reports.html | UPDATE |
| supervisor-indicators.png | templates/supervisor/indicators.html | UPDATE |
| supervisor-audit-log.png | templates/supervisor/audit_log.html | UPDATE |

## Administrator

### Dashboard
- it-admin-dashboard.png
  - UPDATE templates/admin/dashboard.html

### Instrument Families
- it-admin-instrument-families.png
- it-admin-new-instrument-family.png
- it-admin-edit-instrument-family.png
  - UPDATE existing templates/admin/instrument_families/

### Instruments
- it-admin-instruments.png
- it-admin-new-instrument.png
- it-admin-edit-instrument.png
  - UPDATE existing templates/admin/instruments/

### Kits
- it-admin-kits.png
- it-admin-new-kit.png
- it-admin-edit-kit.png
  - UPDATE existing templates/admin/kits/

### Procedures
- it-admin-procedures.png
- it-admin-new-procedure.png
- it-admin-edit-procedure.png
  - CREATE templates/admin/procedures/

### Users
- it-admin-users.png
- it-admin-new-user.png
- it-admin-edit-user.png
- it-admin-change-password-modal.png
- it-admin-deactivate-user-modal.png
  - UPDATE templates/admin/users/
  - UPDATE existing modal components

### Roles
- it-admin-roles.png
- it-admin-new-role.png
- it-admin-edit-role.png
  - UPDATE templates/admin/roles/
  - CREATE New Role view

### Vision Models
- it-admin-vision-models.png
- it-admin-new-vision-model.png
- it-admin-edit-vision-model.png
- it-admin-activate-vision-model-modal.png
  - CREATE templates/admin/vision_models/

### Audit Log
- it-admin-audit-log.png
  - CREATE templates/admin/audit_log.html

### Configuration
- it-admin-configuration.png
- it-admin-edit-institution.png
  - UPDATE templates/admin/configuration/

### Operating Rooms
- it-admin-new-operating-room.png
- it-admin-edit-operating-room.png
  - CREATE templates/admin/configuration/operating_room_form.html

### Capture Stations
- it-admin-new-capture-station.png
- it-admin-edit-capture-station.png
  - UPDATE templates/admin/configuration/capture_station_form.html

  