# PEF — Seguridad de infraestructura (MS-09)

Estado: **preparado y verificado en el entorno aislado; pendiente de validación en el despliegue real.** No se usaron certificados ni secretos reales; nada de lo siguiente declara la producción validada.

## 1. Garage (S3) por HTTPS

| Puerto Garage | Uso | Exposición |
| :--- | :--- | :--- |
| 3900 S3 API | Portal, EvidenceService, VisionWorker (pesos) | Solo `127.0.0.1`; hacia afuera solo por el *front* TLS si hace falta |
| 3901 RPC | Clúster Garage | Nunca publicado |
| 3903 Admin | Administración / salud | Nunca publicado |

- Front TLS: `infra/vm/nginx/garage-s3.conf.template` (renderizado por `scripts/render_gateway_vm.py --garage-host`). Pasa `Host` sin cambios (las URL prefirmadas SigV4 se firman para ese host), TLS 1.2/1.3, HSTS.
- Solo necesario si navegadores o clientes deben abrir URL prefirmadas (`S3_PUBLIC_ENDPOINT=https://s3.<dominio>`); los servicios usan `S3_INTERNAL_ENDPOINT=http://127.0.0.1:3900` (tráfico local, sin salir de la VM).
- Verificado: sintaxis con `nginx -t` (certificado desechable, solo para la prueba de sintaxis). **Pendiente:** certificado real y prueba de descarga de una evidencia por HTTPS en la VM.

## 2. nginx / API Gateway en producción

- Las mismas plantillas del Gateway (MS-04: lista de rutas, límites, TLS, WebSocket, CSRF del portal) se renderizan para la VM con `scripts/render_gateway_vm.py` (upstreams `127.0.0.1`, puertos 80/443, rutas de certificado del operador, logs en `/var/log/nginx/pef_gateway.log`). Rechaza cualquier variable sin valor.
- Publicado: solo lo de `GUIA_API_GATEWAY.md` §2. No publicado: Audit, Monitoring (salvo latido), `/api/v1/integration/*`, `/health/*`, Swagger, rutas internas de VisionWorker y Garage admin.
- Pruebas: `scripts/e2e_gateway.py` 32/32 (TLS con certificado desechable, HSTS, redirección, autorización, revocación, límites) y contratos 0/0.

## 3. Secretos y credenciales

- Nunca en el repositorio: `.env`, `.env.test`, certificados, pesos (`.gitignore` + revisión antes de cada commit). En la VM: `/etc/pef/*.env` `root:pef 0640`, valores tomados de un gestor de secretos (Secret Manager de Google Cloud recomendado).
- `scripts/preflight_production.py --env-dir /etc/pef`: valida longitudes, marcadores de ejemplo, igualdad del secreto JWT entre servicios, secretos distintos entre sí, cookies `Secure`, modo demo apagado, URL internas en `127.0.0.1` y públicas en TLS, permisos de archivos. **Nunca imprime un valor** (solo longitud y huella SHA-256 de 8 caracteres).
- Secretos de clientes máquina: generados por Administración, mostrados una vez, guardados con hash; rotación y revocación (MS-07).
- MS-10, dos defectos de preparación corregidos:
  1. Los once `infra/vm/env/*.env.example` no habían entrado al repositorio porque las reglas `env/` y `ENV/` de `.gitignore` ignoraban la carpeta. Ahora se versionan solo los `*.env.example` (marcadores, sin valores reales) y cualquier otro archivo de esa carpeta sigue ignorado.
  2. `web.env.example` activaba `ADMIN_STORAGE_BACKEND=s3` sin credenciales ni bucket: las fotos de familias, los avisos de privacidad y los pesos de modelos habrían fallado al subir. Se añadieron `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` y `ADMIN_S3_BUCKET`, y el *preflight* lo comprueba.

## 4. JWT y rotación de claves (propuesta; no implementada)

Hoy: HS256 con un secreto compartido por los 9 servicios + revocación en Redis. **No se cambia a RS256 de forma unilateral.** Migración segura propuesta, en tres pasos con pruebas en cada uno:

1. **Rotación HS sin corte:** `JWT_SECRET_KEY` (firma) + `JWT_PREVIOUS_SECRET_KEYS` (solo verificación, ventana = vida máxima del refresh). Cabecera `kid` en los tokens nuevos. Pruebas: token firmado con la clave anterior aceptado dentro de la ventana y rechazado después; `alg=none` y otro algoritmo rechazados (ya probado en MS-04).
2. **Asimétrica en paralelo:** AuthService firma RS256/ES256 con `kid`; publica `/.well-known/jwks.json`; los servicios aceptan **ambos** (HS y RS por `kid`) durante una ventana; cachés de JWKS con caducidad.
3. **Retiro de HS:** cuando todos los tokens vivos sean RS (≥ vida del refresh), los servicios dejan de aceptar HS y el secreto compartido se elimina de los servicios que no firman.

Beneficio: ningún servicio podría emitir tokens (hoy cualquiera con el secreto podría). Riesgo: cambiar los 9 validadores; por eso requiere autorización y una fase de pruebas propia.

## 5. Endpoints internos

Todos los servicios escuchan en `127.0.0.1` (unidades systemd `--bind 127.0.0.1:<puerto>`); el único punto público es nginx (443, y 80 solo para redirigir). Cada servicio valida además el JWT, el rol, la institución y la revocación por sí mismo (defensa en profundidad: el Gateway no autentica).

## 6. Logs y auditoría

- journald por servicio (`journalctl -u pef-<servicio>`), `SyslogIdentifier` propio; nginx en `/var/log/nginx/`. Los servicios no registran tokens, secretos, cuerpos ni datos clínicos; las consultas se registran sin token (Bearer solo en cabecera).
- `X-Correlation-ID` de punta a punta (Gateway → servicio → auditoría).
- `access_audit` (PostgreSQL) para acciones de negocio, con actor usuario o cliente máquina; lectura por AuditService (Administración y Supervisor).
- Retención: journald `SystemMaxUse` y logrotate de nginx según política del hospital (pendiente de definir).

## 7. Control de peticiones y límites

Gateway: `limit_req` para API (20 r/s, ráfaga 40), autenticación y `/token` (10 r/min), portal (50 r/s); conexiones por IP (50); tamaños de cuerpo (1 MB API, 10 MB evidencias, 600 MB video del portal); timeouts. AuthService: bloqueo por intentos fallidos de login y de *client credentials* (usuario/cliente e IP). IntegrationService: páginas de sincronización ≤ 500.

## 8. Revocación

| Qué | Cómo | Efecto |
| :--- | :--- | :--- |
| Usuario desactivado / contraseña restablecida | Administración → AuthService revoca sus tokens en Redis; los servicios además comprueban `user.active` en cada petición | Inmediato |
| Cierre de sesión | `/logout` revoca acceso y refresh; MS-03: también cierra el WebSocket en vivo | Inmediato |
| Cliente máquina desactivado | IntegrationService comprueba `active` en cada petición + revocación de sus tokens | Inmediato (probado por el Gateway) |
| Secreto de cliente rotado | Revocación de todos sus tokens; el secreto anterior deja de funcionar | Inmediato (probado) |
| Alcance retirado a un cliente | Alcances leídos de PostgreSQL en cada petición | Inmediato |

## 9. Pendiente de validación en el despliegue real

Certificados reales y renovación; Secret Manager; `firewalld` y reglas de VPC de Google Cloud; SELinux en *enforcing* con las políticas de §6 de `GUIA_DESPLIEGUE_GCP_CENTOS10.md`; TLS de Garage con descargas reales; prueba de carga de los límites; política de retención de logs.
