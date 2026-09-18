# Complete IT Administrator Frontend

Implement ALL approved IT Administrator frontend views for the
“Surgical Instrument Traceability System”.

This iteration must complete the entire IT Administrator presentation layer while reusing the existing shared frontend architecture already implemented for the Shared, Operator, and Supervisor interfaces.

IMPORTANT:

The existing frontend foundation is approved.

DO NOT rebuild, redesign, or regress:

- Shared Sign In
- Base layout
- Authenticated layout
- Theme system
- Language selector
- Shared components
- Operator frontend
- Supervisor frontend

Reuse the current frontend architecture.

The approved IT Administrator Figma PNG references under:

apps/BackendWebFlask/ui_reference/

are the visual source of truth.

==================================================
1. SCOPE
==================================================

Implement the complete IT Administrator presentation layer.

Required approved screens:

1. IT Administrator Dashboard

CATALOGS
2. Instrument Families
3. New Instrument Family
4. Edit Instrument Family
5. Instruments
6. New Instrument
7. Edit Instrument
8. Kits
9. New Kit
10. Edit Kit

ADMINISTRATION
11. Users
12. New User
13. Edit User
14. Change User Password modal
15. Deactivate User modal
16. Roles
17. Edit Role

SYSTEM
18. Configuration
19. Edit Institution Information
20. New Capture Station
21. Edit Capture Station

Do NOT implement new screens that are not represented in the approved Figma references.

Do NOT implement backend CRUD logic.

==================================================
2. ALLOWED FILES
==================================================

You may create or modify frontend files under:

apps/BackendWebFlask/templates/
apps/BackendWebFlask/static/

You may extend:

apps/BackendWebFlask/devtools/frontend_preview.py

ONLY for DEVELOPMENT-ONLY preview routes and demo context.

You may update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Do NOT modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- database logic
- authentication logic
- production Flask routes

==================================================
3. FIRST STEP — INSPECT EXISTING IMPLEMENTATION
==================================================

Before writing code:

1. Inspect the existing frontend architecture.
2. Inspect the authenticated layout.
3. Inspect shared macros/components.
4. Inspect the existing theme implementation.
5. Inspect Operator and Supervisor sidebars as structural references.
6. Inspect the existing modal, feedback, table, form, filter, card, pagination and badge systems.
7. Read FRONTEND_INTEGRATION.md.
8. Inspect all current IT Administrator PNG references.

Do not duplicate existing generic frontend components.

==================================================
4. CODE LANGUAGE
==================================================

All source code must remain in English.

This includes:

- filenames
- variables
- functions
- CSS classes
- IDs
- data attributes
- JavaScript identifiers
- comments when necessary

Visible static interface strings must remain translation-ready using:

{{ _("...") }}

Do not configure Flask-Babel.

Do not create a JavaScript translation dictionary.

==================================================
5. FIGMA REFERENCES
==================================================

Before implementing each view, inspect the matching approved IT Administrator PNG under:

apps/BackendWebFlask/ui_reference/

Use the current approved Figma image as the visual source of truth.

Match:

- sidebar
- header
- content hierarchy
- tables
- cards
- forms
- badges
- buttons
- modals
- spacing
- responsive behavior

Do not redesign screens.

If multiple versions exist, use the latest approved/current reference and ignore OLD/obsolete versions.

==================================================
6. IT ADMINISTRATOR SIDEBAR
==================================================

Implement or complete the approved IT Administrator sidebar:

HOME
- Dashboard

CATALOGS
- Instrument Families
- Instruments
- Kits

ADMINISTRATION
- Users
- Roles

SYSTEM
- Configuration

ACCOUNT
- My Profile
- Sign Out

Use the approved four-square grid Dashboard icon.

Do NOT use a house icon.

Do not add Operator or Supervisor navigation.

==================================================
7. ACTIVE SIDEBAR STATES
==================================================

Dashboard
→ Dashboard active

Instrument Families
→ Instrument Families active

New Instrument Family
→ Instrument Families active

Edit Instrument Family
→ Instrument Families active

Instruments
→ Instruments active

New Instrument
→ Instruments active

Edit Instrument
→ Instruments active

Kits
→ Kits active

New Kit
→ Kits active

Edit Kit
→ Kits active

Users
→ Users active

New User
→ Users active

Edit User
→ Users active

Change Password
→ Users active

Deactivate User
→ Users active

Roles
→ Roles active

Edit Role
→ Roles active

Configuration
→ Configuration active

Edit Institution Information
→ Configuration active

New Capture Station
→ Configuration active

Edit Capture Station
→ Configuration active

==================================================
8. SHARED HEADER
==================================================

Reuse the existing authenticated header.

Administrator preview/demo identity:

Daniel Brooks

Role:

IT Administrator

Keep:

- breadcrumb
- notification icon
- language selector
- theme selector
- avatar
- user name
- role
- user-menu affordance

Do not invent notification dropdown content.

Do not create a My Profile screen.

==================================================
9. ADMINISTRATOR DASHBOARD
==================================================

Implement the approved IT Administrator Dashboard.

Use the exact Figma reference.

Expected metric concepts include:

Active Users
Instrument Families
Registered Instruments
Active Kits

Also reproduce approved:

Catalog Overview
System Overview

Do not add operational session/discrepancy metrics.

Do not invent infrastructure monitoring metrics.

==================================================
10. DASHBOARD DATA CONTRACT
==================================================

Use presentation-ready Jinja context.

Expected concepts:

dashboard_stats
catalog_overview
system_overview

Do not calculate data in Jinja.

==================================================
11. INSTRUMENT FAMILIES LIST
==================================================

Implement the approved Instrument Families screen.

Expected concepts include:

Search Families
Category
Status

Primary action:

+ New Instrument Family

Approved table concepts:

Code
Name
Category
Function
Status
Action

Render rows using Jinja.

==================================================
12. INSTRUMENT FAMILY FORM
==================================================

Prefer ONE reusable template:

admin/instrument_families/form.html

supporting both:

New Instrument Family
Edit Instrument Family

using context such as:

form_mode = "create" / "edit"

Approved fields:

Code
Name
Category
How to Identify
Classification Characteristics
Function
Status

In Edit mode:

Code must render read-only.

Do not add YOLO-specific fields.

==================================================
13. INSTRUMENT FAMILY ACTIONS
==================================================

Frontend preview behavior:

New
→ New Instrument Family

Edit
→ Edit Instrument Family

Cancel
→ Instrument Families

Save
→ Instrument Families

Do not persist data.

==================================================
14. INSTRUMENTS LIST
==================================================

Implement the approved Instruments screen.

Approved filter concepts:

Search Instruments
Instrument Family
Cycle Status
Active Status

Approved table concepts:

Internal Code
Instrument Family
Cycle Status
Active Status
Action

Render rows with Jinja.

==================================================
15. INSTRUMENT STATUS SEMANTICS
==================================================

Keep these concepts separate:

CYCLE STATUS

Available
In Use
Sterilization
Unavailable

ACTIVE STATUS

Active
Inactive

Do not merge or visually confuse them.

==================================================
16. INSTRUMENT FORM
==================================================

Prefer one reusable:

admin/instruments/form.html

for both:

New Instrument
Edit Instrument

Fields:

Internal Code
Instrument Family
Cycle Status
Active Status

In Edit mode:

Internal Code must be read-only.

Use selects where appropriate.

==================================================
17. KITS LIST
==================================================

Implement the approved Kits screen.

Approved concepts:

Search Kits
Status

Primary action:

+ New Kit

Table concepts:

Kit
Version
Instrument Types
Total Expected Instruments
Status
Action

Do not use:

Tray

Use only:

Kit

==================================================
18. KIT FORM
==================================================

Prefer one reusable:

admin/kits/form.html

for:

New Kit
Edit Kit

Approved top fields:

Kit Name
Version
Status

Section:

Kit Composition

Columns:

Instrument Family
Expected Quantity
Action

Include:

+ Add Instrument Type

Summary:

Instrument Types
Total Expected Instruments

Actions:

Cancel
Save Kit

In Edit mode:

Version should appear read-only/system-controlled.

==================================================
19. KIT FRONTEND INTERACTION
==================================================

For preview/presentation only:

+ Add Instrument Type

may add another editable composition row if it can be implemented cleanly with lightweight JavaScript.

Remove

may remove a composition row.

Expected total and number of types may update visually.

This is presentation-only.

Do not persist changes.

Do not implement business validation beyond simple frontend constraints.

==================================================
20. USERS LIST
==================================================

Implement the approved Users screen.

Approved filters:

Search Users
Role
Institution
Status

Primary action:

+ New User

Table concepts:

Name
Email
Institution
Role
Status
Action

Use demo examples matching Figma where practical:

Alex Morgan
Operator CDE

Sophia Turner
Supervisor CDE / Quality

Daniel Brooks
IT Administrator

Use @institution.edu consistently.

==================================================
21. NEW USER
==================================================

Use the approved New User fields:

Name
Institutional Email
Institution
Role
Password
Confirm Password
Status

Do not remove Password or Confirm Password.

Do not add:

Forgot Password
Password Recovery
Temporary Password Email
Security Questions

==================================================
22. NEW USER PASSWORD VALIDATION
==================================================

Lightweight frontend validation may verify:

Password
Confirm Password

match.

If they do not match:

show translation-ready inline feedback such as:

Passwords do not match.

Do not persist or hash passwords in frontend JavaScript.

Backend is responsible for secure password hashing/storage.

==================================================
23. EDIT USER
==================================================

Prefer reusing:

admin/users/form.html

where practical.

Edit User should contain:

Name
Institutional Email
Institution
Role
Status

Do NOT expose the stored password.

Provide the approved actions:

Change Password
Deactivate User

and normal:

Cancel
Save Changes

==================================================
24. CHANGE PASSWORD MODAL
==================================================

Implement the approved:

Change User Password

modal using the existing generic modal system.

Fields:

New Password
Confirm New Password

Actions:

Cancel
Change Password

Do not request the current password because this flow represents an IT Administrator changing another user's credential.

Do not expose existing password/hash data.

==================================================
25. DEACTIVATE USER MODAL
==================================================

Implement the approved:

Deactivate User?

modal.

Use the existing generic modal system.

Message should reflect the approved concept:

the user loses access while inactive, but historical records remain.

Actions:

Cancel
Deactivate User

Use destructive styling.

Do not use:

Delete User

==================================================
26. USER FRONTEND PREVIEW FLOW
==================================================

Allow development preview of:

Users
→ New User
→ Save
→ Users

Users
→ Edit
→ Edit User
→ Change Password
→ Modal
→ Cancel / Change Password

Edit User
→ Deactivate User
→ Modal
→ Cancel / Deactivate User
→ Users

Do not implement backend persistence.

==================================================
27. ROLES LIST
==================================================

Implement the approved Roles screen.

Approved columns:

Code
Role
Description
Assigned Users
Status
Action

Use the approved roles:

Operator CDE
Supervisor CDE / Quality
IT Administrator

Do not concatenate Description and Assigned Users.

==================================================
28. EDIT ROLE
==================================================

Implement the approved Edit Role screen.

Fields:

Role Code
Role Name
Description
Status
Assigned Users

Role Code:
read-only

Assigned Users:
read-only

Do NOT create a permissions matrix.

Do NOT create granular permission controls.

Do NOT create New Role unless an approved Figma screen exists.

==================================================
29. CONFIGURATION
==================================================

Implement the approved Configuration screen.

Keep ONLY:

Institution Information

Capture Stations

Do not add unsupported General Settings.

Do NOT add:

Session Timeout
Default Kit View
Auto Refresh
AI Confidence Threshold
YOLO Configuration
GPU Settings
Database Settings
Cloud Settings
Password Policies

==================================================
30. INSTITUTION INFORMATION
==================================================

Configuration should show approved concepts:

Institution Name
Institution Code
Status

Action:

Edit

==================================================
31. EDIT INSTITUTION INFORMATION
==================================================

Implement the approved form.

Fields:

Institution Name
Institution Code
Status

If Institution Code is treated as stable identifier:

render it read-only in Edit mode.

Actions:

Cancel
Save Changes

Do not invent address/contact fields unless present in Figma.

==================================================
32. CAPTURE STATIONS LIST
==================================================

Configuration must include the approved Capture Stations table.

Concepts:

Station Code
Station Name
Status
Action

Primary action:

+ New Capture Station

==================================================
33. CAPTURE STATION FORM
==================================================

Prefer one reusable:

admin/configuration/capture_station_form.html

for:

New Capture Station
Edit Capture Station

Fields:

Station Code
Station Name
Status

In Edit mode:

Station Code is read-only.

Actions:

Cancel
Save Capture Station
or the approved existing Save label.

==================================================
34. DEACTIVATION PRINCIPLE
==================================================

Prefer:

Active
Inactive

over permanent deletion throughout Administrator interfaces.

Do not introduce prominent delete actions for:

Users
Instrument Families
Instruments
Kits
Roles
Capture Stations

Historical references must remain conceptually preservable.

==================================================
35. SHARED COMPONENT REUSE
==================================================

Reuse existing:

- cards
- tables
- forms
- filters
- pagination
- buttons
- badges
- modals
- alerts
- feedback banners
- page action bars
- icon macros
- authenticated shell

Do not create Administrator-specific duplicates of generic components.

==================================================
36. FORM REUSE
==================================================

Use one form template for New/Edit where layouts are substantially identical.

Preferred:

instrument_families/form.html
instruments/form.html
kits/form.html
users/form.html
configuration/capture_station_form.html

Use presentation context to control:

title
breadcrumb
mode
read-only fields
submit label

Avoid duplicated nearly-identical HTML.

==================================================
37. THEME SUPPORT
==================================================

All Administrator screens must support:

Light
Dark
System

Reuse existing semantic design tokens.

Do not introduce hard-coded Light-only visual rules.

==================================================
38. LANGUAGE SUPPORT
==================================================

All static visible strings must use:

{{ _("...") }}

Do not configure Flask-Babel.

Do not build client-side translation dictionaries.

If JavaScript needs visible messages, receive translation-ready values from Jinja-rendered attributes.

==================================================
39. RESPONSIVENESS
==================================================

Match desktop Figma first.

Maintain tablet-landscape usability.

Pay particular attention to:

- wide catalog tables
- Kit Composition
- User tables
- Role descriptions
- Configuration
- form action areas

Tables may use controlled horizontal scrolling.

Do not allow global application horizontal overflow.

==================================================
40. ACCESSIBILITY
==================================================

Maintain:

- semantic labels
- table headers
- visible focus states
- accessible modals
- status text plus color
- password input semantics
- touch-friendly actions
- sufficient contrast

Use buttons/links rather than clickable generic divs.

==================================================
41. PREVIEW ROUTES
==================================================

Extend the development preview harness with Administrator routes.

Suggested:

/preview/admin/dashboard

/preview/admin/instrument-families

/preview/admin/instrument-families/new

/preview/admin/instrument-families/edit

/preview/admin/instruments

/preview/admin/instruments/new

/preview/admin/instruments/edit

/preview/admin/kits

/preview/admin/kits/new

/preview/admin/kits/edit

/preview/admin/users

/preview/admin/users/new

/preview/admin/users/edit

/preview/admin/roles

/preview/admin/roles/edit

/preview/admin/configuration

/preview/admin/configuration/institution/edit

/preview/admin/configuration/capture-stations/new

/preview/admin/configuration/capture-stations/edit

These routes are development-only.

Do not create production routes.

==================================================
42. PREVIEW NAVIGATION
==================================================

Use context-provided preview URLs following the pattern already established for Operator and Supervisor.

Administrator sidebar should be navigable in preview.

Do not hard-code preview URLs into templates if doing so would interfere with production integration.

==================================================
43. ADMIN PRIMARY PREVIEW FLOWS
==================================================

Verify:

Sign In preview
→ Administrator Dashboard

Dashboard
→ Instrument Families
→ New / Edit
→ Save / Cancel
→ Instrument Families

Dashboard
→ Instruments
→ New / Edit
→ Save / Cancel
→ Instruments

Dashboard
→ Kits
→ New / Edit
→ Save / Cancel
→ Kits

Dashboard
→ Users
→ New
→ Save
→ Users

Users
→ Edit
→ Change Password modal

Users
→ Edit
→ Deactivate User modal

Dashboard
→ Roles
→ Edit
→ Save
→ Roles

Dashboard
→ Configuration
→ Edit Institution

Configuration
→ New Capture Station

Configuration
→ Edit Capture Station

==================================================
44. DEMO DATA
==================================================

Use local preview dictionaries/objects matching approved Figma examples where practical.

Do not import database models.

Do not initialize SQLAlchemy.

Do not connect to PostgreSQL.

==================================================
45. DATA CONTRACT DOCUMENTATION
==================================================

Update:

apps/BackendWebFlask/FRONTEND_INTEGRATION.md

Document Administrator context contracts for:

Dashboard
Instrument Families
Instruments
Kits
Users
Roles
Configuration

Document modal integration points.

Document form create/edit context.

Clearly distinguish frontend simulation from backend CRUD responsibilities.

==================================================
46. BACKEND INTEGRATION EXPECTATIONS
==================================================

Document expected concepts such as:

instrument_families
instrument_family

instruments
instrument

kits
kit
kit_items

users
user

roles
role

institution
capture_stations
capture_station

Do not assume SQLAlchemy implementation details that are not yet finalized.

==================================================
47. PASSWORD RESPONSIBILITY
==================================================

Explicitly document:

Frontend:
- collects Password + Confirm Password on New User
- performs optional visual match validation
- never stores plaintext credentials

Backend:
- validates password policy if applicable
- hashes password securely
- stores password hash
- authenticates Sign In

Do not implement password storage in frontend.

==================================================
48. ROLE ISOLATION
==================================================

Administrator interfaces must NOT expose Operator actions such as:

New Counting Session
Continue
Validate Count

and must NOT expose Supervisor actions such as:

Discrepancy Review
Approve Review
Request Correction
Reports
Indicators
Audit Log

Keep role responsibilities separated.

==================================================
49. NO BACKEND IMPLEMENTATION
==================================================

Do not implement:

- CRUD database operations
- SQLAlchemy queries
- password hashing
- authentication
- authorization
- APIs
- model creation
- server-side validation
- database schema changes

Only presentation-layer code is in scope.

==================================================
50. REGRESSION VALIDATION
==================================================

Before finishing verify existing Shared, Operator, and Supervisor preview routes still render.

At minimum recheck representative screens:

Shared Sign In
Operator Dashboard
Operator Count Validation
Supervisor Dashboard
Supervisor Discrepancy Review

Do not redesign them.

==================================================
51. FINAL ADMINISTRATOR CHECKLIST
==================================================

DASHBOARD

□ Matches Figma.
□ Correct Administrator sidebar.
□ No Operator/Supervisor metrics.

INSTRUMENT FAMILIES

□ List correct.
□ New works visually.
□ Edit works visually.
□ Edit Code read-only.

INSTRUMENTS

□ List correct.
□ New works visually.
□ Edit works visually.
□ Cycle Status and Active Status distinct.

KITS

□ List correct.
□ New/Edit reuse form.
□ Composition correct.
□ Version controlled/read-only where appropriate.

USERS

□ List correct.
□ New User has Password + Confirm Password.
□ Edit User does not expose password.
□ Change Password modal works.
□ Deactivate User modal works.
□ No Delete User.

ROLES

□ List correct.
□ Edit Role correct.
□ Assigned Users separate/read-only.
□ No permissions matrix.

CONFIGURATION

□ Institution Information correct.
□ Edit Institution correct.
□ Capture Stations correct.
□ New/Edit Capture Station correct.
□ No unsupported General Settings.

GLOBAL

□ Light works.
□ Dark works.
□ System works.
□ Translation-ready.
□ Source code remains English.
□ Shared frontend not regressed.
□ Operator frontend not regressed.
□ Supervisor frontend not regressed.
□ No backend-owned files modified.

==================================================
52. COMPLETION REPORT
==================================================

When finished, report:

- files created
- files modified
- Administrator templates implemented
- preview routes added
- shared components reused/extended
- page-specific CSS/JS added
- Jinja context contracts
- frontend-only form/modal interactions
- uncertainties found in Figma
- preview validation results
- Light/Dark/System validation
- Shared/Operator/Supervisor regression validation
- confirmation that no backend-owned files were modified

Do not implement any additional role or backend functionality after completing this prompt.