# Legacy UI (pre-RF)

Screens and flows kept from earlier V2 / GitHub UI work that are **not** the current
RF contract (`rf_operador_supervisor_spd.md`). They remain runnable so demos and
visual QA are not lost, but they must not be treated as the product path.

| Legacy route | What it was | RF replacement |
| ------------ | ----------- | -------------- |
| `/legacy/operator/dashboard` | Operator metrics dashboard | RF-OP-01 session list is home |
| `/legacy/operator/sessions/new` | Operator creates a counting session | RF-SP-02 (SPD schedules) |
| `/operator/sessions/.../capture` etc. | Vision click-through WS-026 | Manual path OP-04M/06M first; vision stubs later |

Old URLs `/operator/dashboard` and `/operator/sessions/new` redirect here.

Do not delete templates under `templates/operator/` for these flows; wire RF
features beside them and retire only when product asks.
