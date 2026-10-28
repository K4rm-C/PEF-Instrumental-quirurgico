"""Isolated, reproducible E2E environment of the PEF (see GUIA_PRUEBAS.md).

The E2E scripts change persistent state on purpose: they schedule and close sessions, mark pieces
`lost` (stock), create users / institutions / families, upload evidence to Garage, write MongoDB
checkpoints and stop / start containers. Run against a shared database they accumulate history,
exhaust stock and make results depend on previous runs. This runner gives them their own environment:

- compose project `pef_e2e` (its own containers, network and VOLUMES: postgres, redis, mongo, garage),
  ports 56xxx, secrets generated once into .e2e/e2e.env (git-ignored);
- the database is created by the postgres initdb from data/ (schema + catalogs + demo seed), so every
  fresh environment starts from the same users, kits and stock;
- the portal (BackendWebFlask) and VisionWorker run in one of two modes (--mode, remembered in
  .e2e/mode by `up`):
    host           Python processes on the host (`host` starts them), the collector checks them through
                   host.docker.internal (docker-compose.hostdev.yml);
    containerized  the `web` and `vision` services of docker-compose.yml inside the project (the images of
                   the integral deployment), plus scripts/e2e/compose.containerized.yml (outbox bind mount);
  either way the portal is on :56000, AuthService on :56001 and VisionWorker on :56002, so the isolated
  environment coexists with the main deployment (:5000 / :5001 / :5002). The scripts get the URLs in
  PEF_WEB_URL / PEF_AUTH_URL / PEF_VISION_URL and the compose files in COMPOSE_FILE.

`reset` deletes ONLY the volumes of the `pef_e2e` project (they are created by this runner). It never
touches other compose projects (e.g. pef_validacion), other containers, or any other volume; `all`
checks that the containers outside the project are unchanged after the run.

usage:
  python scripts/e2e/isolated.py all [--mode host|containerized] [--suites ...]
                                   reset + up + (host) + run + (stop host) + stop project
  python scripts/e2e/isolated.py up [--mode ...] | host | run [--suites ...] | stop-host | down | reset | env | status
python: --web-python (portal + E2E scripts) and --vision-python (VisionWorker, videos), or the env
        PEF_E2E_WEB_PYTHON / PEF_E2E_VISION_PYTHON; default: this interpreter.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".e2e"
ENV_FILE = STATE / "e2e.env"
PIDS = STATE / "host_pids.json"
LOGS = STATE / "logs"
MODE_FILE = STATE / "mode"
MODES = ("host", "containerized")
PROJECT = "pef_e2e"
SERVICES = ["postgres", "redis", "mongo", "garage", "auth", "catalog", "session", "evidence", "audit",
            "notifications", "notifications-worker", "monitoring", "monitoring-collector"]
PORTS = {"POSTGRES_PORT": 56433, "REDIS_PORT": 56380, "MONGO_PORT": 37117, "S3_API_PORT": 56900,
         "GARAGE_RPC_PORT": 56901, "GARAGE_ADMIN_PORT": 56903, "AUTH_PORT": 56001, "CATALOG_PORT": 56003,
         "SESSION_PORT": 56004, "EVIDENCE_PORT": 56005, "AUDIT_PORT": 56006, "NOTIFICATIONS_PORT": 56007,
         "MONITORING_PORT": 56008, "WEB_PORT": 56000, "VISION_PORT": 56002}
HOST_PORTS = {"web": PORTS["WEB_PORT"], "vision": PORTS["VISION_PORT"]}
# Values derived from the ports: rewritten in an existing e2e.env (its secrets are kept) when they change.
MANAGED = {**{key: str(value) for key, value in PORTS.items()},
           "AUTH_ALLOWED_ORIGINS": f"http://localhost:{PORTS['WEB_PORT']},http://127.0.0.1:{PORTS['WEB_PORT']}",
           "AUTH_BROWSER_URL": f"http://127.0.0.1:{PORTS['AUTH_PORT']}",
           "S3_PUBLIC_ENDPOINT": f"http://127.0.0.1:{PORTS['S3_API_PORT']}"}
KIT = "51515151-0000-4000-8000-000000000001"
STOCK_PER_RUN = 4          # pieces of one demo family that a full run marks lost (the seed has 5)
# name -> (script, needs videos). Order matters only for readability; each suite is self-contained.
SUITES = {
    "identity": ("scripts/e2e_identity.py", False),
    "catalog": ("scripts/e2e_catalog.py", False),
    "admin": ("scripts/e2e_admin.py", False),
    "ops": ("scripts/e2e_ops.py", False),
    "session": ("scripts/e2e_session.py", True),
    "notifications": ("scripts/e2e_notifications.py", True),
    "audit": ("scripts/e2e_audit.py", True),
    "reports": ("scripts/e2e_reports.py", True),
    "mongo": ("scripts/e2e_mongo_ds06.py", True),
    "monitoring": ("scripts/e2e_monitoring.py", False),
    "evidence-unit": ("apps/EvidenceService", False),      # pytest of EvidenceService, real Garage included
}


# --- configuration --------------------------------------------------------------------------------


def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        STATE.mkdir(exist_ok=True)
        values = {
            "POSTGRES_DB": "InstruMed", "POSTGRES_USER": "pef_e2e", "POSTGRES_PASSWORD": secrets.token_hex(16),
            "REDIS_PASSWORD": secrets.token_hex(16), "MONGO_USER": "pef_mongo", "MONGO_PASSWORD": secrets.token_hex(16),
            # Garage key format: GK + 24 hex, secret 64 hex.
            "S3_ACCESS_KEY": "GK" + secrets.token_hex(12), "S3_SECRET_KEY": secrets.token_hex(32),
            "S3_BUCKET_EVIDENCE": "evidence", "S3_REGION": "garage",
            "JWT_SECRET_KEY": secrets.token_hex(32), "JWT_ALGORITHM": "HS256", "SESSION_SECRET_KEY": secrets.token_hex(32),
            "AUTH_RETURN_RESET_TOKEN": "true", "MONITORING_INTERVAL_SECONDS": "10", **MANAGED,
        }
        write_env(values)
        print(f"created {ENV_FILE.relative_to(ROOT)} (new secrets)")
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    if any(env.get(key) != value for key, value in MANAGED.items()):
        env.update(MANAGED)
        write_env(env)
        print(f"updated the ports / URLs of {ENV_FILE.relative_to(ROOT)} (secrets kept)")
    return env


def write_env(values: dict[str, str]) -> None:
    ENV_FILE.write_text("# Generated by scripts/e2e/isolated.py for the pef_e2e project. Do not commit.\n"
                        + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


def current_mode(args) -> str:
    """--mode, else the mode of the last `up` (.e2e/mode), else host."""
    if getattr(args, "mode", None):
        return args.mode
    return MODE_FILE.read_text(encoding="utf-8").strip() if MODE_FILE.exists() else "host"


def compose_files(mode: str) -> list[str]:
    extra = "docker-compose.hostdev.yml" if mode == "host" else "scripts/e2e/compose.containerized.yml"
    return [str(ROOT / "docker-compose.yml"), str(ROOT / extra)]


def compose_env(mode: str) -> dict[str, str]:
    """Process environment of every `docker compose` call (runner and scripts): files + collector targets."""
    if mode == "host":
        targets = {"MONITORING_TARGET_WEB_URL": f"http://host.docker.internal:{HOST_PORTS['web']}",
                   "MONITORING_TARGET_VISION_URL": f"http://host.docker.internal:{HOST_PORTS['vision']}"}
    else:
        targets = {"MONITORING_TARGET_WEB_URL": "http://web:5000", "MONITORING_TARGET_VISION_URL": "http://vision:5002"}
    return {**os.environ, "COMPOSE_FILE": os.pathsep.join(compose_files(mode)), "COMPOSE_PATH_SEPARATOR": os.pathsep,
            **targets}


def process_env(cfg: dict[str, str], mode: str) -> dict[str, str]:
    """Environment of the host processes (portal, VisionWorker) and of the E2E scripts."""
    pg = f"{cfg['POSTGRES_USER']}:{cfg['POSTGRES_PASSWORD']}@127.0.0.1:{cfg['POSTGRES_PORT']}/{cfg['POSTGRES_DB']}"
    url = lambda key: f"http://127.0.0.1:{cfg[key]}"  # noqa: E731
    return {
        **compose_env(mode),
        "PEF_WEB_URL": url("WEB_PORT"), "PEF_AUTH_URL": url("AUTH_PORT"), "PEF_VISION_URL": url("VISION_PORT"),
        "DATABASE_URL": f"postgresql+psycopg://{pg}", "DB_HOST": "127.0.0.1", "DB_PORT": cfg["POSTGRES_PORT"],
        "DB_NAME": cfg["POSTGRES_DB"], "DB_USER": cfg["POSTGRES_USER"], "DB_PASSWORD": cfg["POSTGRES_PASSWORD"],
        "REDIS_URL": f"redis://:{cfg['REDIS_PASSWORD']}@127.0.0.1:{cfg['REDIS_PORT']}/0",
        "JWT_SECRET_KEY": cfg["JWT_SECRET_KEY"], "JWT_ALGORITHM": cfg["JWT_ALGORITHM"],
        "SESSION_SECRET_KEY": cfg["SESSION_SECRET_KEY"],
        "AUTH_SERVICE_URL": url("AUTH_PORT"), "AUTH_BROWSER_URL": url("AUTH_PORT"),
        "AUTH_ALLOWED_ORIGINS": cfg["AUTH_ALLOWED_ORIGINS"], "AUTH_COOKIE_SECURE": "false", "AUTH_COOKIE_SAMESITE": "Lax",
        "AUTH_RETURN_RESET_TOKEN": "true", "FRONTEND_DEMO_MODE": "false",
        "VISION_WORKER_URL": url("VISION_PORT"), "VISION_WORKER_PORT": cfg["VISION_PORT"],
        "MONGO_URL": f"mongodb://{cfg['MONGO_USER']}:{cfg['MONGO_PASSWORD']}@127.0.0.1:{cfg['MONGO_PORT']}/?authSource=admin",
        "MONGO_DB": "pef", "PEF_OUTBOX_DIR": str(STATE / "outbox"),
        "EVIDENCE_SERVICE_URL": url("EVIDENCE_PORT"), "CATALOG_SERVICE_URL": url("CATALOG_PORT"),
        "SESSION_SERVICE_URL": url("SESSION_PORT"), "AUDIT_SERVICE_URL": url("AUDIT_PORT"),
        "NOTIFICATIONS_SERVICE_URL": url("NOTIFICATIONS_PORT"), "MONITORING_SERVICE_URL": url("MONITORING_PORT"),
        "MONITORING_INTERVAL_SECONDS": cfg["MONITORING_INTERVAL_SECONDS"],
        "COMPOSE_ENV_FILE": str(ENV_FILE), "COMPOSE_PROJECT": PROJECT, "WEB_DIR": str(ROOT / "apps/BackendWebFlask"),
        "VISION_UP": "1", "PYTHONIOENCODING": "utf-8",
    }


def compose(mode, *args, check=True, capture=False):
    command = ["docker", "compose", "--env-file", str(ENV_FILE), "-p", PROJECT, *args]
    return subprocess.run(command, cwd=ROOT, check=check, text=True, env=compose_env(mode),
                          stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None)


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait(label: str, predicate, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            print(f"  ready: {label}")
            return
        time.sleep(2)
    raise SystemExit(f"timeout waiting for {label}")


def port_busy(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def other_containers() -> list[str]:
    """Containers outside the project (id, name, status, start time): must be unchanged by a run."""
    out = subprocess.run(["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}} {{.State}} {{.CreatedAt}}"],
                         text=True, capture_output=True, check=True).stdout.splitlines()
    rows = []
    for line in out:
        if f" {PROJECT}-" in line:
            continue
        cid = line.split()[0]
        started = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", cid], text=True,
                                 capture_output=True).stdout.strip()
        rows.append(f"{line} started={started}")
    return sorted(rows)


# --- commands -------------------------------------------------------------------------------------


def cmd_up(cfg, args) -> None:
    mode = current_mode(args)
    STATE.mkdir(exist_ok=True)
    (STATE / "outbox").mkdir(exist_ok=True)       # bind-mounted into `web` in the containerized mode
    services = SERVICES + (["web", "vision"] if mode == "containerized" else [])
    print(f"== up: project {PROJECT}, mode {mode} (ports {cfg['POSTGRES_PORT']}… / web {cfg['WEB_PORT']} / "
          f"auth {cfg['AUTH_PORT']} / vision {cfg['VISION_PORT']})")
    compose(mode, "up", "-d", "--build", "--remove-orphans", *services)
    MODE_FILE.write_text(mode, encoding="utf-8")
    url = lambda key, path="/health/ready": f"http://127.0.0.1:{cfg[key]}{path}"  # noqa: E731
    wait("garage", lambda: http_ok(url("GARAGE_ADMIN_PORT", "/health")))
    for key in ("AUTH_PORT", "CATALOG_PORT", "SESSION_PORT", "EVIDENCE_PORT", "AUDIT_PORT", "NOTIFICATIONS_PORT",
                "MONITORING_PORT"):
        wait(key.removesuffix("_PORT").lower(), lambda key=key: http_ok(url(key)))
    if mode == "containerized":
        wait("web (container)", lambda: http_ok(url("WEB_PORT")))
        wait("vision (container)", lambda: http_ok(url("VISION_PORT", "/health")), seconds=300)


def cmd_reset(cfg, args) -> None:
    assert PROJECT == "pef_e2e"            # the only project whose volumes this runner may delete
    print(f"== reset: removing containers and volumes of project {PROJECT} only")
    cmd_stop_host(cfg, args)
    # Both files sets, so `web` / `vision` of a previous containerized run are removed too.
    compose("containerized", "down", "-v", "--remove-orphans")
    for leftover in (STATE / "outbox").glob("*.json"):      # the outbox of the deleted environment
        leftover.unlink()


def _python(kind: str, args) -> str:
    return getattr(args, f"{kind}_python") or os.getenv(f"PEF_E2E_{kind.upper()}_PYTHON") or sys.executable


def cmd_host(cfg, args) -> None:
    if current_mode(args) != "host":
        raise SystemExit("`host` belongs to the host mode: the containerized mode runs web / vision in the project")
    busy = [name for name, port in HOST_PORTS.items() if port_busy(port)]
    if busy:
        raise SystemExit(f"ports in use: {busy} — stop the other portal / VisionWorker first")
    LOGS.mkdir(parents=True, exist_ok=True)
    (STATE / "outbox").mkdir(exist_ok=True)
    env = process_env(cfg, "host")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    pids = {}
    for name, cwd, command in (
        ("web", ROOT / "apps/BackendWebFlask", [_python("web", args), "-m", "flask", "--app", "app", "run",
                                                 "--host", "127.0.0.1", "--port", str(HOST_PORTS["web"])]),
        ("vision", ROOT / "apps/VisionWorker", [_python("vision", args), "main.py"]),
    ):
        log = open(LOGS / f"{name}.log", "w", encoding="utf-8")
        pids[name] = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      creationflags=flags, start_new_session=os.name != "nt").pid
    PIDS.write_text(json.dumps(pids), encoding="utf-8")
    wait("web", lambda: http_ok(f"http://127.0.0.1:{HOST_PORTS['web']}/health"))
    wait("vision", lambda: http_ok(f"http://127.0.0.1:{HOST_PORTS['vision']}/health"), seconds=300)


def cmd_stop_host(cfg, args) -> None:
    if not PIDS.exists():
        return
    for name, pid in json.loads(PIDS.read_text(encoding="utf-8")).items():
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(pid, 15)
            except OSError:
                pass
        print(f"  stopped {name} (pid {pid})")
    PIDS.unlink()


def cmd_down(cfg, args) -> None:
    cmd_stop_host(cfg, args)
    compose(current_mode(args), "stop")    # keeps the project's volumes (reset deletes them)


def videos_dir(args) -> Path:
    target = Path(args.videos) if args.videos else STATE / "videos"
    if not (target / "tray_full.mp4").exists():
        target.mkdir(parents=True, exist_ok=True)
        print(f"  generating test videos in {target}")
        subprocess.run([_python("vision", args), str(ROOT / "scripts/make_test_tray_videos.py"), str(target)],
                       cwd=ROOT, check=True)
    return target


def stock(cfg, mode) -> dict[str, int]:
    """Available pieces per family of the demo kit (shows that runs do not exhaust a shared stock)."""
    sql = ("SELECT k.family_id::text || '=' || count(i.id) FILTER (WHERE c.code = 'available' AND i.active) "
           "FROM kit_item k LEFT JOIN instrument i ON i.family_id = k.family_id "
           "LEFT JOIN cat_instrument_cycle_status c ON c.id = i.cycle_status_id "
           f"WHERE k.kit_id = '{KIT}' GROUP BY k.family_id ORDER BY 1")
    out = compose(mode, "exec", "-T", "postgres", "psql", "-U", cfg["POSTGRES_USER"], "-d", cfg["POSTGRES_DB"], "-tAc", sql,
                  capture=True, check=False).stdout
    return {line.split("=")[0][-4:]: int(line.split("=")[1]) for line in out.splitlines() if "=" in line}


def cmd_run(cfg, args) -> int:
    mode = current_mode(args)
    env = process_env(cfg, mode)
    print(f"== run: mode {mode}, web {env['PEF_WEB_URL']}, auth {env['PEF_AUTH_URL']}, vision {env['PEF_VISION_URL']}")
    names = args.suites or list(SUITES)
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        raise SystemExit(f"unknown suites: {unknown} (known: {', '.join(SUITES)})")
    videos = videos_dir(args) if any(SUITES[n][1] for n in names) else None
    LOGS.mkdir(parents=True, exist_ok=True)
    before = stock(cfg, mode)
    print(f"  stock before: {before}")
    if before and min(before.values()) < STOCK_PER_RUN:
        # The suites mark pieces `lost` on purpose (safe-close rules); `all` / `reset` restores the seed stock.
        print(f"  WARNING: a family of the demo kit has fewer than {STOCK_PER_RUN} available pieces: "
              "run `reset` (or `all`) to start again from the seed")
    results = []
    for name in names:
        target, needs_videos = SUITES[name]
        if name == "evidence-unit":
            suite_env = {**env, "EVIDENCE_TEST_DATABASE_URL": env["DATABASE_URL"], "EVIDENCE_TEST_REDIS_URL": env["REDIS_URL"],
                         "EVIDENCE_TEST_S3_ACCESS_KEY": cfg["S3_ACCESS_KEY"], "EVIDENCE_TEST_S3_SECRET_KEY": cfg["S3_SECRET_KEY"],
                         "EVIDENCE_TEST_S3_API_PORT": cfg["S3_API_PORT"], "EVIDENCE_TEST_S3_REGION": cfg["S3_REGION"],
                         "EVIDENCE_TEST_S3_BUCKET_EVIDENCE": cfg["S3_BUCKET_EVIDENCE"]}
            command, cwd = [_python("web", args), "-m", "pytest", "-q", "-p", "no:cacheprovider"], ROOT / target
        else:
            suite_env, cwd = env, ROOT
            command = [_python("web", args), target] + ([str(videos)] if needs_videos else [])
        started = time.monotonic()
        with open(LOGS / f"{name}.log", "w", encoding="utf-8") as log:
            code = subprocess.run(command, cwd=cwd, env=suite_env, stdout=log, stderr=subprocess.STDOUT).returncode
        lines = (LOGS / f"{name}.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
        summary = lines[-1] if lines else ""
        results.append((name, code, summary))
        print(f"  {name:14} {'OK ' if code == 0 else 'FAIL'} {time.monotonic() - started:6.1f}s  {summary}")
    print(f"  stock after:  {stock(cfg, mode)}")
    failed = [name for name, code, _ in results if code]
    print(f"== {len(results) - len(failed)}/{len(results)} suites passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


def cmd_all(cfg, args) -> int:
    before = other_containers()
    cmd_reset(cfg, args)
    cmd_up(cfg, args)
    if current_mode(args) == "host":
        cmd_host(cfg, args)
    try:
        code = cmd_run(cfg, args)
    finally:
        cmd_down(cfg, args)
    after = other_containers()
    changed = sorted(set(before) ^ set(after))
    print("== containers outside pef_e2e: " + ("unchanged" if not changed else f"CHANGED: {changed}"))
    return code or (1 if changed else 0)


def cmd_env(cfg, args) -> None:
    env = process_env(cfg, current_mode(args))
    for key in ("PEF_WEB_URL", "PEF_AUTH_URL", "PEF_VISION_URL", "DATABASE_URL", "REDIS_URL", "MONGO_URL", "CATALOG_SERVICE_URL", "SESSION_SERVICE_URL",
                "EVIDENCE_SERVICE_URL", "AUDIT_SERVICE_URL", "NOTIFICATIONS_SERVICE_URL", "MONITORING_SERVICE_URL",
                "COMPOSE_ENV_FILE", "COMPOSE_PROJECT", "COMPOSE_FILE"):
        print(f"{key}={env[key]}")


def cmd_status(cfg, args) -> None:
    print(f"mode: {current_mode(args)}")
    compose(current_mode(args), "ps")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["all", "up", "host", "run", "stop-host", "down", "reset", "env", "status"])
    parser.add_argument("--mode", choices=MODES, help="portal / VisionWorker: host processes or project containers "
                        "(default: the mode of the last `up`, else host)")
    parser.add_argument("--suites", nargs="*", help=f"subset of: {', '.join(SUITES)}")
    parser.add_argument("--videos", help="directory with tray_full.mp4 / tray_masked.mp4 (generated if missing)")
    parser.add_argument("--web-python", help="interpreter of the portal and the E2E scripts")
    parser.add_argument("--vision-python", help="interpreter of VisionWorker (and the video generator)")
    args = parser.parse_args()
    cfg = load_env()
    handler = {"all": cmd_all, "up": cmd_up, "host": cmd_host, "run": cmd_run, "stop-host": cmd_stop_host,
               "down": cmd_down, "reset": cmd_reset, "env": cmd_env, "status": cmd_status}[args.command]
    return handler(cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())
