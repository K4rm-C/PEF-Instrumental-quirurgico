# PEF — Despliegue y actualización en Google Cloud (VM CentOS Stream 10, sin Docker)

Alcance: **procedimiento preparado, no ejecutado**. Nadie se ha conectado a la VM ni ha desplegado nada (MS-09, MS-10, MS-11A/B). Docker Compose solo se usa en el entorno aislado de pruebas. Los servicios PEF corren como unidades systemd con un venv de Python por servicio.

**Reglas de esta guía**

1. La VM **no** se supone vacía. Primero se hace el inventario (§1, solo lectura) y se rellena la tabla de decisiones (§2). Ningún comando modificador se ejecuta antes de eso.
2. Los bloques se marcan **[INSPECCIÓN]** (no cambian nada) o **[MODIFICA]** (cambian la VM). Cada bloque [MODIFICA] va precedido de su respaldo y no sobrescribe archivos existentes: copia con `cp -n`, compara con `diff` y deja la decisión al responsable.
3. Ningún secreto en el repositorio ni en la línea de comandos: los valores reales vienen de Secret Manager y se escriben en `/etc/pef/*.env` (`root:pef 0640`).

## 1. Inventario de la VM [INSPECCIÓN]

**Inventario completo (MS-11):** `sudo bash scripts/vm/inspect_vm.sh > inventario.txt 2>&1` (solo lectura; secretos enmascarados; probado en CentOS Stream 10). Después, en tu equipo: `python scripts/vm/compare_inventory.py inventario.txt --md comparacion.md`. Los comandos de abajo son la versión rápida a mano.

```bash
# Sistema
cat /etc/os-release; uname -r; nproc; free -h; df -h / /var /opt
getenforce; sudo firewall-cmd --list-all
# Lo que ya escucha (¿algún puerto choca con 5000-5009, 5432, 6379, 27017, 3900-3903, 80, 443?)
sudo ss -ltnp
# Servicios y unidades existentes
systemctl list-units --type=service --state=running --no-pager
systemctl list-unit-files 'pef*' 'postgresql*' 'redis*' 'valkey*' 'mongod*' 'garage*' 'nginx*' --no-pager
# Paquetes ya instalados
rpm -q python3 python3-devel postgresql-server valkey redis nginx mongodb-org 2>&1
command -v garage && garage --version
# Configuración existente (leer, no editar)
ls -la /etc/nginx/conf.d/ /etc/pef/ /opt/pef/ /var/lib/pef/ 2>&1
sudo nginx -T 2>/dev/null | grep -nE 'server_name|listen|location = /ws' | head -50
# PostgreSQL existente: versión, bases y si el esquema PEF ya está
sudo -u postgres psql -Atc 'select version()'
sudo -u postgres psql -Atc '\l' | cut -d'|' -f1
sudo -u postgres psql -d pef -Atc "select to_regclass('public.work_session'), to_regclass('public.surgical_tray'), to_regclass('public.sync_change')" 2>&1
# Usuarios del sistema PEF
getent passwd pef-svc pef-web pef-vision; getent group pef
# Certificados
sudo ls -la /etc/pki/tls/certs/ /etc/pki/tls/private/ | grep -i pef
```

## 2. Decisiones a partir del inventario

| Pregunta | Si ya existe | Si no existe |
| :--- | :--- | :--- |
| ¿PostgreSQL 16 en marcha? | Reusar; crear solo la base y el rol PEF si faltan (§4.3) | Instalar (§4.2) |
| ¿Base `pef` con esquema? | **Actualización** (§7): no ejecutar `001_init.sql` ni seeds | **Instalación nueva** (§4.3) |
| ¿Redis/Valkey / MongoDB / Garage? | Reusar con credenciales propias de PEF (base Redis, base Mongo y buckets separados) | Instalar ligado a `127.0.0.1` |
| ¿Puerto 5000–5009 ocupado? | Cambiar el puerto en la unidad `.service`, en `/etc/pef/*.env` y en `gateway.env`, y volver a correr `scripts/verify_architecture.py` en una copia | — |
| ¿nginx con otros sitios? | Añadir solo `conf.d/pef-*.conf`; no tocar los demás `server` | Instalar y habilitar |
| ¿Certificados? | Reusar si cubren los dominios del portal, el API y (opcional) S3 | Emitir (CA del hospital o ACME) antes de §4.7 |

## 3. Topología y requisitos

```
Internet ──443──> nginx (Gateway /api/*, portal, WSS /ws, front TLS de Garage opcional)
                     │  127.0.0.1 únicamente
   ┌─────────────────┼───────────────────────────────────────────────────────┐
   │ pef-web :5000   pef-auth :5001   pef-vision :5002   pef-catalog :5003   │
   │ pef-session :5004  pef-evidence :5005  pef-audit :5006                  │
   │ pef-notifications :5007 (+worker)  pef-monitoring :5008 (+collector)    │
   │ pef-integration :5009 (+worker)                                          │
   │ PostgreSQL :5432  Redis :6379  MongoDB :27017  Garage :3900 (3901/3903)  │
   └──────────────────────────────────────────────────────────────────────────┘
```

Público: **443** (80 solo redirige). SSH por IAP. Nada más.

| Componente | Versión / nota |
| :--- | :--- |
| Python | `python3` 3.12 del sistema (CentOS Stream 10 **no** tiene paquete `python3.12`); un venv por servicio en `/opt/pef/venvs/<App>` |
| PostgreSQL | paquete `postgresql-server` (16.x en CentOS Stream 10), como el entorno de pruebas |
| Redis → **Valkey** | CentOS Stream 10 **no** trae `redis`: trae `valkey` 8.1 (mismo protocolo; probado en MS-11: Auth 199, Session 752, Integration 47 sin fallos). Contraseña, `bind 127.0.0.1` (revocación JWT `auth:jwt:*`, límites) |
| MongoDB | autenticación, `bindIp 127.0.0.1` (checkpoints DS06) |
| Garage | `127.0.0.1:3900` (S3); 3901/3903 nunca públicos; buckets `pef-evidence` y `pef-admin` |
| nginx | del sistema; configuración renderizada por `scripts/render_gateway_vm.py` |
| VisionWorker | Ultralytics/torch (CPU) en su venv; pesos descargados de Garage y verificados por SHA-256 |

## 4. Primera instalación [MODIFICA]

### 4.1 Usuarios, directorios y código

```bash
getent group pef || sudo groupadd --system pef
for u in pef-svc pef-web pef-vision; do
  getent passwd $u || sudo useradd --system --gid pef --home-dir /opt/pef --shell /sbin/nologin $u
done
sudo install -d -o root -g pef -m 0750 /opt/pef /opt/pef/releases /opt/pef/venvs /etc/pef
sudo install -d -o pef-web -g pef -m 0750 /var/lib/pef/web /var/lib/pef/web/outbox
sudo install -d -o pef-vision -g pef -m 0750 /var/lib/pef/vision /var/lib/pef/vision/model_cache \
     /var/lib/pef/vision/uploads /var/lib/pef/vision/spool /var/lib/pef/vision/weights
sudo install -d -o pef-svc -g pef -m 0750 /var/lib/pef/run
# Una versión = un commit verificado (sin .env, sin .pt, sin datos)
sudo git -C /opt/pef/releases clone --branch <rama> <repo> <commit> && sudo git -C /opt/pef/releases/<commit> checkout <commit>
sudo ln -sfn /opt/pef/releases/<commit> /opt/pef/current
```

### 4.2 Dependencias del sistema y venvs

```bash
sudo dnf install -y python3-devel gcc nginx postgresql-server valkey policycoreutils-python-utils   # solo lo que faltó en §1
for app in BackendAuthService BackendWebFlask VisionWorker CatalogService SessionService EvidenceService \
           AuditService NotificationsService MonitoringService IntegrationService; do
  [ -d /opt/pef/venvs/$app ] || sudo python3 -m venv /opt/pef/venvs/$app
  sudo /opt/pef/venvs/$app/bin/pip install -r /opt/pef/current/apps/$app/requirements.txt gunicorn
done
```

MongoDB y Garage no están en los repositorios de CentOS: instalar desde el repositorio oficial de MongoDB y el binario firmado de Garage, con su configuración en `127.0.0.1` (plantilla: `infra/garage.toml.example`; secretos `rpc_secret` y `admin_token` nuevos).

### 4.3 PostgreSQL: base nueva

`001_init.sql` **no es idempotente**: solo se aplica a una base vacía. `data/seeds/002_seed_catalogs.sql` es **obligatorio** (catálogos), `data/seeds/003_seed_demo.sql` es **solo para demos y nunca se aplica en producción**. 004–016 son idempotentes y aditivas.

```bash
sudo -u postgres psql -Atc "select 1 from pg_roles where rolname='pef_app'" | grep -q 1 || \
  sudo -u postgres psql -c "create role pef_app login"            # contraseña: \password pef_app (de Secret Manager)
sudo -u postgres psql -Atc "select 1 from pg_database where datname='pef'" | grep -q 1 || \
  sudo -u postgres createdb -O pef_app pef
R=/opt/pef/current
sudo -u postgres psql -v ON_ERROR_STOP=1 -d pef -f $R/data/migrations/001_init.sql      # SOLO base vacía
sudo -u postgres psql -v ON_ERROR_STOP=1 -d pef -f $R/data/seeds/002_seed_catalogs.sql   # obligatorio
for n in 004 005 006 007 008 009 010 011 012 013 014 015 016; do
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d pef -f $R/data/migrations/${n}_*.sql
done
# (003_seed_demo.sql NO)
```

La misma secuencia se verificó en una base nueva, dos veces, con `scripts/check_migrations_concurrency.py` (MS-10; la segunda pasada omite 001 y 003). Monitoring, Notifications e Integration vuelven a aplicar sus migraciones al arrancar bajo el bloqueo compartido `pef:schema:migration`, sin *deadlock* (5 arranques simultáneos).

### 4.4 Valkey (Redis), MongoDB y Garage

- Valkey (`/etc/valkey/valkey.conf`, unidad `valkey.service`, CLI `valkey-cli`): `requirepass` desde Secret Manager, `bind 127.0.0.1 -::1`, `protected-mode yes`. `REDIS_URL` no cambia (`redis://:<password>@127.0.0.1:6379/0`). Si ya existe un Redis o Valkey compartido, usar una base distinta (`/1`). Las unidades PEF se ordenan después de `redis.service` **y** `valkey.service`.
- MongoDB: usuario `pef` con `readWrite` solo sobre su base; `security.authorization: enabled`.
- Garage: crear la *layout*, la clave de acceso PEF y los buckets `pef-evidence` (EvidenceService) y `pef-admin` (fotos de familias, avisos de privacidad y **pesos de modelos**). Dar a la clave PEF permisos solo sobre esos buckets.

### 4.5 Entornos y secretos

```bash
for f in /opt/pef/current/infra/vm/env/*.env.example; do
  sudo cp -n "$f" /etc/pef/$(basename "$f" .example)     # -n: nunca sobrescribe un archivo existente
done
sudo chown root:pef /etc/pef/*.env && sudo chmod 0640 /etc/pef/*.env
# Completar cada <...> desde Secret Manager (JWT_SECRET_KEY idéntico en todos; VISION_WS_SECRET igual en web y vision;
# credenciales S3 en evidence.env y web.env). Luego:
sudo /opt/pef/venvs/BackendWebFlask/bin/python /opt/pef/current/scripts/preflight_production.py --env-dir /etc/pef --strict
```

El *preflight* falla si queda un marcador `<...>`, un secreto débil o repetido, una URL pública sin TLS, cookies inseguras, modo demo o, desde MS-10, `ADMIN_STORAGE_BACKEND=s3` sin credenciales o sin bucket.

### 4.6 systemd

```bash
for u in /opt/pef/current/infra/vm/systemd/pef-*.service /opt/pef/current/infra/vm/systemd/pef.target; do
  t=/etc/systemd/system/$(basename $u)
  if [ -e "$t" ]; then diff -u "$t" "$u" || echo "REVISAR: $t difiere (no se sobrescribe)"; else sudo install -m 0644 -o root -g root "$u" "$t"; fi
done
sudo systemctl daemon-reload
sudo systemctl enable --now pef.target
```

Las 13 unidades escuchan solo en `127.0.0.1`, con `NoNewPrivileges`, `ProtectSystem=strict` y `ReadWritePaths` limitados a `/var/lib/pef/*`.

### 4.7 nginx, Gateway, TLS y WSS

```bash
sudo cp -n /opt/pef/current/infra/vm/nginx/gateway.env.example /etc/pef/gateway.env   # completar dominios
S=$(mktemp -d)
sudo /opt/pef/venvs/BackendWebFlask/bin/python /opt/pef/current/scripts/render_gateway_vm.py --env /etc/pef/gateway.env \
     --out $S --cert /etc/pki/tls/certs/pef-fullchain.pem --key /etc/pki/tls/private/pef-privkey.pem [--garage-host s3.<dominio>]
diff -ru /etc/nginx/conf.d/pef $S/pef 2>/dev/null; ls $S          # revisar antes de instalar
sudo cp -rn $S/* /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

- TLS: certificado que cubra el portal, el API y, si se usa, `s3.<dominio>`. La clave privada debe ser `root:root 0600`.
- WSS: `location = /ws` (plantilla `ws.conf`) hace el *upgrade* hacia VisionWorker `127.0.0.1:5002`. `VISION_WORKER_WS_URL=wss://<portal>/ws` y `VISION_WS_ORIGINS=https://<portal>`. El worker valida el `Origin` y el ticket HMAC.
- Garage hacia afuera: solo mediante el front TLS opcional (`garage-s3.conf.template`), para las descargas firmadas de evidencias.

### 4.8 SELinux y firewall

```bash
sudo setsebool -P httpd_can_network_connect 1          # nginx -> 127.0.0.1:50xx y :3900
sudo semanage fcontext -a -t var_lib_t '/var/lib/pef(/.*)?' && sudo restorecon -Rv /var/lib/pef
sudo firewall-cmd --permanent --add-service=https --add-service=http && sudo firewall-cmd --reload
# Google Cloud: regla de VPC solo 443/80 hacia la VM; SSH por IAP; sin IP pública para 3900-3903/5432/6379/27017.
```

SELinux sigue en *enforcing*. Si una denegación aparece (`ausearch -m avc -ts recent`), se ajusta el contexto; nunca se desactiva SELinux.

## 5. Persistencia de archivos y modelos

| Dato | Dónde | Respaldo |
| :--- | :--- | :--- |
| Base de datos | PostgreSQL | `pg_dump -Fc` diario + antes de cada actualización |
| Evidencias (fotogramas, videos) | Garage `pef-evidence` | réplica o exportación del bucket |
| Fotos de familias, avisos de privacidad, **pesos YOLO** | Garage `pef-admin` (`ADMIN_STORAGE_BACKEND=s3`) | igual que el anterior |
| Caché de pesos del worker | `/var/lib/pef/vision/model_cache` | no (se vuelve a descargar y verificar por SHA-256) |
| Videos en proceso | `/var/lib/pef/vision/uploads` | no (temporales) |
| Bandeja de salida del portal | `/var/lib/pef/web/outbox` | sí, si hay elementos pendientes |
| Checkpoints DS06 | MongoDB | `mongodump` |
| Configuración | `/etc/pef`, `/etc/nginx/conf.d/pef*`, unidades | copia cifrada antes de cada cambio |

**Copia del modelo activo (MS-11):** `VISION_WEIGHTS_PATH=/var/lib/pef/vision/weights/active.pt`. Las sesiones cargan siempre el modelo fijado desde Garage, verificado por SHA-256, y este archivo solo se usa si su SHA-256 coincide. Sin él, MonitoringService muestra VisionWorker como `degraded` aunque funcione (§R3 de `docs/MS11_PREPARACION_DESPLIEGUE.md`). Se instala con `install -o pef-vision -g pef -m 0640` y se reemplaza al activar otro modelo.

Los pesos `.pt` nunca van en el repositorio. Se suben desde Administración → Modelos (RF-AD-08), quedan en Garage con su SHA-256 y cada sesión fija el modelo con el que empezó.

### 5.1 Respaldo y verificación de la restauración [MODIFICA: solo escribe en el directorio de respaldo]

Ningún comando de esta sección detiene un servicio ni modifica datos. Las restauraciones de prueba van siempre a destinos **nuevos y desechables** (`pef_restore_check`, un Valkey temporal o un bucket vacío), nunca sobre producción.

```bash
B=/var/backups/pef/$(date +%Y%m%d-%H%M); sudo install -d -m 0700 -o root -g root $B
# PostgreSQL (consistente en caliente)
sudo -u postgres pg_dump -Fc -d pef -f /tmp/pef.dump && sudo mv /tmp/pef.dump $B/
sudo -u postgres pg_dumpall --globals-only > /tmp/globals.sql && sudo mv /tmp/globals.sql $B/   # roles, sin datos
# MongoDB (credenciales desde /etc/pef/web.env, nunca en la línea de comandos: --uri por variable)
sudo bash -c 'set -a; . /etc/pef/web.env; mongodump --uri "$MONGO_URL" --db pef --archive='$B'/mongo.archive.gz --gzip'
# Valkey: instantánea sin detener el servicio
sudo bash -c 'set -a; . /etc/pef/common.env; REDISCLI_AUTH=$(echo "$REDIS_URL" | sed -E "s#redis://:([^@]*)@.*#\1#") valkey-cli --rdb '$B'/valkey-dump.rdb'   # instantánea nueva en el archivo (probado con Valkey 8.1 de CentOS 10)
# Garage: evidencias y archivos de Administración (incluye los pesos YOLO)
for b in pef-evidence pef-admin; do
  sudo bash -c "set -a; . /etc/pef/evidence.env; /opt/pef/venvs/EvidenceService/bin/python /opt/pef/current/scripts/vm/s3_mirror.py mirror --bucket $b --dest $B/s3"
done
# Configuración (cifrada; la frase está en Secret Manager)
sudo tar -czf - /etc/pef /etc/nginx /etc/systemd/system/pef* /etc/valkey /etc/mongod.conf /etc/garage.toml 2>/dev/null \
  | gpg --symmetric --cipher-algo AES256 -o $B/config.tar.gz.gpg
sudo sha256sum $B/* > $B/SHA256SUMS
```

Verificación de restauración [INSPECCIÓN + destinos desechables]:

```bash
# PostgreSQL: listar, restaurar en una base desechable y comparar conteos con la base viva
sudo -u postgres pg_restore --list $B/pef.dump | head
sudo -u postgres createdb pef_restore_check && sudo -u postgres pg_restore -d pef_restore_check $B/pef.dump
for t in work_session count_event discrepancy "user" yolo_model surgical_tray; do
  echo "$t $(sudo -u postgres psql -XAtc "select count(*) from $t" -d pef) $(sudo -u postgres psql -XAtc "select count(*) from $t" -d pef_restore_check)"
done
sudo -u postgres dropdb pef_restore_check
# Modelos: el SHA-256 de cada peso respaldado debe coincidir con el registrado
sudo -u postgres psql -XAt -d pef -c "select checksum from yolo_model" | sort > /tmp/db_sha
sudo find $B/s3/pef-admin -name '*.pt' -exec sha256sum {} + | awk '{print $1}' | sort > /tmp/bk_sha; comm -23 /tmp/db_sha /tmp/bk_sha   # vacío = todos respaldados
# Garage: manifiesto contra el bucket y restauración a un bucket VACÍO de prueba
sudo bash -c "set -a; . /etc/pef/evidence.env; /opt/pef/venvs/EvidenceService/bin/python /opt/pef/current/scripts/vm/s3_mirror.py verify --bucket pef-evidence --dest $B/s3"
# MongoDB: restaurar con otro nombre de base y contar documentos
sudo bash -c 'set -a; . /etc/pef/web.env; mongorestore --uri "$MONGO_URL" --archive='$B'/mongo.archive.gz --gzip --nsFrom "pef.*" --nsTo "pef_restore_check.*"'
# Configuración
gpg -d $B/config.tar.gz.gpg | tar -tzf - | head
```

Después, copiar `$B` fuera de la VM (bucket GCS con retención) y probar al menos una vez la restauración completa en otra máquina.

## 6. Comprobaciones de disponibilidad [INSPECCIÓN]

```bash
systemctl list-units 'pef-*' --no-pager                      # 13 unidades active (running)
for p in 5000 5001 5003 5004 5005 5006 5007 5008 5009; do
  curl -fsS -o /dev/null -w "%{http_code} $p\n" http://127.0.0.1:$p/health/live || echo "FAIL $p"; done
curl -fsS -o /dev/null -w "%{http_code} 5002\n" http://127.0.0.1:5002/health || echo "FAIL 5002"   # VisionWorker: solo /health
curl -fsS https://api.<dominio>/healthz                       # Gateway
curl -s -o /dev/null -w '%{http_code}\n' https://api.<dominio>/api/v1/audit/events   # 404: no publicado
sudo ss -ltnp | grep -E ':(50[0-9]{2}|5432|6379|27017|390[0-3])'    # todas en 127.0.0.1
journalctl -u pef-session -n 50 --no-pager                    # sin tokens ni secretos
sudo /opt/pef/venvs/BackendWebFlask/bin/python /opt/pef/current/scripts/preflight_production.py --env-dir /etc/pef
```

Verificación funcional mínima (en una **institución de pruebas**): inicio de sesión de los roles, una sesión con video (inicial, final, validación), detalle del Supervisor con evidencias, reportes con descargas CSV/PDF, notificaciones, monitoreo y vista previa WSS. Se usa la lista de `docs/CHECKLIST_ACEPTACION.md` (grupos Infraestructura y WebSockets).

## 7. Actualización de una instalación existente [MODIFICA]

1. Inventario §1 y comparación de `git -C /opt/pef/current rev-parse HEAD` con el commit nuevo.
2. Desplegar el commit en `/opt/pef/releases/<nuevo>` sin tocar `current`. Si cambió un `requirements.txt`, crear venvs nuevos `/opt/pef/venvs/<App>-<nuevo>`.
3. Respaldo completo y verificación de la restauración (§5.1).
4. Variables nuevas: comparar `infra/vm/env/*.env.example` con `/etc/pef/*.env` (`diff <(cut -d= -f1 ...)`), añadirlas a mano y correr `preflight_production.py --strict`.
5. Migraciones nuevas: solo las posteriores a la última aplicada (todas desde 004 son idempotentes; **nunca** 001 ni 003). Para saber dónde está el esquema: `to_regclass` de las tablas de cada migración (014 `sync_change`, 015 `surgical_tray`, 016 trigger `trg_work_session_tray_same_institution`).
6. Cambiar `current` y reiniciar por grupos, en una ventana sin sesiones de conteo activas: `pef-auth` → servicios de negocio → workers → `pef-vision` → `pef-web`, con §6 después de cada grupo. Las etapas, la disponibilidad de cada una y las interrupciones están en `docs/MS11_PREPARACION_DESPLIEGUE.md` §5. Al terminar: `scripts/vm/smoke_post_deploy.py` (§7 del mismo documento).
7. Unidades o nginx: solo si `diff` muestra cambios. Después, `daemon-reload` y `nginx -t && reload`.

## 8. Reversión

1. `sudo ln -sfn /opt/pef/releases/<anterior> /opt/pef/current && sudo systemctl restart pef.target` (los venvs anteriores siguen intactos).
2. Las migraciones son aditivas: la versión anterior funciona con el esquema nuevo. Solo si una migración causara un problema se restaura el `pg_dump` de §5.1. Eso implica **pérdida de los datos posteriores**, así que la decide el responsable y nunca es automática.
3. Restaurar `/etc/pef`, nginx y unidades del respaldo si cambiaron. Después, `daemon-reload` y `nginx -t && reload`.
4. Comprobaciones §6.

## 9. Pendiente (no verificado aquí)

Ejecución real en la VM; puertos e instalaciones existentes; certificados; Secret Manager; Ultralytics/torch en CentOS 10 con el modelo definitivo; MongoDB y Garage instalados como servicios; prueba de carga; copias de seguridad automatizadas y una restauración probada.
