# Refine Sign In and Operator Dashboard

Refine ONLY the currently implemented:

- Shared Sign In
- Operator Dashboard

of the Surgical Instrument Traceability System.

The frontend architecture and visual system are already approved.

Do NOT redesign the application.

Do NOT create new pages.

Do NOT modify backend-owned files.

Do NOT change the overall structure of:

- base.html
- layouts/guest.html
- layouts/authenticated.html
- shared partials
- theme system
- language selector structure
- sidebar architecture
- component architecture

This task is ONLY about improving visual fidelity to the approved Figma references and making the existing implementation slightly more compact.

==================================================
SCOPE
==================================================

You may modify only frontend files required to refine:

- templates/auth/sign_in.html
- templates/operator/dashboard.html
- shared layout/partials used by those pages
- CSS files used by those pages
- page-specific CSS if necessary

Do not modify:

- app.py
- extensions.py
- controllers/
- clients/
- models/
- devtools/frontend_preview.py
- backend logic

Do not modify Supervisor or IT Administrator templates.

==================================================
VISUAL SOURCE OF TRUTH
==================================================

Inspect the approved Figma PNG references for:

- Shared Sign In
- Operator Dashboard

located under:

apps/BackendWebFlask/ui_reference/

Compare the current browser implementation against those references.

Preserve the current design language.

Do not invent a new style.

==================================================
1. SIGN IN — COMPACT REFINEMENT
==================================================

The current Sign In implementation is visually correct but slightly larger and more spacious than the approved Figma reference.

Refine it to be somewhat more compact while preserving accessibility.

Adjust carefully:

- sign-in card width
- internal card padding
- vertical spacing between sections
- title size
- subtitle spacing
- input height
- button height
- spacing between inputs
- footer/supporting text spacing

Do NOT make controls too small.

Keep controls comfortable for keyboard and tablet use.

Do NOT change:

- Institutional Email
- Password
- Sign In
- EN selector
- Theme selector
- system title
- Authorized Staff Access
- restricted access text

Do NOT add:

- Forgot Password
- Reset Password
- Create Account
- role selector

==================================================
2. SIGN IN ALIGNMENT
==================================================

Keep the Sign In card visually centered in the main viewport.

Keep:

Language selector
Theme selector

aligned in the upper-right area as shown in Figma.

Ensure the card does not appear vertically oversized.

Maintain good balance between:

- logo/mark
- product title
- access section
- fields
- primary button
- restricted-access note

==================================================
3. OPERATOR DASHBOARD — DENSITY
==================================================

The current Operator Dashboard is visually correct but slightly too tall vertically compared with the Figma reference.

Refine the page so more of the dashboard is visible without unnecessary scrolling.

Do this primarily by reducing excessive vertical space.

Adjust carefully:

- vertical gaps between page sections
- card padding
- chart card height
- chart internal padding
- recent sessions section spacing
- dashboard header/title spacing

Do not make the interface cramped.

The goal is:

more compact
but still readable and accessible.

==================================================
4. KPI CARDS
==================================================

Keep the existing four cards:

Sessions Today
Active Sessions
Closed Sessions
Open Discrepancies

Preserve:
- current semantics
- current colors
- current values
- current borders

Refine only:

- card height
- padding
- label/value spacing

The four cards should remain visually balanced.

==================================================
5. SESSIONS BY DAY
==================================================

Keep the current Sessions by Day visualization.

Do not introduce a chart library.

Reduce excessive chart-card height if possible.

Keep:

Mon
Tue
Wed
Thu
Fri

and their values.

Ensure bars remain readable and visually similar to the Figma reference.

Do not overly enlarge the chart.

==================================================
6. SESSION STATUS
==================================================

Keep:

Active Sessions
Closed (Verified)
With Discrepancies

Preserve semantic colors.

Reduce unnecessary vertical spacing between rows and progress indicators while keeping readability.

Do not redesign this panel.

==================================================
7. RECENT COUNTING SESSIONS
==================================================

The Recent Counting Sessions section should appear higher in the page so more of its first rows are visible at common desktop resolutions.

Do not remove information.

Keep:

Session ID
Procedure / Operation
Kit
Operator
Status
Action

Preserve backend-friendly Jinja rendering and existing loops.

Do not replace dynamic rows with hard-coded repeated markup.

==================================================
8. PAGE TITLE / PRIMARY ACTION
==================================================

Keep:

Dashboard

Overview of instrument counting operations

and:

+ New Counting Session

Preserve the visual hierarchy.

Refine spacing only if needed to match Figma more closely.

Do not rename the page.

==================================================
9. SIDEBAR
==================================================

Do NOT redesign the Operator sidebar.

Keep exactly:

HOME
- Dashboard

OPERATIONS
- Counting Sessions
- New Session
- Session History

ACCOUNT
- My Profile
- Sign Out

Keep the approved four-square grid Dashboard icon.

Do not replace it.

Preserve current sidebar width unless a small adjustment is clearly required to match the Figma reference.

==================================================
10. HEADER
==================================================

Do NOT redesign the authenticated header.

Keep:

- breadcrumb
- notification icon
- EN
- Light / Dark / System
- avatar
- Alex Morgan fallback
- Operator role
- user menu affordance

Do not invent new dropdown content.

Refine spacing only if required for closer Figma fidelity.

==================================================
11. LIGHT AND DARK MODE
==================================================

Both Light and Dark modes currently work.

Do NOT break theme switching.

Any CSS refinement must continue to use semantic CSS custom properties.

Do not introduce hard-coded Light-mode-only colors into components.

Verify:

Light
Dark
System

remain functional.

==================================================
12. RESPONSIVENESS
==================================================

After compacting the layouts, preserve usability at:

- large desktop
- typical laptop
- tablet landscape

Do not use fixed heights that cause clipping at smaller resolutions.

Prefer:

min-height
max-width
flex/grid
responsive spacing

over rigid pixel layouts where practical.

==================================================
13. ACCESSIBILITY
==================================================

Do not reduce:

- focus visibility
- readable text size
- touch target usability
- label clarity
- status text

Status must continue to use text in addition to color.

==================================================
14. DO NOT CHANGE DATA CONTRACTS
==================================================

Do not rename existing backend-facing Jinja variables.

Do not change the current shapes expected for:

- current_user
- dashboard_stats
- sessions_by_day
- session_status_breakdown
- recent_sessions
- active_nav_item

Do not introduce new backend requirements for this visual refinement.

==================================================
15. NO NEW FUNCTIONALITY
==================================================

Do not implement:

- real login
- real logout
- navigation routes
- database queries
- notifications
- language persistence
- Flask-Babel configuration
- additional dashboards

This task is visual refinement only.

==================================================
16. PREVIEW VALIDATION
==================================================

Use the existing development preview harness to inspect:

/preview/sign-in

and:

/preview/operator/dashboard

Compare both pages visually against their corresponding approved Figma PNG references.

Test at minimum:

- Light mode
- Dark mode
- 1440px desktop width
- 1280px laptop width
- approximately 1024px tablet landscape width

Verify no CSS/JS errors are introduced.

==================================================
17. FINAL QUALITY CHECK
==================================================

Before finishing verify:

SIGN IN

□ Card is more compact.
□ Card remains centered.
□ Inputs remain accessible.
□ Button remains prominent.
□ EN and Theme controls remain correct.
□ No unapproved elements were added.

OPERATOR DASHBOARD

□ Dashboard is vertically more compact.
□ KPI cards remain balanced.
□ Chart areas are less oversized.
□ Recent Counting Sessions appears earlier.
□ Sidebar remains unchanged.
□ Header remains unchanged.
□ Light mode remains correct.
□ Dark mode remains correct.
□ System mode remains correct.
□ No dynamic Jinja contracts were broken.

==================================================
COMPLETION REPORT
==================================================

When finished, report:

- files modified
- exact visual refinements made
- any dimensions/spacing tokens changed
- whether Sign In and Dashboard still render successfully
- whether Light/Dark/System still work
- whether any uncertainty remained when matching Figma

Do not continue to other screens after this refinement.