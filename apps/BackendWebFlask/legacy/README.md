# Legacy UI routes (pre-RF)

Flask still exposes a few **legacy URLs** so old bookmarks do not 404. They are not the RF product path.

| Legacy route | What it was | RF replacement |
| ------------ | ----------- | -------------- |
| `/legacy/operator/dashboard` | Operator metrics dashboard | RF-OP-01 Assigned Sessions |
| `/legacy/operator/sessions/new` | Operator creates a session | RF-SP-02 (SPD schedules) |

`/operator/dashboard` and `/operator/sessions/new` redirect here.

**Retired templates** (V2 click-through, tray×2, old SPD wizard) were moved out of the app tree to the repo-root folder `Legacy/` (see `../../Legacy/README.md`). Safe to delete that folder when you no longer need the old screens.
