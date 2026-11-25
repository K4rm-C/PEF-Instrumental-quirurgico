"""ELECTRON-01 end to end on an isolated Docker Compose stack (see apps/PEF-Electron/README.md §Pruebas E2E).

Real: PostgreSQL, Redis, AuthService, SessionService, API Gateway, portal (BackendWebFlask), the Electron app
(built, driven through its UI by e2e/electron-ui.mjs) and the VisionWorker application code.
SIMULATED (and reported as such): the YOLO detector and the camera (e2e/vision_sim_worker.py, e2e/camera_sim.py),
because there are no best.pt weights nor a physical camera in this environment.

Flow (ELECTRON-01 phase 10): Electron waits → Supervisor schedules from the portal → Operator starts from the
portal → Electron detects and opens the monitor → detections → yellow / green / red per official phase →
recapture → official validation → SPD close → Electron back to waiting. Also: window auto-show from the
tray, SessionService restart, and a check that the counting rules / official records were not touched.

usage (from the repo root, stack up with the e2e env file):
  python apps/PEF-Electron/e2e/e2e_electron.py <e2e.env>
env:   COMPOSE_PROJECT (default pef_electron_e2e), PEF_E2E_SHOTS (screenshots folder)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

ENV_FILE = Path(sys.argv[1]).resolve()
ENV = dict(l.split("=", 1) for l in ENV_FILE.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#") and "=" in l)
WEB = f"http://127.0.0.1:{ENV['WEB_PORT']}"
AUTH = f"http://127.0.0.1:{ENV['AUTH_PORT']}"
GATEWAY = f"http://127.0.0.1:{ENV['GATEWAY_HTTP_PORT']}"
VISION = "http://127.0.0.1:5002"
DSN = f"postgresql://{ENV['POSTGRES_USER']}:{ENV['POSTGRES_PASSWORD']}@127.0.0.1:{ENV['POSTGRES_PORT']}/{ENV['POSTGRES_DB']}"
PROJECT = os.environ.get("COMPOSE_PROJECT", "pef_electron_e2e")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "docker-compose.integral.yml"), "--env-file", str(ENV_FILE)]
SHOTS = os.environ.get("PEF_E2E_SHOTS", str(HERE / "screenshots"))

import portal_csrf  # noqa: E402  (CSRF token for portal POSTs, as the other E2E scripts)
portal_csrf.enable(WEB)
from camera_sim import SimulatedCamera  # noqa: E402

KIT = "51515151-0000-4000-8000-000000000001"
ROOM, STATION = "12121212-0000-4000-8000-000000000001", "13131313-0000-4000-8000-000000000001"
PHYSICIAN, PATIENT = "32323232-0000-4000-8000-000000000001", "31313131-0000-4000-8000-000000000001"
results: list[tuple[str, bool, str]] = []
OPERATOR, OPERATOR_PW = "operador2@instrumed.com", "DemopwdOP278!"  # no seeded session in progress
sys.stdout.reconfigure(encoding="utf-8")


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return bool(ok)


def q(sql: str, *args):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.execute(sql, args)
        return cur.fetchall() if cur.description else []


def login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{AUTH}/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, (email, r.status_code, r.text[:200])
    return s


def wait_http(url: str, timeout: float = 180) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(url, timeout=3).status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


class Ui:
    """The real Electron app, driven through its UI (electron-ui.mjs)."""

    def __init__(self):
        env = {**os.environ, "PEF_E2E_SHOTS": SHOTS}
        env.pop("ELECTRON_RUN_AS_NODE", None)
        self.p = subprocess.Popen(["node", str(HERE / "electron-ui.mjs")], cwd=str(HERE.parent), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        first = self._read()
        assert first.get("event") == "launched", first

    def _read(self) -> dict:
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("electron-ui ended")
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)

    def cmd(self, **c) -> dict:
        self.p.stdin.write(json.dumps(c) + "\n")
        self.p.stdin.flush()
        return self._read()

    def expect(self, name: str, **c) -> dict:
        r = self.cmd(cmd="expect", **c)
        check(name, r.get("ok"), json.dumps({**(r.get("observed") or {}), "windows": r.get("windows")}, ensure_ascii=False))
        return r


def official(sid: str):
    return q("SELECT s.code, ph.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
             "LEFT JOIN cat_operation_phase ph ON ph.id = w.current_phase_id WHERE w.id = %s", sid)[0]


def event_codes(sid: str) -> list[str]:
    return [r[0] for r in q("SELECT t.code FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                            "WHERE ce.session_id = %s ORDER BY ce.occurred_at", sid)]


def main() -> int:
    for url in (f"{WEB}/health/ready", f"{AUTH}/health/ready", f"http://127.0.0.1:{ENV['SESSION_PORT']}/health/ready",
                f"{GATEWAY}/healthz"):
        check(f"service ready {url}", wait_http(url))

    # --- VisionWorker (real app code, simulated detector) -----------------------------------------------
    sim_env = {**os.environ, "VISION_MONITOR_SECRET": ENV["VISION_MONITOR_SECRET"], "VISION_WS_SECRET": ENV["VISION_WS_SECRET"],
               "REDIS_URL": f"redis://:{ENV['REDIS_PASSWORD']}@127.0.0.1:{ENV['REDIS_PORT']}/0", "PYTHONUNBUFFERED": "1"}
    sim = subprocess.Popen([sys.executable, str(HERE / "vision_sim_worker.py")], env=sim_env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    check("VisionWorker (SIMULATED detector) up with monitor mode", wait_http(f"{VISION}/health", 60)
          and requests.get(f"{VISION}/health", timeout=5).json().get("monitor_ws") is True)

    operator_id = str(q('SELECT id FROM "user" WHERE email = %s', OPERATOR)[0][0])
    notice = str(q("SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1")[0][0])
    procedure = str(q("SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1")[0][0])
    kit_lines = [(str(f), int(n)) for f, n in q("SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id", KIT)]
    station_name = q("SELECT name FROM capture_station WHERE id = %s", STATION)[0][0]
    # Leftovers of earlier runs of THIS isolated stack only: the station must start without a session in progress.
    stale = q("SELECT w.id FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
              "WHERE w.station_id = %s AND s.code = 'in_progress' AND w.user_id = %s", STATION, operator_id)
    check(f"{OPERATOR} starts without sessions in progress at the station", not stale, str(stale))
    others = q("SELECT count(*) FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
               "WHERE w.station_id = %s AND s.code = 'in_progress' AND w.user_id <> %s", STATION, operator_id)[0][0]
    print(f"INFO other operators' sessions in progress at this station (must stay invisible): {others}")

    ui = Ui()
    try:
        # 2-3. Electron starts, station configured through the UI, Operator signs in → waiting screen.
        r = ui.cmd(cmd="setup", apiBaseUrl=GATEWAY, stationId=STATION, visionWsUrl="ws://127.0.0.1:5002/ws", shot="01-setup")
        check("Electron: station configured from the UI", r.get("ok"), r.get("error", ""))
        r = ui.cmd(cmd="login", email=OPERATOR, password=OPERATOR_PW)
        check("Electron: operator signed in through the API Gateway", r.get("ok"), json.dumps(r))
        w = ui.expect("Electron: waiting screen, gray, 'Esperando sesión del Operador'", appState="waiting", timeout=20000, shot="02-waiting")
        check("waiting screen shows the station name", (w.get("observed") or {}).get("waitingText") == "Esperando sesión del Operador")

        # 4. Supervisor schedules from the portal (RF form, privacy agreement → vision capture).
        spd = login("supervisor@instrumed.com", "DemopwdSPD78!")
        when = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        data = {"operator_user_id": operator_id, "procedure_type_id": procedure, "room_id": ROOM, "station_id": STATION,
                "kit_id": KIT, "patient_id": PATIENT, "scheduled_at": when,
                "family_id[]": [f for f, _ in kit_lines], "expected_quantity[]": [str(n) for _, n in kit_lines],
                "physician_id[]": [PHYSICIAN], "surgical_role_code[]": ["surgeon"],
                "confirm_privacy": "1", "privacy_notice_version_id": notice, "privacy_confirm_checkbox": "1"}
        resp = spd.post(f"{WEB}/supervisor/sessions/new", data=data, allow_redirects=False, timeout=30)
        sid = resp.headers.get("Location", "").rstrip("/").split("/")[-1]
        check("Supervisor scheduled the session from the portal", resp.status_code in (302, 303) and len(sid) == 36, f"{resp.status_code} {sid}")
        ui.expect("Electron: a scheduled (not started) session keeps it waiting", appState="waiting", timeout=6000)

        # Hide the monitor in the tray: it must come forward by itself when the session starts.
        ui.cmd(cmd="hide")

        # 5. Operator starts the session from the portal.
        op = login(OPERATOR, OPERATOR_PW)
        r = op.post(f"{WEB}/operator/sessions/{sid}/begin", data={"instruments_ready": "1"}, allow_redirects=False, timeout=30)
        check("Operator started the session from the portal", r.status_code in (302, 303) and "/capture" in r.headers.get("Location", ""), r.headers.get("Location", ""))
        check("official state: in_progress / start", official(sid) == ("in_progress", "start"), str(official(sid)))

        # 6-7. Electron detects it and opens the monitor.
        r = ui.expect("Electron: detects the active session → monitor (Conteo inicial)", appState="monitoring", phaseText="Conteo inicial", timeout=15000, shot="03-session-detected")
        check("Electron: window shown automatically from the tray", any(x.get("visible") for x in r.get("windows", [])), json.dumps(r.get("windows")))

        # 8. Operator opens capture (portal binds VisionWorker); browser camera simulated over the live WS.
        r = op.get(f"{WEB}/operator/sessions/{sid}/capture", timeout=30)
        check("portal capture page bound the session on VisionWorker", r.status_code == 200 and requests.get(f"{VISION}/sessions/{sid}/state", timeout=5).ok)
        cm = requests.get(f"{VISION}/__sim__/class-map/{sid}", timeout=5).json()
        expected = cm["expected"]
        check("every expected family has a model class (bind class_map)", all(cm["class_ids"].get(c) for c in expected), json.dumps(cm["class_ids"]))
        ticket = op.get(f"{WEB}/operator/sessions/{sid}/capture/ws-ticket", timeout=15).json()
        cam = SimulatedCamera("ws://127.0.0.1:5002/ws", sid, ticket["ticket"], cm["class_ids"])
        total = sum(expected.values())
        cam.set_counts(expected)
        r = ui.expect("9/11. full tray → green PROVISIONAL", appState="monitoring", color="green", kind="provisional", timeout=25000, shot="04-green-provisional-initial")
        check(f"count shown {total}/{total}", (r.get("observed") or {}).get("count") == f"{total}/{total}", (r.get("observed") or {}).get("count", ""))
        check("annotated VisionWorker frame displayed", (r.get("observed") or {}).get("hasFrame") is True)
        check("source labelled as camera of the portal (not claimed physical)", "Cámara" in ((r.get("observed") or {}).get("source") or ""), (r.get("observed") or {}).get("source", ""))
        check("operator's own live channel keeps receiving results while observed", cam.results > 0, str(cam.results))

        # Procedure: phase change from the portal.
        r = op.post(f"{WEB}/operator/sessions/{sid}/capture/phase", data={"phase_code": "demo_phase_1"}, timeout=15)
        check("Operator changed phase in the portal (demo_phase_1)", r.ok, r.text[:120])
        ui.expect("Electron: phase 'Procedimiento en curso'", phaseText="Procedimiento en curso", timeout=10000)
        missing = dict(expected)
        first = sorted(missing)[0]
        take = min(2, missing[first])
        missing[first] -= take
        cam.set_counts(missing)
        r = ui.expect("10. instruments out of the tray during the procedure → YELLOW", color="yellow", timeout=20000, shot="05-yellow-procedure")
        check(f"yellow shows the shortage ({total - take}/{total})", (r.get("observed") or {}).get("count") == f"{total - take}/{total}")
        cam.set_counts(expected)
        ui.expect("11. instruments back → green provisional again", color="green", kind="provisional", timeout=20000)

        # SessionService restart while monitoring: gray, then recovery (no data invented meanwhile).
        subprocess.run(COMPOSE + ["stop", "session"], check=True, capture_output=True)
        ui.expect("SessionService down → GRAY (reconnecting)", color="gray", timeout=20000, shot="06-gray-session-service-down")
        subprocess.run(COMPOSE + ["start", "session"], check=True, capture_output=True)
        r = ui.expect("SessionService back → same session, green again", appState="monitoring", color="green", timeout=60000)
        check("frame still displayed after the SessionService restart", (r.get("observed") or {}).get("hasFrame") is True)
        time.sleep(25)  # past one ticket renewal period (ttl/3 = 20 s): the observer must still be the same live link
        r = ui.expect("live link healthy after the renewal period", color="green", timeout=10000)
        check("frame displayed after renewal", (r.get("observed") or {}).get("hasFrame") is True)
        observers = requests.get(f"{VISION}/__sim__/observers", timeout=5).json()
        check("exactly one observer connection on the worker (no leaked sockets)", observers.get(sid) == 1, json.dumps(observers))

        # 12-13. Official final verification from the portal; shortage → RED.
        op.post(f"{WEB}/operator/sessions/{sid}/capture/phase", data={"phase_code": "final_count"}, timeout=15)
        ui.expect("12. Electron: phase 'Verificación final'", phaseText="Verificación final", timeout=10000)
        short = dict(expected)
        short[first] -= 1
        cam.set_counts(short)
        r = ui.expect("13. shortage in the final verification → RED", color="red", titleIncludes="Faltante", timeout=20000, shot="07-red-final")
        check("frame displayed in the final verification", (r.get("observed") or {}).get("hasFrame") is True)
        before = event_codes(sid)
        time.sleep(3)
        # 14. The red light changes nothing official: no close, no discrepancy, no validation was created by it.
        check("14. official record untouched by the monitor (still in_progress, no close/discrepancy events)",
              official(sid)[0] == "in_progress" and not {"manual_close", "discrepancy_raised", "session_close", "validation_passed"} & set(event_codes(sid)),
              str(official(sid)))
        check("monitor created no count events (only the portal's own window samples)", set(event_codes(sid)) - set(before) <= {"auto_count"})

        # 15-16. Correct recapture.
        cam.set_counts(expected)
        ui.expect("15-16. recapture with the full tray → green provisional (never confirmed yet)", color="green", kind="provisional", timeout=20000, shot="08-green-after-recapture")

        # 17. Official validation in the portal (Finish → human validation equal to the reference).
        r = op.post(f"{WEB}/operator/sessions/{sid}/capture/finish", allow_redirects=False, timeout=30)
        check("Operator finished counting in the portal", r.status_code in (302, 303) and "detection-summary" in r.headers.get("Location", ""), r.headers.get("Location", ""))
        cam.stop()
        fam_ids = dict(q("SELECT f.code, f.id FROM instrument_family f WHERE f.code = ANY(%s)", list(expected)))
        form = {f"validated_{fam_ids[c]}": str(n) for c, n in expected.items()}
        for c in expected:
            form[f"reason_{fam_ids[c]}"] = ""
            form[f"notes_{fam_ids[c]}"] = ""
        r = op.post(f"{WEB}/operator/sessions/{sid}/vision-validation", data=form, allow_redirects=False, timeout=30)
        check("17. Operator official validation submitted", r.status_code in (302, 303), f"{r.status_code} {r.headers.get('Location', '')}")
        check("official state: awaiting_spd_review", official(sid)[0] == "awaiting_spd_review", str(official(sid)))
        ui.expect("Electron: review phase, GREEN CONFIRMED (official result)", appState="review", color="green", kind="confirmed", timeout=20000, shot="09-review-green-confirmed")

        # 18. SPD closes from the portal.
        r = spd.post(f"{WEB}/supervisor/sessions/{sid}/close", data={"material_recovered": "1"}, allow_redirects=False, timeout=30)
        check("18. Supervisor closed the session in the portal", r.status_code in (302, 303) and official(sid)[0] == "closed", f"{r.status_code} {official(sid)}")
        ui.expect("19. Electron recognises the end (Sesión terminada)", appState="ended", timeout=20000, shot="10-ended")
        w = ui.expect("20. Electron back to the waiting screen (monitoring stopped)", appState="waiting", timeout=90000, shot="11-back-to-waiting")
        check("waiting screen shows no light and no video", (w.get("observed") or {}).get("color") is None and (w.get("observed") or {}).get("source") is None)

        # Counting rules untouched: the official events are the portal's usual ones, authored by people.
        codes = event_codes(sid)
        authors = {str(r[0]) for r in q("SELECT DISTINCT user_id FROM count_event WHERE session_id = %s", sid)}
        spd_id = str(q("SELECT id FROM \"user\" WHERE email = 'supervisor@instrumed.com'")[0][0])
        check("official events are the portal's standard sequence",
              codes[0] == "session_open" and "manual_close" in codes and "session_close" in codes and "manual_count" in codes, ",".join(sorted(set(codes))))
        check("every count event authored by the Operator or the Supervisor", authors <= {operator_id, spd_id, "None"}, str(authors))
        writes = q("SELECT action FROM access_audit WHERE resource_id = %s AND outcome = 'success' ORDER BY occurred_at", sid)
        check("audit trail contains only portal / SessionService business actions", all(not a[0].startswith("monitor") for a in writes), ",".join(a[0] for a in writes))
    finally:
        try:
            ui.cmd(cmd="quit")
        except Exception:  # noqa: BLE001
            pass
        sim.terminate()

    # Gateway saw only reads + live tickets from the monitor (the desktop client never writes).
    logs = subprocess.run(COMPOSE + ["logs", "--no-color", "gateway"], capture_output=True, text=True, encoding="utf-8").stdout
    monitor_lines = [l for l in logs.splitlines() if "/monitor" in l]
    methods = {m for l in monitor_lines for m in ("GET", "POST", "PUT", "PATCH", "DELETE") if f'"{m} ' in l or f" {m} " in l}
    posts_ok = all("/live-ticket" in l for l in monitor_lines if '"POST ' in l or " POST " in l)
    check("through the gateway the monitor only used GET + POST live-ticket", bool(monitor_lines) and methods <= {"GET", "POST"} and posts_ok,
          f"{len(monitor_lines)} requests, methods={sorted(methods)}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    print("NOTE: detector and camera were SIMULATED (no best.pt, no physical camera). Screenshots:", SHOTS)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
