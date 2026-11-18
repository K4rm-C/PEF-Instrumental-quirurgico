#!/usr/bin/env bash
# MS-11 — READ-ONLY inventory of the PEF VM (CentOS Stream 10, Google Cloud).
#
#   sudo bash inspect_vm.sh > pef_inventory_$(hostname -s)_$(date +%Y%m%d-%H%M).txt 2>&1
#
# Guarantees (review before running):
#   * Changes NOTHING: no install, no restart, no service start/stop, no file is written (output goes to stdout only),
#     dnf is never run, no `git status` (it can rewrite the index), pip without cache or self-check. Only side effect:
#     `rpm -qa` refreshes SQLite's shared-memory index file rpmdb.sqlite-shm (no package data changes).
#   * PostgreSQL queries run in a READ ONLY transaction (PGOPTIONS default_transaction_read_only=on).
#   * Secrets are never printed: env files show KEY names only; password/secret/token lines are masked; TLS private keys
#     are listed (owner/permissions) but NEVER read. Redis/Mongo are only pinged (no AUTH is attempted).
#   * Every command has a timeout; a failing command is reported and the script continues.
# Send the resulting file back for the MS-11 comparison (scripts/vm/compare_inventory.py).
set -u
export LC_ALL=C PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=15000'
T=25
PSQL=(sudo -u postgres env "PGOPTIONS=$PGOPTIONS" psql)   # sudo resets the environment: pass the read-only option explicitly
MASK='s/((password|passwd|secret|token|api[_-]?key|authorization|requirepass|masterauth|rpc_secret|admin_token|metrics_token|keyFile)[^=:[:space:]]*[[:space:]]*[=:][[:space:]]*|((requirepass|masterauth|auth_basic_user_file)[[:space:]]+)).*/\1<MASKED>/I'

section() { printf '\n=================== %s ===================\n' "$*"; }
run() {   # run "<description>" cmd args...   (output masked)
  local d="$1"; shift
  printf '\n--- %s\n$ %s\n' "$d" "$*"
  timeout "$T" "$@" 2>&1 | sed -E "$MASK" | head -n 400
  local rc=${PIPESTATUS[0]}; [ "$rc" -ne 0 ] && printf '[exit %s]\n' "$rc"
  return 0
}
have() { command -v "$1" >/dev/null 2>&1; }
env_keys() {   # KEY=<set|empty|placeholder>, never the value
  awk -F= '/^[[:space:]]*#/ || NF<1 {next} {k=$1; v=substr($0, index($0,"=")+1);
    s=(v=="")?"empty":((v ~ /^</)?"placeholder":"set"); print k"=<"s">"}' "$1"
}

section "0. Run"
echo "inventory_version=ms11-1"; date -u +%FT%TZ; id -un; hostname -f 2>/dev/null || hostname

section "1. OS and resources"
run "os-release" cat /etc/os-release
run "kernel" uname -a
run "CPU" lscpu
run "memory" free -h
run "swap" swapon --show
run "uptime / load" uptime
run "GPU (if any)" sh -c 'lspci 2>/dev/null | grep -iE "vga|3d|nvidia" || echo none'
run "disks" df -hT -x tmpfs -x devtmpfs
run "inodes" df -i -x tmpfs -x devtmpfs
run "block devices" lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE
run "time sync (JWT expiry depends on it)" sh -c 'chronyc tracking 2>/dev/null || timedatectl'

section "2. Google Cloud metadata (read-only)"
for k in name zone machine-type tags network-interfaces/0/ip network-interfaces/0/access-configs/0/external-ip; do
  run "metadata $k" curl -s -m 5 -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/$k"
done

section "3. Packages (cache only, no refresh)"
run "relevant packages" sh -c "rpm -qa | grep -iE '^(python3|postgresql|redis|valkey|mongodb|mongo|nginx|httpd|openssl|policycoreutils|firewalld|certbot|garage|ffmpeg|mesa|libglvnd)' | sort"
run "python interpreters" sh -c 'ls -1 /usr/bin/python3* /usr/local/bin/python3* 2>/dev/null; python3 -V'
run "enabled repositories (files only; dnf is not run: it writes its own logs)" sh -c "grep -H -E '^(\[|enabled=)' /etc/yum.repos.d/*.repo"

section "4. systemd"
run "running services" systemctl list-units --type=service --state=running --no-pager --no-legend
run "failed units" systemctl list-units --state=failed --no-pager --no-legend
run "enabled unit files" systemctl list-unit-files --type=service --state=enabled --no-pager --no-legend
run "PEF and data units" systemctl list-unit-files 'pef*' 'postgresql*' 'redis*' 'valkey*' 'mongod*' 'garage*' 'nginx*' 'httpd*' 'gunicorn*' --no-pager
run "timers (backups?)" systemctl list-timers --all --no-pager
for u in $(systemctl list-unit-files 'pef*' --no-legend 2>/dev/null | awk '{print $1}'); do run "unit $u" systemctl cat "$u"; done
run "cron.d" sh -c 'ls -la /etc/cron.d /etc/cron.daily 2>/dev/null'

section "5. Listening ports and processes"
run "listening TCP/UDP" ss -ltnup
run "python / gunicorn / node / java processes" sh -c "ps -eo pid,user,etimes,args | grep -E 'python|gunicorn|uvicorn|flask|node|java|garage|mongod|redis|postgres' | grep -v grep"
run "conflicts with PEF ports" sh -c "ss -ltnH | awk '{print \$4}' | grep -E ':(80|443|500[0-9]|5432|6379|27017|390[0-3])$' || echo 'none of the PEF ports in use'"

section "6. Web front (nginx / Apache)"
if have nginx; then run "nginx version" nginx -v; run "nginx full config (masked)" nginx -T; else echo "nginx: not installed"; fi
if have httpd; then run "httpd version" httpd -v; run "httpd vhosts" httpd -S; else echo "httpd: not installed"; fi
run "nginx / httpd conf dirs" sh -c 'ls -la /etc/nginx/conf.d /etc/nginx/default.d /etc/httpd/conf.d 2>/dev/null'

section "7. TLS certificates (public part only; private keys are NOT read)"
certs=$(sh -c "nginx -T 2>/dev/null | awk '/ssl_certificate[[:space:]]/ {gsub(\";\",\"\",\$2); print \$2}' | sort -u")
for c in $certs /etc/letsencrypt/live/*/fullchain.pem; do
  [ -f "$c" ] && run "certificate $c" openssl x509 -in "$c" -noout -subject -issuer -dates -ext subjectAltName
done
run "private key files (owner/permissions only)" sh -c "ls -l /etc/pki/tls/private/ /etc/letsencrypt/live/*/privkey.pem 2>/dev/null"

section "8. PostgreSQL (read-only transaction)"
if have psql; then
  run "server" "${PSQL[@]}" -XAtc 'select version()'
  run "settings" "${PSQL[@]}" -XAtc "select name||'='||setting from pg_settings where name in ('listen_addresses','port','max_connections','data_directory','shared_buffers','wal_level','archive_mode','ssl')"
  run "databases and sizes" "${PSQL[@]}" -XAtc "select datname||' '||pg_size_pretty(pg_database_size(datname)) from pg_database where not datistemplate"
  run "roles (no passwords)" "${PSQL[@]}" -XAtc "select rolname||' login='||rolcanlogin||' super='||rolsuper from pg_roles where rolname !~ '^pg_'"
  run "pg_hba (rules)" "${PSQL[@]}" -XAtc "select type||' '||array_to_string(database,',')||' '||array_to_string(user_name,',')||' '||coalesce(address,'')||' '||auth_method from pg_hba_file_rules"
  for db in $("${PSQL[@]}" -XAtc "select datname from pg_database where not datistemplate and datname <> 'postgres'" 2>/dev/null); do
    run "[$db] PEF schema markers (migrations 001..016, seeds 002/003)" "${PSQL[@]}" -XAt -d "$db" -c "
      select 'm001 '||(to_regclass('public.cat_gender') is not null)
      union all select 'm004 '||exists(select 1 from information_schema.columns where table_name='media_asset' and column_name='session_id')
      union all select 'm005 '||coalesce((select exists(select 1 from cat_event_type where code='instruments_added')),false)
      union all select 'm006 '||(to_regclass('public.notification_outbox') is not null)
      union all select 'm007 '||(to_regclass('public.monitoring_check') is not null)
      union all select 'm008 '||(to_regclass('public.user_operating_room') is not null)
      union all select 'm010 '||(to_regclass('public.app_setting') is not null)
      union all select 'm011 '||exists(select 1 from pg_trigger where tgname='trg_work_session_model_pin_immutable')
      union all select 'm012 '||(to_regclass('public.capture_camera') is not null)
      union all select 'm013 '||(to_regclass('public.notification_delivery') is not null)
      union all select 'm014 '||(to_regclass('public.sync_change') is not null)
      union all select 'm015 '||(to_regclass('public.surgical_tray') is not null)
      union all select 'm016 '||exists(select 1 from pg_trigger where tgname='trg_work_session_tray_same_institution')"
    run "[$db] seeds and volumes (counts only, no personal data)" "${PSQL[@]}" -XAt -d "$db" -c "
      select 'seed002_session_statuses '||count(*) from cat_session_status
      union all select 'seed003_demo_users(@instrumed.com) '||count(*) from \"user\" where email like '%@instrumed.com'
      union all select 'institutions '||count(*) from institution
      union all select 'users '||count(*) from \"user\"
      union all select 'work_sessions '||count(*) from work_session
      union all select 'yolo_models(active) '||count(*) filter (where active)||'/'||count(*) from yolo_model
      union all select 'migration009_role_codes '||string_agg(distinct code, ',') from role"
  done
else echo "psql: not installed"; fi
run "PostgreSQL data dir usage" sh -c 'du -sh /var/lib/pgsql 2>/dev/null'

section "9. Redis / Valkey (ping only, no AUTH)"
for f in /etc/redis/redis.conf /etc/redis.conf /etc/valkey/valkey.conf; do
  [ -f "$f" ] && run "config $f (relevant, masked)" grep -E '^(bind|port|protected-mode|requirepass|appendonly|save|dir|dbfilename|maxmemory)' "$f"
done
have redis-cli && run "ping" redis-cli -h 127.0.0.1 -p 6379 ping
have valkey-cli && run "ping (valkey)" valkey-cli -h 127.0.0.1 -p 6379 ping
run "persistence files" sh -c 'ls -la /var/lib/redis /var/lib/valkey 2>/dev/null'

section "10. MongoDB (ping only)"
have mongod && run "version" mongod --version
[ -f /etc/mongod.conf ] && run "config (masked)" cat /etc/mongod.conf
have mongosh && run "ping" mongosh --quiet --norc --eval 'db.runCommand({ping:1})' mongodb://127.0.0.1:27017/
run "data dir usage" sh -c 'du -sh /var/lib/mongo 2>/dev/null'

section "11. Garage (S3)"
have garage && run "version" garage --version
for f in /etc/garage.toml /etc/garage/garage.toml; do [ -f "$f" ] && run "config $f (masked)" cat "$f"; done
have garage && run "cluster status" garage status
have garage && run "layout" garage layout show
have garage && run "buckets" garage bucket list
run "S3 port answers" curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3900/
run "admin health" curl -s -m 5 http://127.0.0.1:3903/health

section "12. Python services, venvs and install paths"
run "PEF tree" sh -c 'ls -la /opt/pef /opt/pef/releases /opt/pef/venvs /var/lib/pef /etc/pef 2>/dev/null'
run "deployed commit" sh -c 'git -c safe.directory="*" -C /opt/pef/current rev-parse HEAD 2>/dev/null; git -c safe.directory="*" -C /opt/pef/current log -1 --format="%h %ci %s" 2>/dev/null'
for v in /opt/pef/venvs/*/; do
  [ -x "$v/bin/python" ] && run "venv $v" sh -c "'$v/bin/python' -V; '$v/bin/python' -m pip list --format=freeze 2>/dev/null | grep -iE '^(flask|gunicorn|sqlalchemy|psycopg|redis|pyjwt|boto3|pymongo|ultralytics|torch|opencv|numpy|requests|flask-sock|simple-websocket)=='"
done
run "other virtualenvs on the machine" sh -c "find / -xdev -maxdepth 5 -name pyvenv.cfg -not -path '/proc/*' 2>/dev/null | head -50"
for f in /etc/pef/*.env; do [ -f "$f" ] && { printf '\n--- env keys %s (values hidden)\n' "$f"; env_keys "$f"; ls -l "$f"; }; done
run "YOLO weights on disk (names/sizes only)" sh -c "find / -xdev \( -name '*.pt' -o -name '*.onnx' \) -size +1M -not -path '/proc/*' -exec ls -lh {} + 2>/dev/null | head -30"
run "space for weights and evidence" sh -c 'df -h /var/lib /opt /var 2>/dev/null'

section "13. SELinux"
run "mode" sestatus
run "booleans" getsebool httpd_can_network_connect httpd_can_network_relay
have semanage && run "http_port_t ports" sh -c 'semanage port -l | grep -E "^http_port_t"'
run "contexts" sh -c 'ls -Zd /opt/pef /var/lib/pef /etc/pef /etc/nginx 2>/dev/null'
run "recent AVC denials" sh -c 'ausearch -m avc -ts today 2>/dev/null | tail -n 40 || echo none'

section "14. Local firewall"
run "firewalld" sh -c 'firewall-cmd --state && firewall-cmd --list-all'
run "nftables (first lines)" sh -c 'nft list ruleset 2>/dev/null | head -n 120'

section "15. Connectivity between components (localhost only)"
for p in 5000 5001 5003 5004 5005 5006 5007 5008 5009; do
  run "127.0.0.1:$p/health/live" curl -s -m 5 -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$p/health/live"
done
run "127.0.0.1:5002/health (VisionWorker)" curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5002/health
have pg_isready && run "PostgreSQL" pg_isready -h 127.0.0.1 -p 5432
run "HTTPS front on this VM" curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://127.0.0.1/

section "END"
echo "inventory complete (read-only)"
