# Frontend Integration Notes

This file documents the frontend foundation built under `templates/` and `static/`, and
everything the backend team will eventually need to connect. It covers the complete
Operator CDE presentation layer: the shared shell (base layout, guest layout, authenticated
layout, header, Operator sidebar), Sign In, Operator Dashboard, Counting Sessions, New
Counting Session, Session History, Active Counting Session, Count Validation (no
discrepancy / with discrepancy), the Close Session and Supervisor Review Required modals,
and Closed Session Details — plus, as of this iteration, the complete **Supervisor CDE /
Quality** presentation layer: the Supervisor sidebar, Supervisor Dashboard, Sessions,
Discrepancies (including its Post Approval / Post Correction result states), Discrepancy
Review, Session Details, Session History, Reports, Indicators, Audit Log, and the Approve
Review / Request Correction modals — plus, as of this iteration, the complete **IT
Administrator** presentation layer: the Administrator sidebar, Dashboard, Instrument
Families, Instruments, and Kits catalogs (each with a shared New/Edit form template), Users
(New/Edit, plus the Change Password and Deactivate User modals), Roles (list + Edit Role),
and Configuration (Institution Information + Capture Stations, each with its own Edit/New
form). Every Administrator screen reuses the same base layout, header, macros, and CSS token
system already built for Shared/Operator/Supervisor — no parallel component system was
created.

## Templates created

```
templates/
├── base.html                          Root HTML document: <head>, theme boot script, CSS/JS includes
├── layouts/
│   ├── guest.html                     Centered-card shell (extends base.html) — used by Sign In
│   └── authenticated.html             Sidebar + header + scrollable content + optional sticky
│                                       page_action_bar block (extends base.html)
├── partials/
│   ├── header.html                    Right-hand header cluster: notifications, language, theme, user menu
│   ├── sidebar_operator.html          Operator role sidebar navigation (accepts optional nav_urls)
│   ├── sidebar_supervisor.html        Supervisor role sidebar navigation (accepts optional nav_urls)
│   ├── sidebar_admin.html             IT Administrator role sidebar navigation (accepts optional nav_urls)
│   ├── language_selector.html         EN / Español (México) dropdown
│   ├── theme_selector.html            Light / Dark / System dropdown
│   ├── user_menu.html                 Avatar + name + role + menu affordance
│   ├── flash_messages.html            Renders Flask's flashed messages, if any
│   └── feedback_banner.html           Renders an optional one-off feedback_banner context
│                                       dict (e.g. "Review submitted successfully."),
│                                       distinct from Flask's flash() mechanism
├── components/
│   ├── macros.html                    Inline SVG icon macros, status_badge(), stat_card(),
│   │                                  alert_banner() (two-line icon+title+description alert)
│   └── modals/
│       ├── close_session.html         "Close Counting Session?" confirmation modal
│       ├── supervisor_review_required.html   "Supervisor Review Required" modal
│       ├── approve_review.html        "Approve Discrepancy Review?" confirmation modal
│       ├── request_correction.html    "Request Operator Correction?" modal (+ Reason field)
│       ├── change_password.html       "Change User Password" modal (New/Confirm New Password)
│       └── deactivate_user.html       "Deactivate User?" confirmation modal (destructive styling)
├── shared/
│   └── profile.html                   Shared read-only My Profile page for all three roles;
│                                      uses the active role's sidebar and shows account info
│                                      plus interface preference summary
├── auth/
│   └── sign_in.html                   Sign In page (extends layouts/guest.html)
├── operator/
│   ├── dashboard.html                 Operator Dashboard (extends layouts/authenticated.html)
│   └── sessions/
│       ├── list.html                  Counting Sessions (operational queue)
│       ├── new.html                   New Counting Session (Session Setup / Expected
│       │                              Inventory Preview / Instrument Readiness + sticky
│       │                              Cancel / Start Session action bar)
│       ├── history.html               Session History (Closed / Cancelled)
│       ├── active.html                Active Counting Session (capture area placeholder +
│       │                              Instrument Count with live +/- counters + Current
│       │                              Alerts + sticky Cancel/Save Progress/Validate Count)
│       ├── validation.html            Count Validation — ONE template driving both the No
│       │                              Discrepancy and Discrepancy states via has_discrepancy;
│       │                              includes both modals
│       └── closed_details.html        Closed Session Details (read-only)
├── supervisor/
│   ├── dashboard.html                 Supervisor Dashboard
│   ├── reports.html                   Reports (report cards, presentation-only actions)
│   ├── indicators.html                Indicators (KPI cards, bar charts, donut chart)
│   ├── audit_log.html                 Audit Log (read-only, no Action column)
│   ├── sessions/
│   │   ├── list.html                  Sessions (read-only monitoring queue)
│   │   ├── history.html               Session History (Closed / Cancelled)
│   │   └── details.html               Session Details (fully read-only)
│   └── discrepancies/
│       ├── list.html                  Discrepancies — ONE template driving the default
│       │                              listing plus the Post Approval / Post Correction
│       │                              result states via context (rows + feedback_banner)
│       └── review.html                Discrepancy Review (standalone: Session Information,
│                                       PRE/POST Comparison, Count Comparison, Operator
│                                       Submission, Supervisor Review, decision actions);
│                                       includes both Supervisor modals
└── admin/
    ├── dashboard.html                 IT Administrator Dashboard (stat cards + Catalog
    │                                  Overview / System Overview definition tables)
    ├── instrument_families/
    │   ├── list.html                  Instrument Families catalog
    │   └── form.html                  ONE template driving New Instrument Family and Edit
    │                                  Instrument Family via form_mode ("create" | "edit");
    │                                  Code is read-only in edit mode
    ├── instruments/
    │   ├── list.html                  Instruments catalog (Cycle Status and Active Status
    │   │                              kept as two distinct badge columns, never merged)
    │   └── form.html                  ONE template driving New/Edit Instrument; Internal
    │                                  Code is read-only in edit mode
    ├── kits/
    │   ├── list.html                  Kits catalog (term "Kit" only — never "Tray")
    │   └── form.html                  ONE template driving New/Edit Kit: Kit Name/Version
    │                                  (read-only)/Status + a Kit Composition table with
    │                                  presentation-only Add Instrument Type/Remove rows and
    │                                  a live Instrument Types/Total Expected Instruments
    │                                  summary (static/js/pages/kit-form.js)
    ├── users/
    │   ├── list.html                  Users administration list
    │   └── form.html                  ONE template driving New User (adds Password/Confirm
    │                                  Password with static/js/pages/user-form.js match
    │                                  validation) and Edit User (adds Change Password /
    │                                  Deactivate User, including both modals)
    ├── roles/
    │   ├── list.html                  Roles list (Description and Assigned Users always
    │   │                              separate columns, never concatenated)
    │   └── edit.html                  Edit Role only — no New Role screen exists (no
    │                                  approved Figma reference for it); Role Code and
    │                                  Assigned Users are read-only; no permissions matrix
    └── configuration/
        ├── index.html                 Configuration: Institution Information + Capture
        │                              Stations only — no General Settings section exists
        ├── institution_form.html      Edit Institution Information (Institution Code
        │                              read-only)
        └── capture_station_form.html  ONE template driving New/Edit Capture Station;
                                        Station Code is read-only in edit mode
```

## Static assets created

```
static/
├── css/
│   ├── tokens.css            Design tokens (color, spacing, radius, shadow, typography) — light + dark
│   ├── base.css              Reset, bare element styles, .visually-hidden utility
│   ├── layout.css            App shell, sidebar, header, breadcrumb, page heading,
│   │                         guest shell, .page-action-bar + .page-action-bar__group
│   ├── components/
│   │   ├── buttons.css, forms.css, cards.css, badges.css, tables.css,
│   │   ├── dropdowns.css, alerts.css, charts.css, modals.css,
│   │   ├── filters.css, pagination.css (search/filter bar, Previous/Next pagination)
│   │   ├── counter.css       Touch-friendly +/- stepper (Instrument Count)
│   │   └── session-details.css   Session context bar, session summary header+meta, info
│   │                         grid, comparison row, two-column workspace grid, sub-panel
│   └── pages/
│       ├── sign-in.css        Sign In card layout
│       ├── dashboard.css      Intentionally near-empty; dashboard reuses component styles
│       ├── profile.css        Shared My Profile layout (summary, account details, preferences)
│       └── active-counting-session.css   Capture area placeholder box
├── js/
│   ├── theme.js               Applies/persists Light/Dark/System via localStorage + data-theme
│   ├── dropdown.js            Generic open/close for the language, theme, and (future) other dropdowns
│   ├── modal.js                Generic open/close for Close Session / Supervisor Review
│   │                          Required (and any future modal)
│   └── pages/
│       ├── new-counting-session.js   Updates read-only Patient/Physician/Operating Room
│       │                            fields from the selected Operation's data-* attributes
│       ├── counting-session.js       Increments/decrements a counted quantity, recomputes
│       │                             the difference, toggles the row's discrepancy
│       │                             highlight — presentation-only, never persisted
│       ├── kit-form.js                IT Administrator Kit form: Add Instrument Type/Remove
│       │                             composition rows + live Instrument Types/Total Expected
│       │                             Instruments summary recompute — presentation-only
│       └── user-form.js               IT Administrator New User form: Password/Confirm
│                                     Password match check, showing a translation-ready
│                                     inline message rendered server-side via Jinja
├── img/                       Reserved, empty
└── icons/                     Reserved, empty — icons are inline SVG macros for currentColor theming
```

### Shared component additions in this iteration

- `forms.css` gained `.form-field__textarea` (Verification Notes).
- `cards.css` gained `.stat-card-grid--three` variants extended for the 1024px breakpoint.
- `tables.css` gained `.data-table__row--highlight-danger` (discrepancy row tint).
- `badges.css` gained `.text-success` / `.text-danger` utilities for the Difference column.
- `alerts.css` gained `.alert__content` / `.alert__title` / `.alert__description` for
  two-line banners (via the new `alert_banner()` macro); `.alert`'s `align-items` changed
  from `center` to `flex-start` to support this (no visible change for existing single-line
  alerts, verified pixel-identical on New Counting Session's readiness banner).
- `modals.css` gained the full inner dialog structure: `.modal-dialog__icon` (+ `--warning` /
  `--success`), `.modal-dialog__title`, `.modal-dialog__description`, `.modal-dialog__info-box`
  (+ `__info-highlight`, `__info-separator`), `.modal-dialog__actions`.
- `macros.html` gained icon macros `icon_search`, `icon_check_circle`, `icon_alert_triangle`,
  `icon_check_square` (previous iteration), plus `icon_minus`, `icon_camera`, and the
  `alert_banner(variant, title, description)` macro (this iteration).
- `layouts/authenticated.html` gained an optional `{% block page_action_bar %}` (previous
  iteration, reused here for Active Session and Count Validation) and now also includes
  `partials/feedback_banner.html` right after `flash_messages.html`, empty/inert unless a
  view passes a `feedback_banner` dict. Sign In and Dashboard verified pixel-identical
  before/after every change in this iteration.

### Shared component additions for the Supervisor iteration

No new CSS files or JS files were created for Supervisor — every screen reuses the same
shell, layout, table, filter, pagination, card, badge, alert, form, and modal components
already built for Shared/Operator. The following existing shared files were extended
minimally (no Supervisor-specific parallel components were created):

- `tokens.css` gained `--color-on-warning` (light `#ffffff`, dark `#201200` — a dark ink so
  filled amber buttons stay legible against the dark theme's brighter `--color-warning`).
- `buttons.css` gained `.btn--accent` (outlined, `--color-primary`-colored — used for
  actionable "Review" buttons in Supervisor tables, visually distinct from the neutral
  `.btn--secondary` used for "View Details"/"Continue") and `.btn--warning` (filled amber —
  the Request Correction modal's primary action).
- `cards.css` gained `.stat-card--warning` / `.stat-card--success` (joining the existing
  `--info` / `--danger`), `.stat-card__description` (an optional third line under a stat
  card's value, e.g. "Expected Instruments"), `.card__header-note` (a small uppercase note
  anchored to a card header, e.g. "DIFFERENCE: -1"), `.form-grid--four` (a 4-column variant
  of the existing 2-column `.form-grid`, used by the Reports filter row), and the
  `.report-card__*` rules (icon, title, description, divider, footer, actions) for Reports'
  cards.
- `session-details.css` gained `.info-grid--three` (a 3-column variant of the existing
  2-column `.info-grid`, used by Discrepancy Review's and Session Details' summary grids).
- `badges.css` gained `.text-primary` / `.text-warning` utilities (joining the existing
  `.text-success` / `.text-danger`), used for Audit Log's Resource column and Session
  Details' Difference note.
- `charts.css` gained `.bar-chart__bar--danger` (recolors a bar chart's bars, used by
  Indicators' "Discrepancies by Kit"), `.status-progress-item__fill--primary` / `--neutral`
  (joining the existing info/success/danger/warning variants), and the `.donut-chart*` /
  `.legend-list` / `.legend-item*` rules — a lightweight CSS-only donut chart (a
  `conic-gradient` ring plus a plain surface-colored center hole; no charting library or SVG
  path math) used by Indicators' "Session Status".
- `macros.html` gained icon macros `icon_document` (Reports sidebar link + report cards),
  `icon_chart_bar` (Indicators sidebar link), and `icon_shield_check` (Audit Log sidebar
  link) — all in the same inline-SVG, `currentColor`, 20x20 viewBox style as every existing
  icon macro.
- `components/modals/close_session.html` and `supervisor_review_required.html` were
  **not** modified; `approve_review.html` and `request_correction.html` reuse the exact same
  `.modal-overlay` / `.modal-dialog` / `data-modal-open` / `data-modal-close` structure and
  the existing generic `static/js/modal.js` — no new JavaScript was written for either modal.

### Shared component additions for the IT Administrator iteration

No new CSS component files were created for the Administrator layer either — every screen
reuses the same shell, layout, table, filter, pagination, card, badge, form, and modal
components already built for Shared/Operator/Supervisor. Two new page-specific JS files were
added (`kit-form.js`, `user-form.js`, both listed above); the following existing shared files
were extended minimally:

- `tokens.css` gained `--color-on-danger` (light `#ffffff`, dark `#2a0a08` — same dark-ink
  pattern as the existing `--color-on-warning`, keeping a filled danger button legible in
  both themes).
- `buttons.css` gained `.btn--danger-solid` (filled destructive button — Deactivate User and
  its confirmation modal — visually distinct from the existing outlined `.btn--danger` used
  by Operator's "Cancel Session"), `.link-action` (a borderless primary-colored text action
  with a leading icon — "+ Add Instrument Type"), and `.link-danger` (a borderless
  destructive text action — each Kit Composition row's "Remove").
- `cards.css` gained `.card--form` (caps New/Edit form cards at 760px instead of stretching
  them across the full content width, matching the approved references), `.stat-card-grid--two`
  (Kit form's Instrument Types / Total Expected Instruments summary tiles), and
  `.card__title-bar` (a card title with a right-aligned action button, e.g. Configuration's
  "Institution Information" + "Edit").
- `tables.css` gained `.data-table__cell--truncate` (Roles' Description column, matching the
  approved reference's ellipsis truncation) and `.data-table__input` (the small inline
  quantity input used in Kit Composition rows).
- `forms.css` gained `.form-actions` (the in-card divider + right-aligned Cancel/Save row used
  by every Administrator New/Edit form, distinct from the full-width sticky
  `.page-action-bar` used by Operator's multi-section New Counting Session),
  `.form-section-divider` (separates Edit User's trailing "Account Status" block from the
  fields above it), and `.form-field__helper--error` (the red "Passwords do not match."
  message).
- `modals.css` gained `.modal-dialog__info-box--user` / `.modal-dialog__user-name` /
  `.modal-dialog__user-role` (the avatar + name + role row inside the Deactivate User modal).
- `macros.html` gained icon macros `icon_layers` (Instrument Families), `icon_wrench`
  (Instruments), `icon_box` (Kits), `icon_users` (Users), `icon_key` (Roles), and
  `icon_settings` (Configuration) — all in the same inline-SVG, `currentColor`, 20x20 viewBox
  style as every existing icon macro. The Administrator Dashboard's sidebar link reuses the
  existing `icon_grid()` (four-square grid) rather than a new house icon, per the approved
  scope (section 6 of the implementation prompt explicitly calls out not to use a house icon).
- `components/modals/close_session.html`, `supervisor_review_required.html`,
  `approve_review.html`, and `request_correction.html` were **not** modified; the two new
  Administrator modals (`change_password.html`, `deactivate_user.html`) reuse the exact same
  `.modal-overlay` / `.modal-dialog` / `data-modal-open` / `data-modal-close` structure and the
  existing generic `static/js/modal.js` — no new JavaScript was written for either modal's
  open/close behavior.

## Shared Jinja context expected

These variables are consumed with safe local defaults (`|default(...)`) so every template
renders sensibly even before a view supplies real data. Once a Flask view passes the real
value, the default is simply not used.

| Variable | Used in | Shape | Notes |
|---|---|---|---|
| `current_locale` | `base.html`, `language_selector.html` | string, e.g. `'en'` or `'es-MX'` | Drives `<html lang>` and the highlighted language option. No persistence yet — see Flask-Babel section below. |
| `current_user` | `user_menu.html`, `shared/profile.html` | dict/object with required header fields `name`, `role_label`, `avatar_url` (nullable); My Profile additionally consumes optional `email`, `institution`, `profile_role_label`, `status_label`, `status_variant` | Should come from the authenticated session once login exists. `profile_role_label` lets the profile display the full role name without changing an already-approved shorter header label. |
| `profile_role` | `shared/profile.html` | `'operator'` \| `'supervisor'` \| `'admin'` | Selects which existing role sidebar the single shared profile template includes. |
| `dashboard_stats` | `operator/dashboard.html` | dict: `sessions_today`, `active_sessions`, `closed_sessions`, `open_discrepancies` (ints) | Falls back to demo numbers matching the Figma reference. |
| `sessions_by_day` | `operator/dashboard.html` | list of `{label: str, count: int}` | Powers the "Sessions by Day" bar chart. |
| `session_status_breakdown` | `operator/dashboard.html` | list of `{label: str, value: int, variant: 'info'\|'success'\|'danger'\|'warning'}` | Powers the "Session Status" progress bars. `variant` selects the bar color. |
| `recent_sessions` | `operator/dashboard.html` | list of `{session_id, procedure_label, kit_name, operator_name, status_label, status_variant, action_label, action_url}` | `status_variant` maps to a `status_badge` color (`info`, `success`, `pending`, `danger`, `neutral`). `action_label`/`action_url` let the backend decide "Continue" vs "View Details" instead of the template guessing from status. |
| `active_nav_item` | `sidebar_operator.html` | string, one of `dashboard`, `counting_sessions`, `new_session`, `session_history`, `profile` | Set by the page template right before including the sidebar (see `operator/dashboard.html`). |
| `nav_urls` | `sidebar_operator.html` | dict, optional keys `dashboard`, `counting_sessions`, `new_session`, `session_history`, `profile`, `sign_out` → URL string | Every key independently falls back to `"#"` via `|default('#')` when absent, so omitting `nav_urls` entirely (current production behavior) is safe. The preview harness passes real preview routes here so the sidebar can be clicked through; the production app can eventually pass real `url_for(...)` results the same way. |
| `sessions` | `operator/sessions/list.html` | list of `{session_id, procedure_name, operating_room, kit_name, operator_name, capture_station_name, started_at, status_label, status_variant, action_label, action_url}` | The Operator's operational queue — Active / Pending Validation / With Discrepancies. Closed sessions belong in `session_history` instead. |
| `sessions_shown_count`, `sessions_total_count`, `current_page`, `total_pages`, `previous_url`, `next_url` | `operator/sessions/list.html`, `operator/sessions/history.html` | ints / URL strings | Back the "Showing X of Y sessions" / "Previous · Page X of Y · Next" pagination row. Static in this iteration — no server-side pagination is implemented yet. |
| `session_history` | `operator/sessions/history.html` | list of `{session_id, procedure_name, operating_room, kit_name, operator_name, started_at, closed_at (nullable), status_label, status_variant, action_label, action_url}` | `closed_at` is expected to be `None`/empty for cancelled sessions; the template renders an em dash in that case. |
| `current_user` | `operator/sessions/new.html` | same shape as above | Used to pre-fill the read-only "Operator" field. |
| `operations` | `operator/sessions/new.html` | list of `{operation_id, procedure_name, patient_name, physician_name, operating_room_name}` | Populates the Operation / Procedure `<select>`. Each `<option>` also carries `data-patient-name`, `data-physician-name`, and `data-operating-room-name` attributes, which `static/js/pages/new-counting-session.js` reads on change to update the read-only fields below — no data is duplicated into JavaScript itself. |
| `selected_operation` | `operator/sessions/new.html` | one item from `operations` | Determines the initially selected `<option>` and the initial values of the read-only Patient / Physician / Operating Room fields on first render. |
| `available_kits`, `selected_kit` | `operator/sessions/new.html` | list of `{kit_id, kit_name}` / one item from that list | Populates the Kit `<select>`. Changing it does not currently update Expected Inventory Preview (out of scope for this iteration — only Operation → Patient/Physician/Operating Room is wired). |
| `available_capture_stations`, `selected_capture_station` | `operator/sessions/new.html` | list of `{station_id, station_name}` / one item from that list | Populates the Capture Station `<select>`. |
| `expected_inventory` | `operator/sessions/new.html` | list of `{instrument_family_name, expected_quantity}` | Renders the Expected Inventory Preview table for the selected kit. |
| `instrument_readiness` | `operator/sessions/new.html` | dict: `expected_count`, `available_count`, `missing_count` (ints), `missing_variant` (badge/stat variant for the Missing stat card), `readiness_variant` (`'success'` or `'warning'`), `ready` (bool, selects the check-circle vs. alert-triangle icon only), `status_label` (e.g. "Instrument Set Ready") | All presentation-ready — the template does not infer variants/labels from the raw counts itself. |
| `start_session_url` | `operator/sessions/new.html` | URL string, defaults to `"#"` | The Start Session button's `<form action>`. Real behavior should create a `WorkSession` and redirect to Active Counting Session. |
| `feedback_banner` | any page (rendered by `layouts/authenticated.html` via `partials/feedback_banner.html`) | dict: `variant` (`'success'`\|`'warning'`\|`'danger'`\|`'info'`, defaults `'success'`), `title`, `description` (optional) | One-off, single-render feedback (e.g. "Review submitted successfully." / "Session WS-026 has been sent to the Supervisor for review."). Distinct from Flask's `flash()` — not persisted across a redirect by itself; the view decides when to pass it (the preview harness uses a `?feedback=...` query flag). |
| `session` | `operator/sessions/active.html`, `validation.html`, `closed_details.html` | dict: `session_id`, `procedure_name`, `operating_room`, `patient_name`, `physician_name`, `kit_name`, `operator_name`, `status_label`, `status_variant`, plus `capture_station_name` (active/closed_details) and `started_at`/`closed_at` (closed_details only) | Field set matches what each page actually displays per its approved Figma reference (Count Validation's summary row does not show Capture Station, for example). |
| `instrument_counts` | `operator/sessions/active.html` | list of `{instrument_name, expected_quantity, counted_quantity, difference, status_label, status_variant}` | Backs the live +/- Instrument Count table. `static/js/pages/counting-session.js` updates `counted_quantity`/`difference`/row highlight client-side for preview only — the backend remains the source of truth and must validate/persist the real counts. |
| `session_summary` | `operator/sessions/active.html` | dict: `expected_total`, `counted_total`, `discrepancy_count` (ints), `discrepancy_variant` (badge variant) | Presentation-ready totals row above Current Alerts; not derived from `instrument_counts` in the template. |
| `current_alerts` | `operator/sessions/active.html` | list of `{variant, title, description}` | Rendered via `alert_banner()`, one box per entry. Figma shows exactly one; the shape supports more without template changes. |
| `cancel_session_url`, `save_progress_url`, `validate_count_url` | `operator/sessions/active.html` | URL strings, default `"#"` | Cancel Session / Save Progress / Validate Count targets in the sticky action bar. |
| `has_discrepancy` | `operator/sessions/validation.html` | bool, default `false` | The single switch driving both the No Discrepancy and Discrepancy presentation states from one template. |
| `pre_post_comparison` | `operator/sessions/validation.html` | dict: `expected_total`, `counted_total`, `difference` (ints), `status_label`, `status_variant` (`'success'` or `'warning'`) | Backs the PRE vs POST Comparison card, including its top-right status pill. |
| `count_comparison` | `operator/sessions/validation.html` | list of `{instrument_name, expected_quantity, counted_quantity, difference, status_label, status_variant}` | Backs the Count Comparison table (`status_label` is "Match" or "Discrepancy", shown as a badge). |
| `discrepancy_reasons`, `selected_discrepancy_reason` | `operator/sessions/validation.html` (discrepancy state only) | list of `{value, label}` / a `value` from that list | Populates the "Reason for Discrepancy" select. Verification Notes has no value binding yet (empty textarea, placeholder only). |
| `discrepancy_summary` | `operator/sessions/validation.html` (discrepancy state) → passed into `components/modals/supervisor_review_required.html` | dict: `session_id`, `instrument_name`, `expected_quantity`, `counted_quantity` | The compact "Session / Discrepancy / Expected / Counted" box inside the Supervisor Review Required modal. |
| `edit_count_url`, `save_review_url` | `operator/sessions/validation.html` | URL strings, default `"#"` | Edit Count (conceptually returns to Active Counting Session) and Save Review targets. |
| `close_session_url` | `operator/sessions/validation.html` (no-discrepancy) → `components/modals/close_session.html` | URL string, default `"#"` | The Close Session modal's primary action; should finalize the validated count and close the `WorkSession`. |
| `submit_for_review_url` | `operator/sessions/validation.html` (discrepancy) → `components/modals/supervisor_review_required.html` | URL string, default `"#"` | The Supervisor Review Required modal's primary action; should route the session/discrepancy to Supervisor review (Pending Review). |
| `pre_procedure_status`, `post_procedure_status` | `operator/sessions/closed_details.html` | dict: `expected_count` / `validated_count` (int), `status_label`, `status_variant` | Backs the two Pre/Post Procedure Status sub-panels. |
| `closure_summary` | `operator/sessions/closed_details.html` | dict: `variant`, `message` | The single-line "All Instruments Accounted For" banner, rendered via `alert_banner()` with no description. |
| `validated_count` | `operator/sessions/closed_details.html` | same shape as `count_comparison` | Read-only — this page never shows +/- controls or an Edit Count action. |
| `activity_log` | `operator/sessions/closed_details.html` | list of `{timestamp, event_label, actor_name, details}` | Session Activity Log table. Presentation-ready; the template does not infer or reconstruct events — the backend/audit log is the source of truth. |
| `back_to_sessions_url`, `export_summary_url` | `operator/sessions/closed_details.html` | URL strings, default `"#"` | Back to Sessions / Export Summary targets. Export Summary does not generate a real file in this iteration. |

### Supervisor CDE / Quality context contracts

Same conventions as above: every variable is consumed with a safe `|default(...)` so every
Supervisor template renders sensibly before a real view supplies data, and every table row
already carries presentation-ready `status_label` / `status_variant` / `action_label` /
`action_url` fields rather than making the template infer them.

| Variable | Used in | Shape | Notes |
|---|---|---|---|
| `current_user` | every Supervisor page (via `partials/user_menu.html`) | same shape as Operator's | Preview fallback is `{'name': 'Sophia Turner', 'role_label': 'Supervisor CDE / Quality', 'avatar_url': None}`. Falls back to initials ("ST") since no real avatar photo is available (same as Operator). |
| `nav_urls` | `partials/sidebar_supervisor.html` | dict, optional keys `dashboard`, `sessions`, `discrepancies`, `session_history`, `reports`, `indicators`, `audit_log`, `profile`, `sign_out` → URL string | Same `|default('#')`-per-key pattern as `sidebar_operator.html`'s `nav_urls`. |
| `active_nav_item` | `partials/sidebar_supervisor.html` | string, one of `dashboard`, `sessions`, `discrepancies`, `session_history`, `reports`, `indicators`, `audit_log` | Set by the page template right before including the sidebar. Session Details defaults to `'sessions'` (opened from Sessions monitoring) but accepts an override so a future "opened from Discrepancies" flow can pass `'discrepancies'` instead (section 7 of the implementation prompt). |
| `dashboard_stats` | `supervisor/dashboard.html` | dict: `sessions_today`, `active_sessions`, `pending_reviews`, `open_discrepancies` (ints) | Note the Supervisor Dashboard's third stat is `pending_reviews`, not Operator's `closed_sessions`. |
| `sessions_by_day` | `supervisor/dashboard.html`, `supervisor/indicators.html` | list of `{label: str, count: int}` | Same shape as Operator's dashboard chart; reused as-is by Indicators. |
| `review_status` | `supervisor/dashboard.html` | list of `{label: str, value: int, variant: 'info'\|'success'\|'danger'\|'warning'\|'primary'\|'neutral'}` | Powers the "Review Status" progress bars (Pending Review / With Discrepancies / Closed Today). |
| `sessions_requiring_attention` | `supervisor/dashboard.html` | list of `{session_id, procedure_label, kit_name, operator_name, issue_label, submitted_at, status_label, status_variant, action_label, action_url}` | `action_label` of exactly `"Review"` renders the accent-outlined button (`.btn--accent`); anything else renders the neutral `.btn--secondary` — the template does not otherwise infer styling from status. |
| `sessions` | `supervisor/sessions/list.html` | list of `{session_id, procedure_name, operating_room, kit_name, operator_name, capture_station_name, started_at, status_label, status_variant, action_label, action_url}` | Same shape as Operator's Counting Sessions, but this is a read-only monitoring view: no "New Session" page action and no Operator-only row actions (`Continue`, `Edit Count`, `Validate Count`) are ever rendered — only `Review` / `View Details`. |
| `sessions_shown_count`, `sessions_total_count`, `current_page`, `total_pages`, `previous_url`, `next_url` | `supervisor/sessions/list.html`, `supervisor/sessions/history.html` | ints / URL strings | Same pagination contract as Operator's list/history pages. |
| `session_history` | `supervisor/sessions/history.html` | list of `{session_id, procedure_name, operating_room, kit_name, operator_name, started_at, closed_at (nullable), status_label, status_variant, action_label, action_url}` | Every row's `action_label` is always `"View Details"` — Session History never exposes a `Review` action. |
| `session` | `supervisor/sessions/details.html` | dict: `session_id`, `procedure_name`, `operating_room`, `patient_name`, `physician_name`, `kit_name`, `operator_name`, `capture_station_name`, `status_label`, `status_variant` | Backs both the page's summary header and its 3-column, stacked-label/value info grid. |
| `pre_post_status` | `supervisor/sessions/details.html` | dict: `expected_count`, `counted_count`, `difference` (ints), `difference_variant` (`'warning'` or `'danger'`, drives both the header note color and the Difference stat card's tint) | Backs "PRE/POST Procedure Status". |
| `count_summary` | `supervisor/sessions/details.html` | list of `{instrument_name, expected_quantity, counted_quantity, difference, status_label, status_variant}` | Backs "Count Summary". Status is rendered as plain colored text here (no badge pill), matching the approved reference. |
| `correction_log` | `supervisor/sessions/details.html` | list of `{timestamp, instrument_name, previous_quantity, corrected_quantity, reason}` | Backs "Corrections Log". |
| `discrepancy_details` | `supervisor/sessions/details.html` | dict: `instrument_name`, `expected_quantity`, `counted_quantity`, `difference`, `reason`, `status_label`, `status_variant`, `reviewed_by` | Backs "Discrepancy Details". This section's exact Figma content is cropped out of the approved reference — see "Anything that could not be determined from Figma" below. |
| `activity_log` | `supervisor/sessions/details.html` | list of `{timestamp, event_label, actor_name, details}` | Backs "Session Activity" — same shape as Operator's Closed Session Details activity log. |
| `back_to_sessions_url` | `supervisor/sessions/details.html` | URL string, default `"#"` | The one action this read-only page exposes, in the sticky action bar. |
| `discrepancies_summary` | `supervisor/discrepancies/list.html` | dict: `pending_reviews`, `open_discrepancies`, `reviewed_today` (ints) | Powers the three KPI cards. Stays constant across the default / Post Approval / Post Correction states in the approved references (only the table rows and the feedback banner change). |
| `discrepancies` | `supervisor/discrepancies/list.html` | list of `{session_id, procedure_name, operating_room, kit_name, operator_name, instrument_name, expected_quantity, counted_quantity, difference, reason, status_label, status_variant, action_label, action_url}` | The exact columns from section 14 of the implementation prompt. This ONE template renders the default listing, the Post Approval state, and the Post Correction state purely from this list plus `feedback_banner` — see "Result state architecture" below. |
| `feedback_banner` | `supervisor/discrepancies/list.html` (rendered by `layouts/authenticated.html`) | same shape as Operator's | Post Approval passes `{'variant': 'success', 'title': 'Review approved successfully.', 'description': 'Session WS-026 has been approved and may proceed to closure.'}`; Post Correction passes the warning-variant equivalent. |
| `session` | `supervisor/discrepancies/review.html` | dict: `session_id`, `procedure_name`, `operating_room`, `patient_name`, `physician_name`, `kit_name`, `operator_name`, `capture_station_name`, `submitted_at`, `status_label`, `status_variant` | Backs "Session Information" (a 3-column `info-grid--three`, filled in row-major order — see the approved reference's exact field placement). |
| `pre_post_comparison` | `supervisor/discrepancies/review.html` | dict: `expected_total`, `counted_total`, `difference` (ints), `status_label`, `status_variant` | Same shape as Operator's Count Validation `pre_post_comparison`, but rendered as three stat cards (Pre-Procedure / Post-Procedure / Difference, each with a description line) plus a one-line `.alert` banner below them, per the approved Discrepancy Review reference (Operator's equivalent page renders this as a badge instead). |
| `count_comparison` | `supervisor/discrepancies/review.html` | same shape as Operator's `count_comparison` | Read-only: no increment/decrement controls and no Edit Count action anywhere on this page (section 19 — the Supervisor must never modify instrument counts). |
| `operator_submission` | `supervisor/discrepancies/review.html` | dict: `reason`, `verification_notes` | Rendered as plain read-only text (not a `<select>`/`<textarea>`) since these values belong to the Operator's original submission, per section 20. |
| `approve_review_url`, `request_correction_url` | `supervisor/discrepancies/review.html` → `components/modals/approve_review.html` / `request_correction.html` | URL strings, default `"#"` | See "Modal integration points" below. |
| `reports` | `supervisor/reports.html` | list of `{icon, title, description, last_generated_label, export_url, view_report_url}` | `icon` is one of `'document'`, `'alert_triangle'`, `'clipboard'`, `'user'` — the template maps it to the matching icon macro rather than embedding markup in the context. `Export` / `View Report` are presentation-only (section 33): no report detail pages or file generation exist. |
| `indicator_stats` | `supervisor/indicators.html` | dict: `total_sessions`, `closed_sessions`, `sessions_with_discrepancies`, `pending_reviews` (ints) | Powers the four KPI cards. |
| `discrepancies_by_reason` | `supervisor/indicators.html` | list of `{label, value, variant}` | Powers the "Discrepancies by Reason" progress bars, reusing the same `status-progress-item` component as `review_status`. |
| `session_status_chart` | `supervisor/indicators.html` | dict: `total_label`, `segments`: list of `{label, percent, variant}` | Powers the "Session Status" donut chart. `percent` values are expected to already sum to (approximately) 100 — the template accumulates a running offset purely to place each segment's `conic-gradient` stop (the same category of presentational arithmetic as the existing bar chart's fill-percentage calculation), it does not derive the percentages themselves. |
| `discrepancies_by_kit` | `supervisor/indicators.html` | list of `{label, count}` | Powers the "Discrepancies by Kit" bar chart (colored via `.bar-chart__bar--danger`). |
| `audit_log_entries` | `supervisor/audit_log.html` | list of `{timestamp, user_name, role_label, event_label, event_variant, resource_id, details}` | `event_variant` is `'danger'`, `'success'`, or `'default'` — the template colors the Event cell accordingly and never infers it from `event_label` text. Strictly read-only: no Edit/Delete/Modify actions and no Action column at all. |
| `events_shown_count`, `events_total_count`, `current_page`, `total_pages`, `previous_url`, `next_url` | `supervisor/audit_log.html` | ints / URL strings | Same pagination contract as every other paginated Supervisor list. |

#### Result state architecture (Discrepancies)

Per section 28 of the implementation prompt, `supervisor/discrepancies/list.html` is the
**only** discrepancies template. The default listing, the Post Approval state, and the Post
Correction state are three different renders of that same template, distinguished purely by
the `discrepancies` rows and the optional `feedback_banner` passed in — nothing in the
template branches on a "which state is this" flag. In the preview harness,
`/preview/supervisor/discrepancies/post-approval` and `.../post-correction` are thin
redirects to `/preview/supervisor/discrepancies?feedback=review_approved` (or
`correction_requested`), matching the real intended flow: the Approve Review / Request
Correction modals' primary actions are plain links to the Discrepancies URL with that same
query flag.

`get_flashed_messages` (Flask's own Jinja global) is used defensively via
`get_flashed_messages is defined` in `flash_messages.html`, since it is only registered when
Jinja is rendered through Flask.

### IT Administrator context contracts

Same conventions as above: every variable is consumed with a safe `|default(...)`, and every
list row already carries presentation-ready `status_label` / `status_variant` / `edit_url`
fields. Every New/Edit form pair (Instrument Family, Instrument, Kit, User, Capture Station)
is driven by **one** template distinguished by a `form_mode` value of `"create"` or `"edit"`
— never two near-duplicate templates — per section 36 of the implementation prompt.

| Variable | Used in | Shape | Notes |
|---|---|---|---|
| `current_user` | every Administrator page (via `partials/user_menu.html`) | same shape as Operator's/Supervisor's | Preview fallback is `{'name': 'Daniel Brooks', 'role_label': 'IT Administrator', 'avatar_url': None}`. |
| `nav_urls` | `partials/sidebar_admin.html` | dict, optional keys `dashboard`, `instrument_families`, `instruments`, `kits`, `users`, `roles`, `configuration`, `profile`, `sign_out` → URL string | Same `\|default('#')`-per-key pattern as every other role's `nav_urls`. |
| `active_nav_item` | `partials/sidebar_admin.html` | string, one of `dashboard`, `instrument_families`, `instruments`, `kits`, `users`, `roles`, `configuration` | Set by the page template right before including the sidebar. New/Edit/modal pages for a given section keep that section's key active (e.g. New Instrument Family keeps `instrument_families` active), per section 7 of the implementation prompt. |
| `dashboard_stats` | `admin/dashboard.html` | dict: `active_users`, `instrument_families`, `registered_instruments`, `active_kits` (ints) | Deliberately does **not** include Operator/Supervisor operational metrics (sessions, discrepancies) or any infrastructure/YOLO monitoring metric — out of scope per section 9. |
| `catalog_overview`, `system_overview` | `admin/dashboard.html` | list of `{label: str, value: str \| int}` | Rendered as plain label/value rows (a borderless `data-table`) — the template does not compute these from other catalog data (section 10: no Jinja-side calculation). |
| `instrument_families` | `admin/instrument_families/list.html` | list of `{code, name, category, function, status_label, status_variant, edit_url}` | |
| `families_shown_count`, `families_total_count`, `current_page`, `total_pages`, `previous_url`, `next_url` | `admin/instrument_families/list.html` | ints / URL strings | Same pagination contract as every other paginated list in the app. |
| `new_instrument_family_url` | `admin/instrument_families/list.html` | URL string, default `"#"` | "+ New Instrument Family" target. |
| `form_mode`, `instrument_family`, `categories`, `save_url`, `cancel_url` | `admin/instrument_families/form.html` | `form_mode`: `"create"` \| `"edit"`; `instrument_family`: dict `code, name, category, how_to_identify, classification_characteristics, function, status`; `categories`: list of `{value, label}` | `code` renders read-only only when `form_mode == "edit"`. `how_to_identify` and `classification_characteristics` reuse the existing `.form-field__textarea` component (the approved Edit reference shows these wrapping onto two lines; New shows them at rest — both are the same textarea, just empty). |
| `instruments` | `admin/instruments/list.html` | list of `{internal_code, instrument_family_name, cycle_status_label, cycle_status_variant, active_status_label, active_status_variant, edit_url}` | Cycle Status (`Available`/`In Use`/`Sterilization`/`Unavailable` → `info`/`neutral`/`warning`/`danger`) and Active Status (`Active`/`Inactive` → `success`/`neutral`) are always two separate badges — never merged or visually confused, per section 15. |
| `instrument_families_filter` | `admin/instruments/list.html` | list of `{value, label}` | Populates the "Family: All" filter select. |
| `new_instrument_url` | `admin/instruments/list.html` | URL string, default `"#"` | "+ New Instrument" target. |
| `form_mode`, `instrument`, `instrument_families`, `save_url`, `cancel_url` | `admin/instruments/form.html` | `instrument`: dict `internal_code, instrument_family, cycle_status, active_status`; `instrument_families`: list of `{value, label}` | `internal_code` renders read-only only when `form_mode == "edit"`; New mode shows a backend-suggested but still editable value (matching the approved New Instrument reference, e.g. `INS-005`). |
| `kits` | `admin/kits/list.html` | list of `{name, version, instrument_types, total_expected_instruments, status_label, status_variant, edit_url}` | The term "Kit" is used everywhere — never "Tray" (section 17). |
| `new_kit_url` | `admin/kits/list.html` | URL string, default `"#"` | "+ New Kit" target. |
| `form_mode`, `kit`, `kit_composition`, `instrument_families`, `save_url`, `cancel_url` | `admin/kits/form.html` | `kit`: dict `name, version, status`; `kit_composition`: list of `{instrument_family_value, instrument_family_label, expected_quantity}`; `instrument_families`: list of `{value, label}` for each row's select | `version` always renders read-only/system-controlled (both New and Edit — the approved New Kit reference already shows it pre-filled and greyed at `1`, ahead of the section 18 requirement that only mandates this for Edit). The Instrument Types / Total Expected Instruments summary tiles are rendered once from `kit_composition` server-side, then kept in sync client-side by `kit-form.js` as rows are added/removed — the backend remains the source of truth for the persisted composition. |
| `users` | `admin/users/list.html` | list of `{name, email, institution, role_label, status_label, status_variant, edit_url}` | Demo rows reuse the same three identities already established across every role's preview (Alex Morgan / Operator CDE, Sophia Turner / Supervisor CDE / Quality, Daniel Brooks / IT Administrator), all at `@institution.edu` (section 20). |
| `new_user_url` | `admin/users/list.html` | URL string, default `"#"` | "+ New User" target. |
| `form_mode`, `user`, `institutions`, `roles`, `save_url`, `cancel_url` | `admin/users/form.html` (both modes) | `user`: dict `name, email, institution, role, status` (`role_label` also expected in edit mode, for the Deactivate User modal's user box); `institutions`, `roles`: lists of `{value, label}` | |
| `change_password_url`, `deactivate_user_url` | `admin/users/form.html` (edit mode only) → `components/modals/change_password.html` / `deactivate_user.html` | URL strings, default `"#"` | Feed the two modals' primary actions — see "Modal integration points" below. Edit User never receives or displays the user's existing password/hash. |
| `roles` | `admin/roles/list.html` | list of `{code, name, description, assigned_users, status_label, status_variant, edit_url}` | `description` is truncated with an ellipsis (`.data-table__cell--truncate`) rather than wrapping, matching the approved reference; it is never concatenated with `assigned_users` (section 27). |
| `role`, `save_url`, `cancel_url` | `admin/roles/edit.html` | `role`: dict `code, name, description, status, assigned_users` | `code` and `assigned_users` are always read-only; there is no permissions matrix or granular permission control anywhere on this page (section 28). No New Role route/template exists — no approved Figma reference for it was provided. |
| `institution`, `capture_stations` | `admin/configuration/index.html` | `institution`: dict `name, code, status_label, status_variant`; `capture_stations`: list of `{code, name, status_label, status_variant, edit_url}` | Configuration keeps ONLY these two sections — no General Settings, Session Timeout, AI/YOLO, GPU, database, or cloud configuration exists anywhere (section 29). |
| `edit_institution_url`, `new_capture_station_url` | `admin/configuration/index.html` | URL strings, default `"#"` | "Edit" (Institution Information) and "+ New Capture Station" targets. |
| `institution`, `save_url`, `cancel_url` | `admin/configuration/institution_form.html` | dict: `name, code, status` | `code` is always read-only (treated as the institution's stable identifier, section 31). No address/contact fields exist — none were shown in the approved reference. |
| `form_mode`, `capture_station`, `save_url`, `cancel_url` | `admin/configuration/capture_station_form.html` | dict: `code, name, status` | `code` renders read-only only when `form_mode == "edit"` — see "Anything that could not be determined from Figma" below for why New mode does not copy the read-only styling shown in that particular reference frame. |

#### Modal integration points (IT Administrator)

| Modal | Primary action | Context URL | Backend should |
|---|---|---|---|
| Change User Password | Change Password | `change_password_url` | Validate password policy if applicable, hash and store the new password, and never expose the previous password/hash — then return to Edit User (the preview shows a one-off "Password changed successfully." banner via `?feedback=password_changed`). Never requests the Administrator's own or the target user's current password, since this is an Administrator resetting another user's credential. |
| Deactivate User? | Deactivate User | `deactivate_user_url` | Set the user's status to Inactive and revoke session access while preserving every historical record tied to that user — then redirect to Users (the preview shows a one-off warning banner via `?feedback=user_deactivated`). This is explicitly not a delete action — see the Deactivation Principle note below. |

Both modals reuse the exact same `.modal-overlay` / `.modal-dialog` / `data-modal-open` /
`data-modal-close` structure and the existing generic `static/js/modal.js` as every other
modal in the app — no modal-specific JavaScript exists for opening or closing either one.

#### Deactivation principle

Per section 34 of the implementation prompt, every Administrator interface prefers
Active/Inactive status over permanent deletion — Users, Instrument Families, Instruments,
Kits, Roles, and Capture Stations all expose Deactivate/Inactive concepts (or, for catalogs, a
plain Active/Inactive `status` field) instead of a "Delete" action, so historical references
stay conceptually preservable. No template in `admin/` renders a Delete action anywhere.

#### Password responsibility (IT Administrator)

Explicit split, per section 47 of the implementation prompt:

- **Frontend** — New User collects Password + Confirm Password; `user-form.js` performs an
  optional, purely visual match check between them; Edit User never displays or round-trips
  a password/hash; Change User Password collects New Password + Confirm New Password only
  (never the current password, since this flow is an Administrator resetting someone else's
  credential). Nothing here ever stores, hashes, or transmits a plaintext password beyond the
  normal form submission.
- **Backend** — validates password policy if applicable, hashes the password securely, stores
  only the hash, and authenticates Sign In. Not implemented anywhere in this iteration.

## Shared My Profile

`templates/shared/profile.html` is one read-only account page reused by Operator CDE,
Supervisor CDE / Quality, and IT Administrator. The view receives `profile_role` to select
the appropriate existing sidebar and uses `current_user` for account data. It intentionally
does **not** provide self-service role changes, account activation/deactivation, or password
management; those remain administrator/backend responsibilities. The page summarizes the
current language and Light/Dark/System preference, while the actual controls remain in the
shared top header. `static/js/pages/profile.js` only mirrors the current theme preference
into that summary and performs no persistence beyond the existing `theme.js` behavior.

## Backend integration points (not implemented here, by design)

- **Static/URL generation** — All CSS/JS references use `url_for('static', filename=...)`,
  which assumes Flask's default static folder configuration (`static_folder='static'`,
  `static_url_path='/static'`). No route/view exists yet in `app.py`, so nothing in
  `templates/`/`static/` can actually be rendered or exercised in a browser until the
  backend team wires up the Flask app, a Jinja `_()`/Babel global, and at least one view
  per page (e.g. `GET /sign-in`, `GET /operator/dashboard`).
- **Authentication** — `auth/sign_in.html`'s form has `action="#"` and no CSRF token. The
  backend owns the real action URL, method, CSRF protection, and validation/error display
  (which should render into `flash_messages.html` or inline form errors).
- **Current user / session** — `current_user` in `partials/user_menu.html` needs to come
  from whatever session/auth mechanism the backend implements (Flask-Login or similar),
  exposing at least `name`, `role_label`, and optionally `avatar_url`.
- **Sign out** — The sidebar's "Sign Out" link (`href="#"`) needs a real route.
- **Navigation routes** — Every sidebar link, the "New Counting Session" buttons, and every
  table row's action link use placeholder `href="#"` values (or a context-provided
  `action_url` that itself defaults to `"#"`). Intended Flask endpoint names (for the backend
  team to create and for the frontend to later fill in via `url_for(...)` — or pass through
  `nav_urls`, see above):
  - `operator.dashboard` → Operator Dashboard (implemented)
  - `operator.counting_sessions` → Counting Sessions list (implemented — this iteration)
  - `operator.new_session` → New Counting Session (implemented — this iteration)
  - `operator.session_history` → Session History (implemented — this iteration)
  - `operator.profile` → shared My Profile page (`templates/shared/profile.html`)
  - `auth.sign_out` → Sign out action (not yet implemented)
  - `operator.active_session` → Active Counting Session (implemented)
  - `operator.count_validation` → Count Validation, both states (implemented)
  - `operator.closed_session_details` → Closed Session Details (implemented)
  - A session details/review route for each `session.action_url` in Counting Sessions and
    Session History should resolve to the routes above (e.g. Active for `Continue`, Count
    Validation for `Review`, Closed Session Details for `View Details` on a closed row).
- **Dashboard data** — `dashboard_stats`, `sessions_by_day`, `session_status_breakdown`, and
  `recent_sessions` are demo data (clearly marked in `operator/dashboard.html`). A real view
  should query `WorkSession`/`CountEvent`/`Discrepancy` (and friends) and pass these same
  shapes in.
- **Counting Sessions / Session History filters** — The search input and Status/Kit/Date
  `<select>` controls in both pages are presentation-only: they submit a plain `GET` to `"#"`
  and nothing reads their values yet. No client-side filtering is implemented either. The
  backend should either handle the submitted query string server-side, or the frontend can
  add client-side filtering in a later iteration if desired.
- **New Counting Session — Start Session** — The button in the sticky action bar submits the
  `#new-counting-session-form` form (`action="#"`, method `post`) via HTML5's `form="..."`
  attribute (the button itself lives outside the `<form>`, in the shared action bar). Real
  behavior should create a `WorkSession` / session state and then navigate to Active Counting
  Session — neither is implemented here. No CSRF token is present yet.
- **New Counting Session — operation-dependent fields** — Only Operation → Patient/Physician
  /Operating Room is wired (via `static/js/pages/new-counting-session.js` reading `data-*`
  attributes already rendered server-side on each `<option>`). Kit → Expected Inventory
  Preview is **not** reactive in this iteration; changing the Kit selection does not update
  the table below it. If that becomes required, prefer the same data-attribute pattern
  rather than embedding inventory data in JavaScript.
- **Notifications** — The header bell is a static, non-interactive icon button
  (`aria-label` only). No dropdown, count badge, or data source has been built, since no
  notifications UI has been approved in Figma yet.
- **User menu dropdown** — Similarly, the user menu (avatar/name/role/chevron) is a visual
  affordance only; it does not open a menu. A profile/account dropdown can be added once
  approved in Figma.
- **Active Counting Session — counting** — `static/js/pages/counting-session.js` lets the
  Operator click +/- to change a row's displayed counted quantity, difference, and
  discrepancy highlight. This is preview/demo behavior only: nothing is saved, no API is
  called, and the totals row (`session_summary`) and Current Alerts (`current_alerts`) do
  **not** recompute from it. The real capture/YOLO pipeline and backend remain the source of
  truth for actual counts; when connected, the count fields should be re-rendered from the
  server (e.g. via WebSocket-driven page updates), not left to this local JS state.
- **Active Counting Session — actions** — Cancel Session, Save Progress, and Validate Count
  are plain links to context-provided URLs (defaulting to `"#"`). None of them currently
  persist anything; "Cancel Session" should eventually cancel the `WorkSession`, "Save
  Progress" should persist the in-progress count, and "Validate Count" should navigate to
  Count Validation only after the backend has recorded the operator's submitted count.
- **Count Validation — Confirm Validation** — Opens one of two modals client-side based on
  `has_discrepancy` (`data-modal-open` targets `close-session-modal` or
  `supervisor-review-required-modal`); no form is submitted at that point. The modals'
  primary actions (`close_session_url` / `submit_for_review_url`) are the real integration
  points — see "Modal integration points" below.
- **Count Validation — Discrepancy Review** — `discrepancy_reasons` populates a plain
  `<select>` and Verification Notes is a plain `<textarea>` with no name-bound persisted
  value; neither is submitted anywhere in this iteration. "Save Review" is a link to
  `save_review_url` (defaults to `"#"`), not a form submission — the backend should decide
  whether Reason/Notes need to be sent along with it once real persistence exists.
- **Closed Session Details — Export Summary** — Does not generate a real file in this
  iteration (per scope); `export_summary_url` is a plain link defaulting to `"#"`. Real
  behavior should stream/download a generated summary document.
- **Supervisor Sessions / Discrepancies / Session History — filters** — Same as Operator's:
  every search input and `<select>` submits a plain `GET` to `"#"` and nothing reads their
  values yet; no client-side filtering either.
- **Supervisor Discrepancy Review — Supervisor Notes** — A plain `<textarea>` with no
  name-bound persisted value (same pattern as Operator's Verification Notes); nothing is
  submitted anywhere in this iteration.
- **Supervisor Discrepancy Review — decision actions** — Opens one of two modals client-side
  (`data-modal-open` targets `approve-review-modal` or `request-correction-modal`); no form
  is submitted at that point. The modals' primary actions (`approve_review_url` /
  `request_correction_url`) are the real integration points — see "Modal integration points"
  below.
- **Supervisor Reports — View Report / Export** — Presentation-only in this iteration
  (section 33 of the implementation prompt): both are plain links to context-provided URLs
  (defaulting to `"#"`); no report detail pages exist and no file is generated.
- **Supervisor Session Details / Audit Log** — Fully read-only: no editing controls, no
  Edit/Delete/Modify actions, and Audit Log has no Action column at all.
- **IT Administrator catalogs (Instrument Families / Instruments / Kits) — filters** — Same
  pattern as every other list in the app: search inputs and `<select>` filters submit a plain
  `GET` to `"#"` and nothing reads their values; no client-side filtering is implemented.
- **IT Administrator New/Edit forms — Save** — Every form (Instrument Family, Instrument,
  Kit, User, Role, Institution Information, Capture Station) submits to a context-provided
  `save_url` (default `"#"`); the preview harness intercepts the `POST` purely to redirect
  back to the corresponding list with a `?feedback=...` flag (matching the existing
  Operator/Supervisor PRG pattern) — no create/update logic, validation, or persistence
  exists. Real behavior should validate and persist the submitted fields, enforcing every
  read-only field noted in the context contracts table above (Code/Internal Code/Station
  Code in edit mode, Kit Version always, Role Code and Assigned Users always, Institution
  Code always).
- **IT Administrator Kit form — Kit Composition** — `kit-form.js`'s Add Instrument
  Type/Remove and the live Instrument Types/Total Expected Instruments summary are
  presentation-only (section 19): nothing is validated against real catalog data (e.g. an
  Instrument Family could be selected twice) and nothing is persisted. Real behavior should
  validate and persist the composition as `kit_items` rows tied to the `kit`.
- **IT Administrator Users — New User password** — `user-form.js`'s Password/Confirm
  Password match check is a convenience only; the backend must independently validate any
  password policy and is solely responsible for hashing/storing the credential — see
  "Password responsibility (IT Administrator)" above.
- **IT Administrator Edit User — Change Password / Deactivate User** — Both open their
  respective modal client-side (`data-modal-open`); their primary actions are the real
  integration points — see "Modal integration points (IT Administrator)" above.
- **IT Administrator Roles — Edit Role** — No New Role route/template exists; add one only
  once an approved Figma reference is provided (section 28). There is no permissions
  matrix/granular permission control to wire up — Roles remain a fixed set of
  code/name/description/status/assigned-count fields.
- **IT Administrator Configuration** — Institution Information and Capture Stations are the
  only two sections; do not add General Settings, Session Timeout, AI/YOLO, GPU, database, or
  cloud configuration unless a future approved Figma reference introduces them (section 29).

### Modal integration points

All four modals (`templates/components/modals/close_session.html`,
`supervisor_review_required.html`, `approve_review.html`, `request_correction.html`) are
opened/closed entirely by the existing generic `static/js/modal.js` (`data-modal-open` /
`data-modal-close` / overlay click / Escape) — no modal-specific JavaScript was written for
any of them. Each modal's **secondary** action (`Keep Session Open`, `Back to Validation`,
`Cancel`, `Cancel`) just closes the modal client-side and requires no backend work. Each
modal's **primary** action is a real backend integration point:

| Modal | Primary action | Context URL | Backend should |
|---|---|---|---|
| Close Counting Session? | Close Session | `close_session_url` | Finalize the validated count, close the `WorkSession`, then serve/redirect to Closed Session Details. |
| Supervisor Review Required | Submit for Review | `submit_for_review_url` | Persist the discrepancy + reason/notes, set the session to Pending Review, and make it visible to Supervisor screens — then redirect to Counting Sessions. |
| Approve Discrepancy Review? | Approve Review | `approve_review_url` | Persist the Supervisor's decision, mark the discrepancy Approved, and make the session eligible for closure — then redirect to Discrepancies (Post Approval state). |
| Request Operator Correction? | Request Correction | `request_correction_url` | Persist the Supervisor's note, set the session to Correction Required, and route it back to the Operator — then redirect to Discrepancies (Post Correction state). |

In this preview, every primary action is a plain link to another preview route (with a
`?feedback=...` flag so the destination page can show the matching one-off
`feedback_banner`) — there is no real cross-role data persistence anywhere.

### Frontend presentation vs. backend-required workflow (summary)

To make the boundary explicit: everything below is **presentation-only** and must not be
treated as the system of record —

- the entire Active Counting Session counter UI (`counting-session.js`)
- which modal opens on Confirm Validation / Discrepancy Review's decision actions, and
  closing a modal
- the demo swap of the Counting Sessions list after "Submit for Review" (`?feedback=...` in
  the preview harness) and the "Progress saved" / "Review saved" / "Review submitted"
  banners
- the demo swap of the Discrepancies list into its Post Approval / Post Correction states
  (`?feedback=review_approved` / `?feedback=correction_requested`) and their feedback banners
- the IT Administrator Kit form's Add Instrument Type/Remove rows and its live Instrument
  Types/Total Expected Instruments recompute (`kit-form.js`)
- the IT Administrator New User form's Password/Confirm Password match check (`user-form.js`)
- every IT Administrator "Save"/"Save Changes" action across Instrument Families,
  Instruments, Kits, Users, Roles, Institution Information, and Capture Stations, and the
  matching one-off `feedback_banner` shown after each preview `POST` redirect
- all `?feedback=...` query-string handling in `devtools/frontend_preview.py`

Everything below is a **backend-required workflow/state transition**, not implemented
anywhere in `templates/`/`static/` —

- creating a `WorkSession` (Start Session), persisting in-progress counts (Save Progress),
  cancelling a session (Cancel Session)
- validating and persisting a final count (Validate Count / Confirm Validation)
- closing a session (Close Session) and generating its Closed Session Details record
- routing a discrepancy to Supervisor review (Submit for Review) and enforcing that a
  Pending Review session hides `Continue`/`Close Session` from the Operator (section 28 of
  the implementation prompt — represented here only as static demo data, e.g. WS-026 shows
  `View Details` instead of `Continue` once `?feedback=review_submitted`)
- approving a discrepancy review (Approve Review) or requesting an Operator correction
  (Request Correction), and enforcing every downstream effect: marking the discrepancy
  Approved/Correction Required, making the session eligible for closure or returning it to
  the Operator, and reflecting that in Supervisor Sessions/Discrepancies/Session Details
- generating a real Export Summary file, a real Supervisor Report, or real Indicators
  analytics (the demo KPI/chart values are not derived from live data)
- all authentication, authorization, and audit/activity-log persistence (including writing
  the rows Audit Log displays)
- creating/updating an `instrument_family`, `instrument`, `kit` (and its `kit_items`),
  `user`, `role`, `institution`, or `capture_station` record; hashing and storing a new
  user's or a reset user's password; and deactivating a user (revoking access while
  preserving every historical record tied to them)

## Theme behavior (Light / Dark / System)

- Fully client-side; no backend involvement required.
- Preference is stored in `localStorage` under the key `themePreference` (`"light"`,
  `"dark"`, or `"system"`).
- `base.html` includes a small inline `<script>` at the very top of `<head>`, before any
  stylesheet, that reads the stored preference and sets `data-theme` on `<html>`
  synchronously — this is what avoids a flash of the wrong theme on load.
- `static/js/theme.js` handles the interactive part: applying a newly chosen preference,
  persisting it, listening for `prefers-color-scheme` changes while `"system"` is selected,
  and updating the theme selector's visible label/`aria-selected` state.
- All colors are defined as semantic custom properties in `static/css/tokens.css`
  (`--color-background`, `--color-surface`, `--color-text-primary`, `--color-primary`,
  `--color-success`, `--color-warning`, `--color-danger`, `--color-info`, etc.). Components
  must consume these tokens rather than hard-coded colors. The sidebar's dark-teal palette
  intentionally stays constant across both themes, matching the approved Figma design.

## Planned Flask-Babel integration

- All visible static text is already wrapped in `{{ _("...") }}` throughout every template
  created so far (including inside `{% set %}` demo-data blocks), per the project's
  internationalization plan.
- Flask-Babel itself is **not** installed or configured (`requirements.txt` has no Babel
  dependency yet, and `app.py` is empty). Until it is, rendering any of these templates
  through Flask will raise a Jinja error on `_()` (undefined global) — this is expected and
  intentional at this stage, not a bug to fix from the frontend side.
- The language selector (`partials/language_selector.html`) is visually complete (EN /
  Español (México), dropdown open/close, current-selection highlighting) but selecting an
  option does not currently change anything — there is no JS handler wired to persist a
  locale, by design. Once Babel and a locale-switching mechanism (session key, cookie, or
  route) exist, wire `data-language-code` clicks to that mechanism and pass the resulting
  `current_locale` into the template context.
- Pagination text uses gettext-style parameterized strings, e.g.
  `{{ _("Showing %(shown)s of %(total)s sessions", shown=sessions_shown_count, total=sessions_total_count) }}`.
  This is the standard Flask-Babel/gettext pattern for variable interpolation and needs no
  change once Babel is configured. The preview harness's `_` stand-in was updated to
  interpolate `%(name)s` placeholders the same way real gettext does, purely so these
  strings render correctly during preview — this does not affect production templates.

## Anything that could not be determined from Figma

- No original Figma frame shows the notifications dropdown content or user-menu dropdown
  content, so those affordances remain intentionally inert. A shared read-only My Profile
  page was later added outside the original Figma set, using only the established visual
  system and existing role sidebars.
- Real avatar photos are not available; `user_menu.html` falls back to a generated
  two-letter initials badge when `current_user.avatar_url` is empty.
- Exact chart data (Sessions by Day, Session Status) is not sourced from anywhere yet; the
  demo values reproduce the Figma reference numbers only.
- The approved New Counting Session Figma reference is cropped mid-scroll at "SECTION 3 -
  Instrument Readiness" (its heading is visible, but the sticky Cancel/Start Session footer
  is already visible directly below it in the same screenshot, implying the section's real
  content sits below the fold and was never fully captured). Section 3's content — three
  stat cards (Expected/Available/Missing) plus a status banner reading "Instrument Set
  Ready" — was designed by reusing the existing `stat_card` macro and `.alert` component
  rather than inventing new visual patterns, per the task's own description of the expected
  concepts. If an exact approved visual exists elsewhere, this section should be revisited
  against it.
- The Counting Sessions "With Discrepancies" badge in Figma appears to combine two icons (a
  circular glyph and a warning triangle) inside the pill, rather than the single dot used by
  every other status badge. This was intentionally not special-cased — the shared
  `status_badge` macro (dot + text) was reused as-is for every status across all three pages,
  per the instruction to reuse rather than extend the badge component for one row.
- Active Counting Session's breadcrumb ("Counting Session WS-024") and its own session
  context bar ("Session: WS-026") show two different session IDs in the approved reference —
  an apparent authoring inconsistency in the Figma file. This implementation standardizes on
  **WS-026** throughout `active.html` (breadcrumb, context bar, and its "Validate Count" link
  target) since that ID is what the context bar, the alert message, and the discrepancy
  example all agree on, and because it flows naturally into the Discrepancy validation state.
- The Session Activity Log in Closed Session Details is cropped in its Figma reference right
  at the column header row ("TIMESTAMP / EVENT / USER"), with no visible entries. The four
  demo rows shown here were composed to match that header shape plus the `timestamp` /
  `event_label` / `actor_name` / `details` contract given in the task, reusing the existing
  two-line `data-table__primary`/`__secondary` cell for `details`. If an unambiguous fuller
  reference exists, this table should be checked against it.
- Similarly, Count Validation's Verification Notes `<textarea>` is cropped out of both Count
  Validation Figma references before any placeholder/value is visible; a neutral placeholder
  ("Add any additional notes about this discrepancy...") was used.
- The approved Supervisor Discrepancy Review reference (`supervisor-discrepancy-review.png`)
  is cropped right at the "Operator Submission" heading — its Reason/Verification Notes
  content, and the entire "Supervisor Review" section below it, are not visible in the
  reference. Both sections were composed using the same field contracts already established
  elsewhere (Operator's Discrepancy Reason/Verification Notes on Count Validation, and a
  Supervisor Notes textarea styled identically to it) rather than invented freely. If a
  fuller reference exists, this section should be checked against it.
- The approved Supervisor Session Details reference (`supervisor-session-details.png`) is
  cropped mid-scroll: its "Discrepancy Details" card's content is entirely cut off (only the
  heading is visible) and "Session Activity" is cut off after its third row. "Discrepancy
  Details" was composed from the same discrepancy concept already established by the
  Discrepancies table and Discrepancy Review screen (instrument, expected/counted/
  difference, reason, reviewed by); the "Session Activity" demo rows stop at the same third
  entry visible in the reference rather than inventing further ones. If a fuller reference
  exists, both sections should be checked against it.
- The same Session Details reference shows the session's Kit as "General Surgery Kit" but
  its (partially visible) Session Activity row reads "Delivery Kit loaded" — an apparent
  authoring inconsistency in the Figma file, similar to the WS-024/WS-026 breadcrumb
  mismatch already noted above for Operator's Active Counting Session. This implementation
  reproduces the reference's own values as-is (Kit: General Surgery Kit, activity detail:
  "Delivery Kit loaded") rather than silently reconciling them.
- The Supervisor Discrepancy Review breadcrumb in its approved reference reads "Monitoring /
  Discrepancies / WS-026 / WS-026" (the session ID appears twice — once as a plain segment,
  once bold as the current page), and Supervisor Session Details' breadcrumb follows the
  same "Monitoring / Sessions / WS-024 / WS-024" pattern. Both are reproduced exactly as
  shown rather than collapsed to a single segment, since that is what the approved
  references show.
- No original Figma frame shows Supervisor-specific My Profile content or the
  notifications/user-menu dropdown content. My Profile now uses the same shared read-only
  template as the other roles; notification/user-menu dropdown content remains inert.
- The Administrator Dashboard's approved reference shows a house icon for the "Dashboard"
  sidebar link, but section 6 of the implementation prompt explicitly instructs not to use a
  house icon and to use the four-square grid icon instead — the written instruction was
  followed over the reference image, reusing the existing `icon_grid()` already used by
  Operator's and Supervisor's Dashboard links.
- Several Administrator reference frames (New/Edit Kit, Users, New User, Edit User, the
  Deactivate User modal, Roles, Edit Role) show a circled-X icon in the header where every
  other frame (and every Operator/Supervisor frame) shows the standard notification bell.
  This reads as a Figma prototype/exit-view artifact bleeding into the exported PNG rather
  than an intentional design change, so the header was kept consistent with the bell icon
  already used everywhere else in the app (section 8: "Keep: notification icon").
- The approved New Capture Station reference shows Station Code already styled read-only
  (grey background) even in New mode, unlike New Instrument's Internal Code (`INS-005`),
  which is pre-suggested but still editable-styled in its own New mode reference. Section 33
  of the implementation prompt only requires Station Code to be read-only "In Edit mode," so
  this implementation follows the written rule (and the New Instrument precedent) rather than
  that one frame's styling: `admin/configuration/capture_station_form.html` renders Station
  Code as an editable, pre-filled field in create mode.
- The approved Edit Institution Information reference renders both Institution Name and
  Institution Code in the same greyed, read-only-looking style. Section 31 of the
  implementation prompt only calls out Institution Code as the stable identifier to render
  read-only, so Institution Name remains editable here, consistent with every other form's
  pattern of exactly one system-controlled identifier field per record.
- The approved Roles list reference truncates the Operator row's description to "Performs
  instrument counting and validation a…", while the approved Edit Role reference's full
  Description field reads "Performs instrument counting sessions and validates operational
  results." for the same role — an apparent authoring inconsistency between the two frames,
  similar to the WS-024/WS-026 mismatch already noted above for Operator. Each screen
  reproduces its own reference's text as-is rather than reconciling them: the Roles list demo
  row uses "Performs instrument counting and validation activities." (truncated via
  `.data-table__cell--truncate` to match the visible "...and validation a…" shape) and Edit
  Role's demo record keeps the fuller sentence from its own reference.
- The approved Edit Instrument Family reference shows "How to Identify" and "Classification
  Characteristics" as taller, two-line-wrapped fields, while the New Instrument Family
  reference shows both at the same single-row height as every other field on that screen (both
  empty). Rather than infer a bespoke fixed height, both fields reuse the existing
  `.form-field__textarea` component (already used for Verification/Supervisor Notes
  elsewhere) in both modes — it simply renders shorter when empty.
- New Kit's Kit Composition rows are added via `kit-form.js` cloning the row markup in
  JavaScript rather than re-using the Jinja-rendered `<option>` list; a newly added row's
  Instrument Family `<select>` therefore renders without the decorative chevron icon markup
  present on server-rendered rows (the native select arrow still displays; only the inline SVG
  is missing). This is a deliberate, minimal-JS trade-off within the "lightweight JavaScript"
  scope of section 19, not a functional gap.

## Next steps (not part of this iteration)

Per the approved scope, this iteration completes the entire Operator CDE presentation layer,
the entire Supervisor CDE / Quality presentation layer, and — as of this iteration — the
entire IT Administrator presentation layer: Dashboard, Instrument Families, Instruments, and
Kits catalogs (each with a shared New/Edit form), Users (New/Edit, Change Password,
Deactivate User), Roles (list + Edit Role), and Configuration (Institution Information +
Capture Stations, each with its own Edit/New form) — all built by reusing the same base
layout, header, macros, and CSS token system, with only the minimal component extensions
documented above. No new role or backend functionality is planned beyond this: any future
work (production Flask routes/views, real persistence, Flask-Babel, authentication) belongs
to the backend integration effort already itemized throughout this document.
