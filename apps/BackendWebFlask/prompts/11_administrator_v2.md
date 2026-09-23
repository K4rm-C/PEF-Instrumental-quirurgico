# 11 — Administrator V2 Migration

Continue the V2 frontend migration in:

apps/BackendWebFlask/

Shared V2, Operator V2 and Supervisor V2 are already complete.

Proceed directly with implementation.

IMPORTANT FOR CONTEXT EFFICIENCY:

- Do NOT perform another full-project audit.
- Do NOT reread prompts 01–10 unless a specific implementation issue requires it.
- Do NOT re-audit Shared, Operator or Supervisor.
- Do NOT explain your plan before editing files.
- Inspect only Administrator-related files and directly reused shared components.
- Keep the final response concise.

Read only the minimum context needed:

- V2_IMPLEMENTATION_NOTES.md
- ui_reference_v2/SCREEN_MANIFEST_V2.md
- ui_reference_v2/administrator/
- templates/admin/
- Administrator-related routes/controllers/view-data helpers
- directly reused templates/components/partials
- relevant shared/Admin CSS and JS
- relevant existing models only when required to wire a screen correctly

The approved PNG files inside:

ui_reference_v2/administrator/

are the visual source of truth.

Do not modify those PNG files.
Do not display reference PNGs directly in the application.

The project is being modified directly without Git.
Do not stop for branch, commit, repository-state or Git confirmation.

--------------------------------------------------
SCOPE
--------------------------------------------------

Migrate ONLY the Administrator role to V2.

Do not redesign or modify:

- Public/Shared V2
- Operator V2
- Supervisor V2

except for a strictly necessary shared compatibility fix.

Reuse the V2 component system already implemented:

- header
- sidebar
- breadcrumbs
- cards
- KPI cards
- tables
- forms
- filters
- status badges
- alerts
- modals
- pagination
- language/theme controls

Extend existing components instead of duplicating them.

Preserve Flask + Jinja and the existing MVC structure.

--------------------------------------------------
APPROVED ADMINISTRATOR SCREENS
--------------------------------------------------

Implement/update every approved PNG present in:

ui_reference_v2/administrator/

The expected V2 set contains these functional areas:

1. Dashboard

2. Instrument Families
   - List
   - New
   - Edit

3. Instruments
   - List
   - New
   - Edit

4. Kits
   - List
   - New
   - Edit

5. Procedures
   - List
   - New
   - Edit

6. Users
   - List
   - New
   - Edit
   - Change Password modal
   - Deactivate User modal

7. Roles
   - List
   - New
   - Edit

8. Vision Models
   - List
   - New
   - Edit
   - Activate Vision Model modal

9. Audit Log

10. Configuration
   - Configuration overview
   - Edit Institution
   - New Operating Room
   - Edit Operating Room
   - New Capture Station
   - Edit Capture Station

Use the exact filenames present in the reference folder.

Update existing templates whenever practical.

Create new templates only for V2 views that do not currently exist.

--------------------------------------------------
ADMIN SIDEBAR
--------------------------------------------------

Match the approved Administrator navigation.

HOME
- Dashboard

CATALOGS
- Instrument Families
- Instruments
- Kits
- Procedures

ADMINISTRATION
- Users
- Roles

SYSTEM
- Vision Models
- Audit Log
- Configuration

ACCOUNT
- My Profile
- Sign Out

Do not create duplicate navigation systems.

--------------------------------------------------
DASHBOARD
--------------------------------------------------

Match the approved V2 Administrator Dashboard.

Primary KPI cards:

Active Users = 18
Instrument Families = 11
Registered Instruments = 96
Active Kits = 8

Secondary KPI cards:

Procedures = 5
Capture Stations = 3
Vision Models = 2
Active Vision Model = 1

Catalog Overview:

Instrument Families = 11 Active
Instruments = 96 Registered
Kits = 8 Active
Procedures = 5 Active
Capture Stations = 3 Configured

System Overview:

Inactive Users = 0
Configured Roles = 3
Operating Rooms = 4
Capture Stations = 3
Active Institution = 1
Active Vision Model Version = v0.4.2
Model Classes = 11
System Status = Operational

Operational Metrics:

Total Sessions = 156
Total Discrepancies = 8
AI-Human Agreement = 94.2%
Human Corrections = 12
Average Resolution Time = 18 min

Use existing backend values when actually available.

Approved numbers may be used as presentation fallback data where analytics/backend integration does not yet exist.

Do not invent database schema merely to persist dashboard demo values.

--------------------------------------------------
INSTRUMENT FAMILIES
--------------------------------------------------

Update the existing CRUD to visually match V2.

Preserve existing backend fields and behavior.

Use actual model fields.

Do not invent additional persistence fields just because the reference includes explanatory UI text.

--------------------------------------------------
INSTRUMENTS
--------------------------------------------------

Update existing Instruments CRUD to match V2.

Preserve:

- instrument identification
- Instrument Family relationship
- lifecycle/cycle information where currently supported
- active state

Reuse existing model/controller behavior.

--------------------------------------------------
KITS
--------------------------------------------------

Update Kits CRUD to match V2.

IMPORTANT TERMINOLOGY:

Kit composition uses:

Instrument Families

not:

Instrument Types

The approved Delivery Kit example contains 5 instrument families and an expected total quantity of 11.

Use existing kit / kit-item relationships.

Do not change DB relationships solely for UI convenience.

--------------------------------------------------
PROCEDURES
--------------------------------------------------

Implement the V2 Procedures module if it does not already exist.

Base Procedure information should remain aligned with the actual model.

Do not invent fields such as Description or Status if they are not present in the current model.

Procedure list reference:

Total procedures = 5

Typical columns:

Code
Procedure
Associated Kits
Default Kit
Counting Phases
Action

Procedure form must support the existing relationships for:

Associated Kits
Counting Phases

Associated Kits should visually expose:

Kit
Technique Label
Default
Active
Action

Approved example:

Delivery Kit
Technique Label = Standard
Default = Default
Active = Active

General Surgery Kit
Technique Label = Alternative
Default = Not Default
Active = Active

Counting Phases should expose:

Phase
Count Required
Order
Active

Approved example:

Pre-Procedure | Yes | 1 | Active
Post-Procedure | Yes | 2 | Active

Include the approved:

+ Add Kit
+ Add Phase

interactions visually.

Use the actual procedure-kit and procedure-phase models/relationships.

Do not create duplicate persistence structures.

--------------------------------------------------
USERS
--------------------------------------------------

Update Users CRUD to V2.

Approved users count:

18

Use:

Assigned Roles

because users may have more than one role.

Each role must appear as one complete badge.

Examples:

Operator CDE
Supervisor CDE / Quality
IT Administrator

Do NOT split one role label into multiple badges.

New User:

The IT Administrator assigns the initial password.

No password recovery workflow is required.

Edit User should support existing user information and assigned roles.

Keep/update:

Change Password modal
Deactivate User modal

using existing backend actions where available.

Do not implement fake client-side user management.

--------------------------------------------------
ROLES
--------------------------------------------------

Update Roles CRUD to V2.

Role list should focus on:

Code
Description
Institution
Assigned Users
Action

Do not add a Status filter unless supported by the actual model/reference.

Do not create a separate Role Name field if the model does not have one.

Approved role codes include:

OPERATOR
SUPERVISOR
ADMIN

New/Edit Role should use:

Role Code
Description
Institution

Assigned Users may be read-only on Edit where shown in V2.

Use the real many-to-many user-role relationship.

--------------------------------------------------
VISION MODELS
--------------------------------------------------

Implement the V2 Vision Models module using the existing model structure.

List columns should match the approved reference:

Version
Classes
Status
Published At
Checksum
Action

Approved active example:

Version = v0.4.2
Classes = 11 Classes
Status = Active Model

New/Edit Vision Model should use model-supported concepts such as:

Version Tag
Model Asset Reference
Active
Checksum
Published At

Model Class mapping should use:

YOLO Class ID
Instrument Family
Action

Do NOT invent persistent fields such as:

- Model Name
- Confidence Threshold
- Description
- Class Name
- model-specific Status field

unless they genuinely exist in the current backend model.

Reuse the existing Vision Model / Model Class / Instrument Family relationships.

--------------------------------------------------
ACTIVATE VISION MODEL
--------------------------------------------------

Implement/update the approved activation modal.

Show:

Version
Classes
Model Asset
Checksum

Preserve this historical rule:

Historical counting sessions retain the model version originally used.

Activating a new model must not conceptually rewrite historical session traceability.

If backend activation behavior exists, preserve it.

If it does not yet exist, implement the UI safely and document the missing POST/service integration rather than inventing persistence.

--------------------------------------------------
AUDIT LOG
--------------------------------------------------

Implement/update Administrator Audit Log to match V2.

Filters:

Search Events
User
Event Type
Entity
Date

Table:

Timestamp
User
Role
Event
Entity
Record
Result
Details

Use existing audit/access-audit data where available.

Do not create fake database schema.

--------------------------------------------------
CONFIGURATION
--------------------------------------------------

Match the approved V2 Configuration module.

It must represent:

Institution Information
Operating Rooms
Capture Stations

Reuse existing Configuration structure wherever possible.

--------------------------------------------------
OPERATING ROOMS
--------------------------------------------------

Implement New/Edit Operating Room views if missing.

Use only fields supported by the existing model, such as:

Code
Name
Active

and Institution relationship where appropriate.

Do not invent extra configuration fields.

--------------------------------------------------
CAPTURE STATIONS
--------------------------------------------------

Update/create Capture Station forms to match V2.

Use:

Station Name
Operating Room
Status

Do NOT introduce a persistent "Station Code" if the model does not contain one.

Capture Station must be associated with an Operating Room.

If ROI configuration exists in the model but the V2 interface does not edit raw ROI data, it may be represented read-only as:

Configured

when appropriate.

Do not expose raw JSON unnecessarily.

--------------------------------------------------
DATA / BACKEND RULES
--------------------------------------------------

Use existing PostgreSQL models and relationships whenever available.

DO NOT:

- invent DB columns
- invent ORM relationships
- create migrations solely to satisfy visual mockups
- replace server persistence with JavaScript-only state
- hardcode CRUD behavior that already exists
- create a parallel backend architecture

If a V2 field has no backend representation:

1. preserve the approved visual structure when practical
2. use existing presentation/fallback mechanisms
3. document the integration gap
4. do not invent schema

--------------------------------------------------
ROUTES
--------------------------------------------------

Reuse existing Administrator routes and endpoint names whenever practical.

Add narrowly scoped routes only for truly new modules/screens such as:

- Procedures
- Vision Models
- Operating Rooms
- Audit Log
- New Role

if they are currently missing.

Do not reorganize the entire controller layer.

Do not modify Operator or Supervisor routes unless absolutely necessary.

--------------------------------------------------
CSS / JS
--------------------------------------------------

Reuse the shared V2 styles already implemented.

Only add Administrator-specific CSS where necessary.

Avoid duplicate implementations of:

cards
tables
forms
buttons
filters
badges
modals
pagination
alerts

Preserve existing JavaScript.

Do not create frontend-only persistence to simulate missing backend behavior.

--------------------------------------------------
VALIDATION
--------------------------------------------------

Do NOT provision PostgreSQL if it is unavailable.

Perform efficient validation only.

For all modified/new Administrator templates:

- verify Jinja syntax
- verify Flask imports/routes
- verify static asset references
- direct-render templates where possible
- perform HTTP smoke tests where DB access allows
- render screenshots only for Administrator screens being migrated
- compare those renders against ui_reference_v2/administrator/

Do not repeatedly screenshot or test untouched Operator/Supervisor pages.

For regression validation, a minimal render check of their main dashboards is sufficient.

Confirm:

- Shared V2 remains intact
- Operator V2 remains intact
- Supervisor V2 remains intact

--------------------------------------------------
DOCUMENTATION
--------------------------------------------------

Append a concise:

Administrator V2

section to:

V2_IMPLEMENTATION_NOTES.md

Include only:

- templates created
- templates updated
- routes/actions added or changed
- relevant backend integration limitations
- validation performed

Do not produce a long development diary.

--------------------------------------------------
FINAL RESPONSE
--------------------------------------------------

Keep the response concise.

Return only:

1. Administrator V2 screens implemented
2. Files created/modified
3. Validation result
4. Real blockers / pending backend integration

Do not repeat the prompt.
Do not provide another full-project audit.