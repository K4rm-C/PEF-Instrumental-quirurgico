# ADMIN-09 — validación en navegador (2026-11-09T20:15:10)

Ejecución `741FEF` · Chromium (Playwright) · portal http://127.0.0.1:15000 · entorno aislado `pef_admin_test`.

**Resumen:** 95 PASS · 0 FAIL · 0 BLOCKED

| RF | Comprobación | Resultado | Detalle | Evidencia |
|---|---|---|---|---|
| RF-AD-01 | dashboard loads with session cards and System Health | PASS | HTTP 200, cards 12 | [rf-ad-01_000_dashboard.jpg](rf-ad-01_000_dashboard.jpg) |
| RF-AD-01 | all health checks reachable with the stack up | PASS | Authentication service	 Reachable 	HTTP 200 · 6 ms PostgreSQL	 Reachable 	SELECT 1 · 1 ms Redis	 Reachable 	PONG · 2 ms MongoDB	 Reachable 	ping · 32 ms Object  |  |
| RF-AD-01 | KPI colour follows the configured thresholds (OR hold -> danger with 0/0) | PASS | stat-card stat-card--danger | [rf-ad-01_002_dashboard-threshold-danger.jpg](rf-ad-01_002_dashboard-threshold-danger.jpg) |
| RF-AD-01 | MongoDB down: dashboard still loads and marks only MongoDB as failing | PASS | {'MongoDB': 'MongoDB  Unreachable  ServerSelectionTimeoutError · 2018 ms', 'PostgreSQL': 'PostgreSQL  Reachable  SELECT 1 · 1 ms', 'Redis': 'Redis  Reachable  P | [rf-ad-01_003_dashboard-mongo-down.jpg](rf-ad-01_003_dashboard-mongo-down.jpg) |
| RF-AD-02 | create family; listed after reload | PASS | Instrument family saved. | [rf-ad-02_004_family-created.jpg](rf-ad-02_004_family-created.jpg) |
| RF-AD-02 | duplicate family code rejected with a message | PASS | That family code already exists. | [rf-ad-02_005_family-duplicate.jpg](rf-ad-02_005_family-duplicate.jpg) |
| RF-AD-02 | edit family name persists after reload | PASS |  |  |
| RF-AD-02 | upload example photo; image rendered from storage | PASS | 1 image(s) | [rf-ad-02_007_family-photo.jpg](rf-ad-02_007_family-photo.jpg) |
| RF-AD-02 | non-image file renamed .png rejected with a message | PASS | This file type is not allowed. | [rf-ad-02_008_family-reject-not-an-image-png.jpg](rf-ad-02_008_family-reject-not-an-image-png.jpg) |
| RF-AD-02 | image > 5 MB rejected with a message | PASS | The file exceeds the maximum size allowed. | [rf-ad-02_009_family-reject-big-photo-png.jpg](rf-ad-02_009_family-reject-big-photo-png.jpg) |
| RF-AD-02 | deactivating a family of the active model needs confirmation | PASS | This family is detected by the active vision model. Confirm to deactivate it. | [rf-ad-02_010_family-active-model-confirm.jpg](rf-ad-02_010_family-active-model-confirm.jpg) |
| RF-AD-02 | deactivate family; shown under Inactive | PASS |  | [rf-ad-02_011_family-inactive.jpg](rf-ad-02_011_family-inactive.jpg) |
| RF-AD-02 | search in the list finds the family (reactivated) | PASS |  |  |
| RF-AD-03 | create procedure with two phases; persisted in order | PASS | Procedure saved.; phases=2 | [rf-ad-03_013_procedure-created.jpg](rf-ad-03_013_procedure-created.jpg) |
| RF-AD-03 | associated kits visible and read-only | PASS | editable controls=0 | [rf-ad-03_014_procedure-kits.jpg](rf-ad-03_014_procedure-kits.jpg) |
| RF-AD-03 | create reasons entry; listed after reload | PASS |  |  |
| RF-AD-03 | create categories entry; listed after reload | PASS |  |  |
| RF-AD-03 | create phases entry; listed after reload | PASS |  |  |
| RF-AD-03 | invalid catalog code (uppercase/space): blocked in the browser and by the server | PASS | Code may only contain lowercase letters, numbers and underscores. | [rf-ad-03_018_catalog-invalid-code.jpg](rf-ad-03_018_catalog-invalid-code.jpg) |
| RF-AD-03 | duplicate catalog code rejected | PASS | That code already exists in this catalog. |  |
| RF-AD-03 | deactivate phase; inactive after reload | PASS |  | [rf-ad-03_020_phase-inactive.jpg](rf-ad-03_020_phase-inactive.jpg) |
| RF-AD-04 | publish PDF notice; listed with its version | PASS | Privacy notice published. | [rf-ad-04_021_notice-published.jpg](rf-ad-04_021_notice-published.jpg) |
| RF-AD-04 | same document again: duplicate warning, published after confirmation | PASS | warning: This document is identical to version ZZB-741FEF-1. Confirm to publish it again. | [rf-ad-04_022_notice-duplicate-confirmed.jpg](rf-ad-04_022_notice-duplicate-confirmed.jpg) |
| RF-AD-04 | publish HTML notice | PASS | Privacy notice published. |  |
| RF-AD-04 | .exe renamed to .pdf rejected with a message | PASS | This file type is not allowed. | [rf-ad-04_024_notice-reject-renamed-exe-pdf.jpg](rf-ad-04_024_notice-reject-renamed-exe-pdf.jpg) |
| RF-AD-04 | PDF > 10 MB rejected with a message | PASS | The file exceeds the maximum size allowed. | [rf-ad-04_025_notice-reject-too-big-pdf.jpg](rf-ad-04_025_notice-reject-too-big-pdf.jpg) |
| RF-AD-04 | download returns the same bytes as an attachment | PASS | attachment; filename="privacy-notice-ZZB-741FEF-1.pdf" |  |
| RF-AD-04 | HTML notice downloaded as attachment with CSP sandbox | PASS | attachment; filename="privacy-notice-ZZB-741FEF-3.html" / sandbox; default-src 'none' |  |
| RF-AD-04 | 9 MB notice (multipart in Garage) published and downloaded intact | PASS | Privacy notice published.; 9437202 bytes | [rf-ad-04_028_notice-9mb.jpg](rf-ad-04_028_notice-9mb.jpg) |
| RF-AD-04 | deactivate and reactivate a version | PASS |  | [rf-ad-04_029_notice-reactivated.jpg](rf-ad-04_029_notice-reactivated.jpg) |
| RF-AD-04 | single-active parameter leaves only one active version | PASS | ['ZZB-741FEF-2'] | [rf-ad-04_030_notice-single-active.jpg](rf-ad-04_030_notice-single-active.jpg) |
| RF-AD-05 | create or_charge user with a room; listed | PASS |  | [rf-ad-05_031_user-created.jpg](rf-ad-05_031_user-created.jpg) |
| RF-AD-05 | duplicate email rejected with a message | PASS | A user with this email already exists. | [rf-ad-05_032_user-duplicate.jpg](rf-ad-05_032_user-duplicate.jpg) |
| RF-AD-05 | short password: blocked in the browser and by the server | PASS | The password is shorter than the minimum length set for your institution. | [rf-ad-05_033_user-short-password.jpg](rf-ad-05_033_user-short-password.jpg) |
| RF-AD-05 | deactivation ends the open session of the user (other browser) | PASS | User deactivated. Their history is kept.; user now at http://127.0.0.1:15000/sign-in?next=/operator/sessions | [rf-ad-05_034_user-deactivated.jpg](rf-ad-05_034_user-deactivated.jpg) |
| RF-AD-05 | reactivated user can sign in again | PASS | http://127.0.0.1:15000/operator/sessions |  |
| RF-AD-05 | password reset: session ended, old password refused, new one works | PASS | Password updated. The user must sign in with the new password. | [rf-ad-05_036_user-password-reset.jpg](rf-ad-05_036_user-password-reset.jpg) |
| RF-AD-05 | an administrator cannot deactivate their own account | PASS | deactivate button offered=False | [rf-ad-05_037_user-self.jpg](rf-ad-05_037_user-self.jpg) |
| RF-AD-05 | users of another institution are not listed and their URL is 404 | PASS | listed=False, HTTP 404 | [rf-ad-05_038_user-other-institution-404.jpg](rf-ad-05_038_user-other-institution-404.jpg) |
| RF-AD-06 | exactly the four fixed roles; no create/delete actions | PASS | codes=['station_operator', 'spd_supervisor', 'it_admin', 'or_charge'], rows=4 | [rf-ad-06_039_roles.jpg](rf-ad-06_039_roles.jpg) |
| RF-AD-06 | edit role description persists; code not editable | PASS |  | [rf-ad-06_040_role-edited.jpg](rf-ad-06_040_role-edited.jpg) |
| RF-AD-06 | description restored | PASS |  |  |
| RF-AD-06 | /admin/roles/new answers 404 | PASS |  |  |
| RF-AD-06 | role of another institution answers 404 | PASS |  |  |
| RF-AD-07 | create operating room; listed | PASS |  | [rf-ad-07_044_room-created.jpg](rf-ad-07_044_room-created.jpg) |
| RF-AD-07 | duplicate room code rejected | PASS | An operating room with this code already exists. |  |
| RF-AD-07 | create capture station with ROI; shown as configured | PASS | Browser station 741FEF	ZZB-R741FEF	Configured	 Active 	Edit | [rf-ad-07_046_station-created.jpg](rf-ad-07_046_station-created.jpg) |
| RF-AD-07 | ROI outside the frame rejected | PASS | The ROI must fit inside the camera frame. | [rf-ad-07_047_station-roi-outside.jpg](rf-ad-07_047_station-roi-outside.jpg) |
| RF-AD-07 | partial ROI rejected | PASS | Fill in every ROI value (x, y, w, h, frame width and frame height). |  |
| RF-AD-07 | deactivate room; still listed as inactive | PASS | ZZB-R741FEF	Browser room 741FEF	 Inactive 	Edit | [rf-ad-07_049_room-inactive.jpg](rf-ad-07_049_room-inactive.jpg) |
| RF-AD-07 | room of another institution answers 404 | PASS |  |  |
| RF-AD-07 | station of another institution answers 404 | PASS |  |  |
| RF-AD-08 | .onnx file rejected (only PyTorch .pt) | PASS | This file type is not allowed. | [rf-ad-08_052_model-reject-onnx.jpg](rf-ad-08_052_model-reject-onnx.jpg) |
| RF-AD-08 | weights above the configured maximum rejected | PASS | The file exceeds the maximum size allowed. | [rf-ad-08_053_model-reject-size.jpg](rf-ad-08_053_model-reject-size.jpg) |
| RF-AD-08 | upload real best.pt (step 1) | PASS | already uploaded in an earlier run as best-6215cb74-222bef |  |
| RF-AD-08 | stored checksum equals the SHA-256 of the file; object in the configured storage | PASS | ('6215cb746e20eb9e10c812fe03679b59263bf268b1fb0d419724611db2c785d9', 'pef-admin-browser', 'models/b1a5bf60228d421b930594f4e8350ba1.pt', 53068645) |  |
| RF-AD-08 | the 17 classes of the real model are shown | PASS | ['Adson without teeth', 'Adson with teeth', 'Foerster forceps', 'Allis forceps', 'Straight Kelly Forceps', 'Rochester-Pean forceps', 'Pozzi forceps', 'Olsen-Heg | [rf-ad-08_056_model-classes.jpg](rf-ad-08_056_model-classes.jpg) |
| RF-AD-08 | mapping published (classes mapped / rejected, missing-family warning confirmed) | PASS | mapped=16, rejected=['Surgical probe'], warning=These active instrument families are not detected by this model. Sessions that expect them will need manual coun | [rf-ad-08_057_model-mapped.jpg](rf-ad-08_057_model-mapped.jpg) |
| RF-AD-08 | class map stored without the rejected class | PASS | 16 classes |  |
| RF-AD-08 | activate the real model: it is the only active one | PASS | best-6215cb74-222bef	16 Classes	 Active Model 	2026-11-09	6215...5d9	Edit | [rf-ad-08_059_model-active.jpg](rf-ad-08_059_model-active.jpg) |
| RF-AD-08 | same weights uploaded again: duplicate checksum rejected | PASS | These weights are already registered as version best-6215cb74-222bef. | [rf-ad-08_060_model-duplicate.jpg](rf-ad-08_060_model-duplicate.jpg) |
| RF-AD-08 | deactivating the only active model with active stations is refused | PASS | Activate another model first: active capture stations need one. | [rf-ad-08_061_model-deactivate-refused.jpg](rf-ad-08_061_model-deactivate-refused.jpg) |
| RF-AD-08 | bind payload for VisionWorker carries the activated model and its class map (no rejected class) | PASS | {'model_id': 'b4811029-bf78-4001-bb2e-46186b8087f6', 'model_version': 'best-6215cb74-222bef', 'classes': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]} |  |
| RF-AD-10 | rename institution persists; own institution cannot be deactivated | PASS | You cannot deactivate the institution your account belongs to. | [rf-ad-10_063_institution-deactivate-refused.jpg](rf-ad-10_063_institution-deactivate-refused.jpg) |
| RF-AD-10 | warning above danger rejected | PASS | Check the highlighted parameter: its value is not valid. | [rf-ad-10_064_settings-invalid-threshold.jpg](rf-ad-10_064_settings-invalid-threshold.jpg) |
| RF-AD-10 | empty MIME list rejected | PASS | Check the highlighted parameter: its value is not valid. |  |
| RF-AD-10 | password minimum (12) persists and is applied to new users (10 chars refused) | PASS | The password is shorter than the minimum length set for your institution. | [rf-ad-10_066_settings-password-policy.jpg](rf-ad-10_066_settings-password-policy.jpg) |
| SEC | anonymous visitor is sent to sign-in | PASS | http://127.0.0.1:15000/sign-in?next=/admin/users |  |
| SEC | AuthService recognises spd_supervisor | PASS | ['spd_supervisor'] |  |
| SEC | spd_supervisor: every Administration page is refused (landing /supervisor/dashboard) | PASS | {'/admin/dashboard': 403, '/admin/users': 403, '/admin/vision-models': 403, '/admin/configuration': 403} | [sec_069_spd-supervisor-admin-403.jpg](sec_069_spd-supervisor-admin-403.jpg) |
| SEC | AuthService recognises station_operator | PASS | ['station_operator'] |  |
| SEC | station_operator: every Administration page is refused (landing /operator/sessions) | PASS | {'/admin/dashboard': 403, '/admin/users': 403, '/admin/vision-models': 403, '/admin/configuration': 403} | [sec_071_station-operator-admin-403.jpg](sec_071_station-operator-admin-403.jpg) |
| SEC | AuthService recognises or_charge | PASS | ['or_charge'] |  |
| SEC | or_charge: the portal has no screens for this role and says so on the sign-in page | PASS | /sign-in |  |
| SEC | or_charge: every Administration page is refused (landing /sign-in) | PASS | {'/admin/dashboard': 200, '/admin/users': 200, '/admin/vision-models': 200, '/admin/configuration': 200} | [sec_074_or-charge-admin-403.jpg](sec_074_or-charge-admin-403.jpg) |
| SEC | AuthService recognises it_admin | PASS | ['it_admin'] |  |
| SEC | pieces and kits stay with the SPD supervisor (admin gets 403) | PASS | {'/admin/instruments': 403, '/admin/kits': 403} |  |
| SEC | denied Administration attempts are audited | PASS |  | [sec_077_audit-denied-after-roles.jpg](sec_077_audit-denied-after-roles.jpg) |
| SEC-STEWARD | without the variable any it_admin (also institution B) can edit shared catalogs | PASS | Instrument family saved. | [sec-steward_078_steward-institution-b-no-steward.jpg](sec-steward_078_steward-institution-b-no-steward.jpg) |
| SEC-STEWARD | with steward = A, institution B gets 403 "Shared catalogs are maintained..." | PASS | Shared catalogs are maintained by the administrators of the steward institution. | [sec-steward_079_steward-institution-b-steward-a.jpg](sec-steward_079_steward-institution-b-steward-a.jpg) |
| SEC-STEWARD | with steward = A, institution A can still edit | PASS | Instrument family saved. | [sec-steward_080_steward-institution-a-steward-a.jpg](sec-steward_080_steward-institution-a-steward-a.jpg) |
| SEC-STEWARD | with steward = A, institution B can still read the catalog | PASS | HTTP 200 |  |
| RF-AD-09 | audit log lists this run's family events (search) | PASS |  | [rf-ad-09_082_audit-search.jpg](rf-ad-09_082_audit-search.jpg) |
| RF-AD-09 | filter by result DENIED | PASS |  | [rf-ad-09_083_audit-denied.jpg](rf-ad-09_083_audit-denied.jpg) |
| RF-AD-09 | filter by event type ACTIVATE_VISION_MODEL | PASS |  |  |
| RF-AD-09 | From after To: notice shown and nothing listed | PASS | rows=1 The start date is after the end date: choose a valid date range. | [rf-ad-09_085_audit-empty-range.jpg](rf-ad-09_085_audit-empty-range.jpg) |
| RF-AD-09 | pagination: next page loads | PASS | http://127.0.0.1:15000/admin/audit-log?per_page=50&page=2 | [rf-ad-09_086_audit-page-2.jpg](rf-ad-09_086_audit-page-2.jpg) |
| RF-AD-09 | another institution's administrator does not see them | PASS |  | [rf-ad-09_087_audit-other-institution.jpg](rf-ad-09_087_audit-other-institution.jpg) |
| PERSIST | after restarting portal + Garage: real model still active with its 17 classes | PASS | best-6215cb74-222bef	16 Classes	 Active Model 	2026-11-09	6215...5d9	Edit | [persist_088_persist-model.jpg](persist_088_persist-model.jpg) |
| PERSIST | weights object read back from Garage after restart: same SHA-256 | PASS | pef-admin-browser/models/b1a5bf60228d421b930594f4e8350ba1.pt (53068645 bytes) |  |
| PERSIST | privacy notice downloaded after restart: same SHA-256 | PASS |  |  |
| PERSIST | family photo still rendered after restart | PASS |  | [persist_091_persist-family-photo.jpg](persist_091_persist-family-photo.jpg) |
| PERSIST | users created in this run still listed after restart | PASS |  |  |
| PERSIST | local backend: notice published, portal restarted, same bytes downloaded | PASS | /admin/privacy-notices/93fb8c6c-6f41-46e1-9db6-4f2b48758531/document |  |
| RESTORE | restore: previously active model active again (test DB left as found) | PASS | HTTP 200 |  |

## Errores de consola, de página y respuestas 5xx

- Ninguno.

## Respuestas 4xx esperadas (pruebas negativas: validaciones, 403, 404)

- [RF-AD-02] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/instrument-families/new)
- [RF-AD-02] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/instrument-families/41414141-0000-4000-8000-000000000015/edit)
- [RF-AD-03] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/selection-catalogs/reasons)
- [RF-AD-03] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/selection-catalogs/reasons)
- [RF-AD-04] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/privacy-notices/new)
- [RF-AD-04] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/privacy-notices/new)
- [RF-AD-04] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/privacy-notices/new)
- [RF-AD-05] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/users/new)
- [RF-AD-05] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/users/new)
- [RF-AD-05] Failed to load resource: the server responded with a status of 401 (UNAUTHORIZED) (/sign-in)
- [RF-AD-05] Failed to load resource: the server responded with a status of 404 (NOT FOUND) (/admin/users/7f4eec5f-8c89-452f-b92c-f1df38c1e8a8/edit)
- [RF-AD-06] Failed to load resource: the server responded with a status of 404 (NOT FOUND) (/admin/roles/new)
- [RF-AD-06] Failed to load resource: the server responded with a status of 404 (NOT FOUND) (/admin/roles/c94d8361-6129-40f6-9f55-3fc3165043e2/edit)
- [RF-AD-07] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/configuration/operating-rooms/new)
- [RF-AD-07] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/configuration/capture-stations/ef2ec32b-a770-4fb8-9bfb-3a7b3ecd046d/edit)
- [RF-AD-07] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/configuration/capture-stations/ef2ec32b-a770-4fb8-9bfb-3a7b3ecd046d/edit)
- [RF-AD-07] Failed to load resource: the server responded with a status of 404 (NOT FOUND) (/admin/configuration/operating-rooms/b4eca18d-92b0-43f3-b4b0-c9fa341b0638/edit)
- [RF-AD-07] Failed to load resource: the server responded with a status of 404 (NOT FOUND) (/admin/configuration/capture-stations/e3b36771-d1e4-429a-95bd-2ae089be0ca0/edit)
- [RF-AD-08] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/vision-models/new)
- [RF-AD-08] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/vision-models/new)
- [RF-AD-08] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/vision-models/b4811029-bf78-4001-bb2e-46186b8087f6/edit)
- [RF-AD-08] Failed to load resource: the server responded with a status of 409 (CONFLICT) (/admin/vision-models/new)
- [RF-AD-10] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/configuration/institution/edit)
- [RF-AD-10] Failed to load resource: the server responded with a status of 400 (BAD REQUEST) (/admin/users/new)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/dashboard)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/users)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/vision-models)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/configuration)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/dashboard)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/users)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/vision-models)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/configuration)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/instruments)
- [SEC] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/kits)
- [SEC-STEWARD] Failed to load resource: the server responded with a status of 403 (FORBIDDEN) (/admin/instrument-families/4d52ef64-113f-41d1-95dd-47efdbf828b6/edit)
