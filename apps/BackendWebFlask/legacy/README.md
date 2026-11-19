# Legacy UI routes (pre-RF)

Flask still exposes a couple old URLs so bookmarks dont 404. Not the RF product path.

| Legacy route | Was | RF replacement |
| --- | --- | --- |
| `/legacy/operator/dashboard` | operator metrics dashboard | RF-OP-01 Assigned Sessions |
| `/legacy/operator/sessions/new` | operator creates a session | RF-SP-02 (SPD schedules) |

`/operator/dashboard` and `/operator/sessions/new` redirect here.

Old V2 templates / porting zips used to sit in a root `Legacy/` folder. That tree is gone; runtime is `apps/*` only.
