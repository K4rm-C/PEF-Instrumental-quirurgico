# MS-11A/B — Preparación del despliegue en Google Cloud (CentOS Stream 10, sin Docker)

Fecha: 2026-11-09. Base: commit **`6ae2aa4`** (MS-10). **No se ha accedido a la VM.** Nada de este documento se ejecutó en ella: no se desplegó, reinició, detuvo ni modificó ningún servicio. Todo lo que sí se probó se hizo en el entorno aislado o en contenedores CentOS Stream 10 desechables, y así se indica.

Procedimientos paso a paso: `GUIA_DESPLIEGUE_GCP_CENTOS10.md`. Este documento contiene el análisis, las decisiones y las listas de verificación de MS-11 y no repite esos comandos.

## 0. Herramientas preparadas (todas en `scripts/vm/`)

| Herramienta | Qué hace | Cómo se validó |
| :--- | :--- | :--- |
| `inspect_vm.sh` | Inventario de **solo lectura** de la VM (17 secciones) | Ejecutado en contenedores CentOS Stream 10 con secretos falsos plantados: 0 secretos en la salida y ningún archivo modificado. Única excepción documentada: `rpm -qa` refresca el índice de memoria compartida `rpmdb.sqlite-shm`, sin cambiar datos |
| `compare_inventory.py` | Convierte el inventario en la tabla instalado / falta / actualizar / conservar / conflicto / requiere detener | Sobre inventarios reales de contenedor (con y sin nginx y Valkey). Lo que no se inspeccionó sale como "no inspeccionado", nunca supuesto |
| `s3_mirror.py` | Respaldo de Garage (mirror con SHA-256), verificación y restauración de prueba | Contra el Garage de pruebas: 95 objetos; incremental; restauración a un bucket vacío verificada; se niega a escribir sobre un bucket con datos; detecta una copia dañada |
| `smoke_post_deploy.py` | Prueba posterior al despliegue, **solo a través de nginx** (sin base de datos ni SSH) | `smoke_local_harness.py`: pila aislada + Gateway nginx real en modo TLS (dos hosts, HTTPS, WSS) + YOLO real (§8) |

## 1. Inventario de la VM (Fase 1)

**Estado: no ejecutado.** No tengo acceso autorizado a la VM. Para que lo ejecutes tú:

```bash
# En tu equipo (copiar el script; no cambia nada en la VM salvo el archivo copiado en /tmp)
gcloud compute scp scripts/vm/inspect_vm.sh <vm>:/tmp/inspect_vm.sh --zone <zona> --tunnel-through-iap
# En la VM (solo lectura; la salida va a tu terminal, redirígela a un archivo)
sudo bash /tmp/inspect_vm.sh > pef_inventory_$(hostname -s)_$(date +%Y%m%d-%H%M).txt 2>&1
# De vuelta en tu equipo
gcloud compute scp <vm>:~/pef_inventory_*.txt . --zone <zona> --tunnel-through-iap
python scripts/vm/compare_inventory.py pef_inventory_*.txt --md comparacion_vm.md
# Reglas de firewall de VPC (solo lectura, desde tu equipo)
gcloud compute firewall-rules list --format="table(name,network,direction,sourceRanges.list(),allowed[].map().firewall_rule().list(),targetTags.list())"
gcloud compute instances describe <vm> --zone <zona> --format="yaml(tags,networkInterfaces[].accessConfigs,serviceAccounts)"
```

Cubre: sistema y recursos (CPU, RAM, disco, inodos, GPU, sincronización horaria), metadatos de GCP, paquetes (solo caché local), unidades systemd y temporizadores, puertos y procesos, nginx/Apache (`nginx -T` con secretos enmascarados), certificados (solo la parte pública; de las claves privadas solo propietario y permisos), PostgreSQL (transacción de **solo lectura**: versión, bases, roles sin contraseñas, `pg_hba`, marcadores de las migraciones 001–016, seeds, volúmenes como conteos), Valkey/Redis (configuración enmascarada + `ping` sin AUTH), MongoDB, Garage (estado, *layout*, buckets), venvs y versiones de dependencias, claves de `/etc/pef/*.env` (**sin valores**: `<set|empty|placeholder>`), pesos `.pt` (nombre y tamaño), SELinux, firewalld/nftables y conectividad local.

Antes de ejecutarlo, revisa la cabecera del script: lista exactamente qué garantiza.

## 2. Comparación contra el proyecto (Fase 2)

**Estado de la VM: desconocido hasta recibir el inventario.** `compare_inventory.py` genera la tabla en las categorías pedidas. Esto es lo que el repositorio exige y contra lo que compara:

| Componente | Requerido | Fuente en el repositorio |
| :--- | :--- | :--- |
| 9 servicios + portal | 10 procesos gunicorn en `127.0.0.1:5000–5009` + 3 workers (notificaciones, monitoreo, integración) | `infra/vm/systemd/` (13 unidades + `pef.target`) |
| nginx | Gateway TLS con dos hosts (portal, API), `/ws` con *upgrade*, redirección 80→443, front S3 opcional | `infra/gateway/`, `scripts/render_gateway_vm.py` |
| Variables de entorno | 11 archivos `/etc/pef/*.env` | `infra/vm/env/*.env.example` |
| Migraciones | 001 + seed 002 + 004–016 (003 nunca) | `data/` (§4) |
| Python | 3.12 + `requirements.txt` de cada servicio | `apps/*/requirements.txt` |
| VisionWorker | Ultralytics/torch en CPU, `/var/lib/pef/vision/*`, copia del modelo activo (R3) | `vision.env.example` |
| WebSockets | `wss://<portal>/ws`, Origin + ticket HMAC | `ws.conf.template`, `VISION_WS_*` |
| Garage | `127.0.0.1:3900`, buckets `pef-evidence` y `pef-admin` | `evidence.env`, `web.env` |
| IntegrationService | API `:5009` + worker; `INTEGRATION_ALLOWED_HOSTS` vacío | `integration.env.example` |
| API Gateway | rutas publicadas; auditoría, monitoreo y exportaciones **no** publicadas | `routes.conf.template` |

Para cada fila, la tabla generada indica: ya instalado, falta, actualizar, conservar, conflicto (puerto ocupado por otro proceso, escucha fuera de `127.0.0.1`, unidad ajena, Redis sin contraseña, datos demo en la base…) y si el cambio **requiere detener** un servicio.

## 3. Riesgos de compatibilidad (encontrados sin la VM, verificados en CentOS Stream 10)

| # | Riesgo | Evidencia | Resolución |
| :--- | :--- | :--- | :--- |
| R1 | CentOS Stream 10 **no tiene `redis`**; trae **Valkey 8.1** | `dnf list` en el contenedor | Usar `valkey`. **Probado:** suites AuthService 199, SessionService 752 e IntegrationService 47 contra Valkey 8.1, sin fallos. Las unidades se ordenan después de `redis.service` y `valkey.service` |
| R2 | Nombres de paquetes de la guía MS-10 incorrectos: no existen `python3.12` ni `postgresql16-server` | `dnf list` | Corregido: `python3` (3.12.15), `python3-devel`, `postgresql-server` (16.14), `policycoreutils-python-utils` |
| R3 | MonitoringService marca VisionWorker **`degraded`** si no existe `VISION_WEIGHTS_PATH`. Desde MS-02 ese archivo es opcional (las sesiones cargan el modelo fijado desde Garage), así que en la VM sería una **falsa alarma permanente** | Prueba de humo local: `vision: degraded` → con la copia del modelo: `up` | Sin cambiar código: copia del modelo activo en `/var/lib/pef/vision/weights/active.pt` (ya en `vision.env.example` y en la guía). Alternativa con código (MS-11C, con autorización): que la sonda considere la caché de modelos |
| R4 | nginx de CentOS 10 es **1.26.3** (las pruebas usaban 1.27.4) | `nginx -t` de la configuración renderizada en 1.26.3: válida | Sin cambios de sintaxis |
| R5 | El `nginx.conf` de CentOS ya tiene un `server_name _` en el puerto 80: el bloque de redirección del PEF (también `_`) **se ignoraba**, y el puerto 80 no redirigía a HTTPS | Advertencia `conflicting server name` en 1.26.3 | **Corregido** en `render_gateway_vm.py`: en la VM el bloque nombra sus hosts (portal, API, S3). Verificado con peticiones reales: 308 a HTTPS |
| R6 | Como `conf.d/*.conf` se carga antes del servidor por defecto de CentOS, el bloque PEF pasa a ser el predeterminado en 80/443: un `Host` desconocido o el acceso por IP también se redirige al PEF | Peticiones con `Host: other.example.org` → 308 | Si la VM ya sirve algo por IP o por el sitio por defecto, marcar ese servidor como `default_server` (**cambio de configuración existente: requiere tu autorización**) |
| R7 | Python 3.12 en la VM frente a 3.14 en este equipo (VisionWorker sí se prueba en 3.12, en su contenedor) | — | MS-11C: crear los venvs en la VM y correr las suites de los servicios antes de activarlos |
| R8 | MongoDB y Garage no están en los repositorios de CentOS | `dnf list` | MongoDB desde su repositorio oficial (verificar soporte de EL10 antes de instalar); Garage como binario firmado. Si MongoDB no estuviera disponible: solo se pierden los *checkpoints* DS06 y el monitoreo lo muestra como `unknown` (decisión) |
| R9 | `ExecStart` y permisos: unidades verificadas con `systemd-analyze verify` en CentOS 10 | Sin errores de directivas | Instalar con `install -m 0644` (la guía lo hace); nunca archivos de unidad ejecutables |
| R10 | Desconocido sin inventario: puertos ocupados, servicios Python ajenos, Apache, versión de PostgreSQL existente, datos demo (seed 003) en la base, certificados próximos a vencer | — | Lo detecta `compare_inventory.py` |
| R11 | Recursos: YOLO en CPU + 13 procesos | 12 CPU en el contenedor de prueba; la VM real es desconocida | Medir la inferencia en MS-11C con el modelo definitivo |

## 4. Respaldo, migraciones y verificación de restauración (Fase 3)

**No se ejecutó ningún respaldo en la VM.** Orden: detener la escritura **solo** si se va a migrar (§6). Un respaldo en caliente es válido para PostgreSQL (`pg_dump` es consistente) y para Garage (objetos inmutables).

| Qué | Cómo (comandos en `GUIA_DESPLIEGUE_GCP_CENTOS10.md` §5.1) | Verificación de restauración (misma sección) |
| :--- | :--- | :--- |
| PostgreSQL | `pg_dump -Fc -d pef` + `pg_dumpall --globals-only` (roles, sin datos) | `pg_restore --list` + restaurar en `pef_restore_check` y comparar `count(*)` por tabla con la base viva; borrar la base de prueba |
| MongoDB | `mongodump --db pef --archive=… --gzip` | `mongorestore --nsFrom 'pef.*' --nsTo 'pef_restore_check.*'` + conteos por colección |
| Valkey | `valkey-cli --rdb <archivo>`: instantánea nueva directa al respaldo (`BGSAVE` + copia podría tomar un `dump.rdb` viejo: `dir ./` es relativo y el guardado es asíncrono) | Solo contiene sesiones de *refresh*, revocaciones y bloqueos de inicio de sesión. Perderlo cierra todas las sesiones y **puede re-validar durante ≤ 15 min** un *access token* revocado. Respaldarlo antes de cambios; la restauración se prueba en un Valkey temporal en otro puerto |
| Garage (evidencias, avisos, fotos, **pesos YOLO**) | `s3_mirror.py mirror` por bucket (`pef-evidence`, `pef-admin`) | `s3_mirror.py verify` y `restore` a un bucket **vacío** de prueba (probado) |
| Modelos YOLO | Incluidos en `pef-admin` | `sha256sum` de cada `models/*.pt` respaldado = `yolo_model.checksum` (consulta en la guía) |
| Configuración | `tar` de `/etc/pef`, `/etc/nginx`, `/etc/systemd/system/pef*`, `/etc/valkey`, `/etc/mongod.conf`, `/etc/garage.toml`, cifrado (`gpg --symmetric`, frase en Secret Manager) | `tar -tzf` + `diff -r` contra un directorio extraído |
| Fuera de la VM | Copiar los respaldos a un bucket GCS con retención | Descarga y restauración en otra máquina (pendiente de definir) |

### Migraciones

| Base | Aplicar | Nunca |
| :--- | :--- | :--- |
| **Nueva (vacía)** | `001_init.sql` → `seeds/002_seed_catalogs.sql` → 004 … 016 en orden | `003_seed_demo.sql` (solo demos) |
| **Existente** | Solo las que falten de 004–016, en orden (todas idempotentes: repetir una ya aplicada no cambia nada). `inspect_vm.sh` informa cuáles están (`m004…m016`) | 001 (no es idempotente) ni 003 |

Las decisiones de MS-10 se mantienen: 002 obligatorio, 003 nunca en producción, 001 solo en una base vacía. Monitoring, Notifications e Integration vuelven a aplicar sus migraciones (idempotentes) al arrancar, bajo el bloqueo `pef:schema:migration`. Probado: 5 arranques simultáneos sin *deadlock*. Si el inventario encuentra usuarios `@instrumed.com` (seed 003) en la base, hay que decidir si es la base de demostración o la de producción **antes** de migrar.

## 5. Plan de actualización (Fase 4)

Principio: primero lo que **no** interrumpe; después, por capas, desde los datos hasta el borde. Cada etapa termina con `smoke_post_deploy.py` (o su parte aplicable). **Nada se ejecutó.**

| Etapa | Qué | Disponibilidad durante la etapa | ¿Interrupción? |
| :--- | :--- | :--- | :--- |
| 0 | Inventario, comparación, respaldos y verificación de restauración | Todo disponible | No |
| 1 | Nuevo release en `/opt/pef/releases/<commit>`, venvs nuevos, `/etc/pef/*.env` completos, *preflight* `--strict`, configuración nginx renderizada en un directorio temporal | Todo disponible (`current` no cambia) | No |
| 2 | Infraestructura faltante: Valkey, MongoDB, Garage (buckets), PostgreSQL. Si ya existen: solo crear base, rol, buckets o claves PEF | Servicios existentes intactos | Solo si el inventario exige cambiar `bind` o contraseña de un servicio existente (**autorización**) |
| 3 | **Ventana de mantenimiento:** detener la escritura (`pef-web` y los workers), respaldo final, migraciones | Portal en mantenimiento; lectura del API posible | **Sí, corta** (minutos) |
| 4 | `pef-auth` | El resto sigue funcionando (validan el JWT localmente) | Inicios de sesión un instante |
| 5 | `pef-catalog`, `pef-session`, `pef-evidence`, `pef-audit` | Portal en mantenimiento | Parte de la ventana |
| 6 | `pef-notifications` (+ worker), `pef-monitoring` (+ collector), `pef-integration` (+ worker) | Avisos diferidos (la bandeja de salida los conserva) | Parte de la ventana |
| 7 | `pef-vision` + copia del modelo activo (R3) | Ninguna sesión de conteo en curso (programar la ventana fuera de quirófano) | Sí |
| 8 | `pef-web` | Fin de la ventana | Fin |
| 9 | nginx: `nginx -t` y `reload` (sin cortar conexiones) | Todo | No (`reload`) |
| 10 | Prueba de humo completa; vigilar `journalctl` 30 min | Todo | No |

**Reglas:** no actualizar durante sesiones de conteo activas (el modelo queda fijado por sesión, pero un reinicio de VisionWorker corta el video en curso). Si una etapa falla, aplicar la reversión §9 sin pasar a la siguiente.

## 6. Seguridad y configuración (Fase 5) — lista de verificación

| Área | Verificación | Cómo |
| :--- | :--- | :--- |
| HTTPS | TLS 1.2/1.3, HSTS, 80 → 308 a HTTPS para los hosts PEF | prueba de humo [3]; `curl -I` |
| WSS | `wss://<portal>/ws`; Origin y ticket HMAC validados por VisionWorker; ticket falsificado rechazado | prueba de humo [6] |
| Garage TLS | Solo `127.0.0.1:3900`; acceso externo únicamente por el front TLS `s3.<dominio>`; 3901/3903 nunca públicos | inventario §11, `ss -ltn` |
| JWT | HS256 (**sin cambiar a RS256 todavía**); el mismo `JWT_SECRET_KEY` de ≥ 64 caracteres en todos los servicios; *access token* 15 min | *preflight* |
| Revocación | logout → `/me` y un microservicio responden 401 | prueba de humo [5] |
| Secretos | Solo en Secret Manager → `/etc/pef/*.env` `root:pef 0640`; nunca en el repositorio, en reportes ni en la línea de comandos; distintos entre sí | *preflight* (no imprime valores) |
| Puertos internos | 5000–5009, 5432, 6379, 27017, 3900–3903 solo en `127.0.0.1` | `compare_inventory.py` (conflicto si no) |
| Firewall GCP | Entrada solo 443/80 hacia la VM; SSH solo por IAP (`35.235.240.0/20`); sin IP pública para el resto | `gcloud compute firewall-rules list` |
| Firewall CentOS | firewalld activo con `http https` (+ `ssh`); ningún puerto 50xx abierto | inventario §14 |
| SELinux | `enforcing`; `httpd_can_network_connect=on`; contexto de `/var/lib/pef` | inventario §13; nunca desactivar |
| systemd | Usuarios sin *shell*, `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` mínimos, unidades `0644 root` | `systemd-analyze security pef-*.service` |
| Logs y auditoría | `journalctl` sin tokens ni secretos; auditoría en PostgreSQL (`access_audit`, AuditService); logs de nginx en `/var/log/nginx/pef_gateway.log` con rotación | revisión manual tras la prueba de humo |
| CSRF | Todo POST del portal exige el token (las peticiones de Administración sin token se rechazan con 403; observado en MS-11) | suites web + prueba de humo |
| Clientes externos | `client_credentials` con alcances mínimos; secreto mostrado una vez y guardado con hash; rotación y revocación en Administración | prueba de humo [13] y [15] |
| Rotación y recuperación | JWT: rotar = cambiar `JWT_SECRET_KEY` en todos los servicios y reiniciar (cierra todas las sesiones). Valkey, MongoDB, Garage y PostgreSQL: cambiar la credencial en el servicio y en `/etc/pef/*.env`, reiniciar solo los consumidores. Credenciales perdidas: restablecer desde Secret Manager; nunca leerlas de un respaldo sin cifrar | Procedimiento por escribir en MS-11C con los nombres reales de los secretos |

## 7. Pruebas posteriores al despliegue (Fase 6)

`scripts/vm/smoke_post_deploy.py` (solo a través de nginx; datos de una **institución de prueba**; sin paciente; se niega a correr sin `--confirm-test-institution`):

| # | Prueba | Cómo |
| :--- | :--- | :--- |
| 1 | Inicio de sesión de los tres roles | Login por el API + página inicial de cada rol en el portal |
| 2 | Disponibilidad de los 9 servicios | Pantalla de salud del Supervisor (MonitoringService) + `--local-ports` en la VM (`127.0.0.1:5000–5009`, incluye Monitoring e Integration) |
| 3 | nginx | `/healthz`, ruta interna no publicada (404), HSTS, 80 → HTTPS |
| 4 | Persistencia | PostgreSQL (charola escrita y leída), Garage (evidencia descargada por su URL firmada), MongoDB (`up` en el monitoreo). La persistencia tras un reinicio es un paso manual |
| 5 | Revocación | logout → `/me` y CatalogService 401 |
| 6 | WSS | Ticket del portal; *upgrade* por nginx; ticket falsificado rechazado |
| 7 | Modelo fijado | La sesión registra el modelo y su SHA-256; todos los conteos usan ese modelo |
| 8 | Captura y recaptura | Video de prueba: inicial, final distinto rechazado (409), recaptura aceptada, validación |
| 9 | Evidencias del Supervisor | ≥ 2 imágenes y descarga de una (JPEG) |
| 10 | Notificaciones | El Supervisor recibe el aviso de la sesión |
| 11 | Reportes y exportaciones | CSV y PDF desde la pantalla del Supervisor; la ruta del API **no** está publicada |
| 12 | Cámaras y estaciones | Selección de cámara + tabla de estaciones |
| 13 | Sincronización | Token de cliente máquina + `/api/v1/sync/changes` |
| 14 | Charolas | Alta y lectura |
| 15 | FHIR | `metadata` + búsqueda con el cliente de prueba. **Nunca** entregas a un servidor externo desde la prueba de humo |

**Fixture** (IDs de la institución de prueba), consulta de solo lectura en la VM:

```sql
-- psql -X -v ON_ERROR_STOP=1 -d pef  (sesión de solo lectura: SET default_transaction_read_only = on;)
SELECT json_build_object(
  'institution_id', u.institution_id, 'operator_user_id', u.id,
  'station_id', s.id, 'room_id', s.room_id,
  'camera_id', (SELECT c.id FROM capture_camera c WHERE c.station_id = s.id AND c.active ORDER BY c.name LIMIT 1),
  'kit_id', k.id, 'family_id', (SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = k.id ORDER BY ki.family_id LIMIT 1),
  'physician_id', (SELECT p.id FROM physician p WHERE p.institution_id = u.institution_id AND p.active ORDER BY p.name LIMIT 1),
  'privacy_notice_version_id', (SELECT v.id FROM privacy_notice_version v WHERE v.active AND v.effective_at <= now() ORDER BY v.effective_at LIMIT 1),
  'procedure_type_id', (SELECT t.id FROM cat_procedure_type t ORDER BY t.code LIMIT 1))
FROM "user" u
JOIN capture_station s ON s.active
JOIN operating_room r ON r.id = s.room_id AND r.active AND r.institution_id = u.institution_id
JOIN kit k ON k.active AND k.institution_id = u.institution_id AND k.name = :'test_kit'
WHERE u.email = :'test_operator_email' LIMIT 1;
```

Se añaden a mano `"videos": {"one_removed": …, "full": …}` (videos de prueba de MS-03.1, sin pacientes) y se exportan las credenciales de la institución de prueba como variables `PEF_SMOKE_*` (nunca en un archivo versionado).

**Validación local** (`smoke_local_harness.py`): resultado en §10.

## 8. Decisiones funcionales (Fase 7)

D1–D5 siguen documentadas en `docs/DECISIONES_PENDIENTES.md`, sin implementar. Lo que cambia es **para qué despliegue** se necesitan:

| Decisión | Demostración académica | Despliegue hospitalario real |
| :--- | :--- | :--- |
| D1 Cierre tras contingencia | No bloquea: el comportamiento actual se distingue por `close_basis` y `approved_contingency` | Requiere la decisión de Calidad (¿"seguro" o no?) |
| D2 Resolución de un escalamiento | No bloquea: cierre no seguro siempre | Requiere la decisión de CEyE (devolver al Operador o no) |
| D3 P6 avisos por institución | No bloquea si hay **una** institución | Bloquea con varias instituciones; requiere aprobación de Privacidad |
| D4 Ciclo de la charola | No bloquea (charola opcional) | Requiere decisión si la charola se usa para trazabilidad de esterilización |
| D5 FHIR | Solo con HAPI de pruebas o sin entregas (`INTEGRATION_ALLOWED_HOSTS` vacío) | Requiere mapeo aprobado, convenio, base legal y autenticación saliente |

Además, para un hospital real hacen falta: modelo YOLO definitivo validado, cámaras físicas probadas, aceptación manual (lista de MS-10), aviso de privacidad aprobado, retención de datos definida, copias fuera de la VM con restauración probada, monitoreo externo y un responsable de guardia. Para la demostración académica basta con la institución y los usuarios de prueba, el modelo provisional y los videos de prueba.

## 9. Comandos de despliegue (no ejecutados) y reversión

Separados en `GUIA_DESPLIEGUE_GCP_CENTOS10.md`: **[INSPECCIÓN]** §1 y §6; **[MODIFICA]** §4 (instalación) y §7 (actualización). Corregidos en MS-11: nombres de paquetes (R1, R2), copia del modelo activo (R3), unidades con `install -m 0644` (R9) y directorio `weights`. La redirección 80→443 (R5) se corrigió en `render_gateway_vm.py`.

Reversión (guía §8): volver el enlace `current` al release anterior y reiniciar `pef.target` (los venvs anteriores siguen intactos). Las migraciones son aditivas: la versión anterior funciona con el esquema nuevo. La restauración de `pg_dump` solo la decide el responsable, porque **pierde los datos posteriores**. Para nginx: restaurar `conf.d/pef*` del respaldo, `nginx -t` y `reload`. Para la infraestructura nueva (Valkey, MongoDB, Garage): detener y deshabilitar solo lo que instaló el PEF; los servicios preexistentes nunca se tocan.

## 10. Resultado de la validación local de la prueba de humo

`smoke_local_harness.py` (pila aislada + Gateway nginx real con la plantilla TLS de producción: hosts `portal.pef.test` y `api.pef.test`, HTTPS con certificado temporal, WSS, YOLO real con el modelo provisional): **31 PASS · 0 FAIL · 3 SKIP**.

| SKIP | Motivo |
| :--- | :--- |
| Garage en MonitoringService | La pila de pruebas no publica la API de administración de Garage (3903). En la VM sí se monitorea |
| Puertos locales 5000–5009 | Solo aplica en la VM (`--local-ports`) |
| Entregas FHIR | Nunca desde la prueba de humo (solo contra un servidor de pruebas autorizado, a mano) |

Defectos encontrados al validar la prueba de humo, todos en el propio script y corregidos: campo de cámara (JSON), exportaciones (son descarga del portal, el API no las publica, y eso se comprueba), modelo fijado (`?expand=detail`) y una variable reutilizada que rompía `--resolve`. Lo que reveló del sistema: VisionWorker `degraded` sin `VISION_WEIGHTS_PATH` (R3) y que la preparación de Administración por HTTP plano se rechaza por CSRF con cookies `Secure`, que es el comportamiento correcto.

## 11. Bloqueadores (antes de MS-11C)

1. **Inventario de la VM** (lo ejecutas tú, §1) y su comparación.
2. **Autorización** para cada cambio en la VM, en particular los que afectan servicios existentes (R6, `bind` o contraseñas de servicios ya instalados).
3. Dominios y certificados (portal, API y, opcionalmente, S3) y su registro DNS.
4. Secretos creados en Secret Manager (JWT, sesión del portal, WS, PostgreSQL, Valkey, MongoDB, Garage).
5. Institución y usuarios de prueba en la base de la VM para la prueba de humo.
6. Disponibilidad de MongoDB para EL10 (R8).
7. Decisión sobre la ventana de mantenimiento (§5, etapas 3–8).
