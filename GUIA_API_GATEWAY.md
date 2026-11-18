# API Gateway (MS-04)

Actualización: 8 de noviembre de 2026.

El API Gateway es la única entrada para clientes externos (Android con JSON, escritorio con XML) y, en producción, también para el navegador. Usa nginx `1.27.4-alpine`, con la configuración del repositorio en `infra/gateway/`. No reemplaza ningún servicio y **no autentica por su cuenta**: cada microservicio sigue validando el JWT (firma, algoritmo, caducidad, revocación en Redis), el rol y la institución, igual que si se le llamara directamente.

## 1. Topología

```text
Desarrollo (docker compose --profile gateway up -d gateway)
  Cliente ──HTTP──> 127.0.0.1:8080  gateway ──> auth, catalog, session, evidence, notifications, vision (/ws)
  Navegador ──HTTP──> 127.0.0.1:5000 portal   (directo, como hasta ahora; login contra 127.0.0.1:5001)

Producción (docker-compose.yml + docker-compose.gateway-tls.yml)
  Android / escritorio ──HTTPS──> api.<dominio>:443     ─┐
  Navegador          ──HTTPS/WSS──> <portal>:443         ─┤ gateway (TLS 1.2/1.3, HSTS)
                       HTTP :80 → 308 a HTTPS            ─┘
       api.<dominio>  → /api/v1/... (lista explícita) y /ws
       <portal>       → portal (web:5000), /auth/{login,logout,refresh,verify,preferences,password/change}
                        → AuthService, /ws → VisionWorker
  auth y web dejan de publicar puertos; el resto ya solo escuchaba en 127.0.0.1.
```

## 2. Rutas publicadas (servidor de API)

| Ruta | Métodos | Servicio | Notas |
|---|---|---|---|
| `/healthz` | GET | el propio gateway | Sin detalle de los servicios |
| `/api/v1/auth/login`, `/refresh`, `/logout`, `/password/change` | POST | AuthService | Tokens en el cuerpo; límite de peticiones estricto |
| `/api/v1/auth/me`, `/permissions`, `openapi.yaml`, `schemas/*.xsd` | GET | AuthService | |
| `/api/v1/catalog/{categories,cycle-statuses,families,instruments,kits}[/{id}]` | GET, POST, PUT | CatalogService | Las escrituras siguen limitadas por rol en el servicio |
| `/api/v1/catalog/openapi.yaml`, `schemas/*.xsd` | GET | CatalogService | |
| `/api/v1/sessions/{id}/evidence` | GET | EvidenceService | Antes que la regla de sesiones |
| `/api/v1/evidence[/{id}]` | GET, POST | EvidenceService | Cuerpo de hasta `GATEWAY_EVIDENCE_MAX_BODY` |
| `/api/v1/sessions[/...]` | GET, POST, PUT | SessionService | Excepto `/docs` |
| `/api/v1/notifications[/...]` | GET, PATCH | NotificationsService | Excepto `/docs` |
| `/api/v1/auth/token` | POST | AuthService | **MS-07.** Client credentials de clientes máquina (DS08); mismo límite estricto que el login |
| `/api/v1/catalog/trays[/{id}]` | GET, POST, PUT | CatalogService | **MS-08.** Charolas (escribe solo el SPD; lo decide el servicio) |
| `/api/v1/sync/{resources,changes,openapi.yaml,schemas/integration.xsd}` | GET | IntegrationService | **MS-07.** Registro de cambios para escritorio, Android y clientes máquina |
| `/api/v1/sync/operations` | POST | IntegrationService | **MS-07.** Operaciones idempotentes (solo usuarios), reenviadas al servicio dueño |
| `/fhir/r4/metadata`, `/fhir/r4/{Location,Practitioner,Encounter}`, `/fhir/r4/{Organization,Location,Practitioner,Patient,Encounter}/{id}`, `/fhir/r4/Encounter/{id}/$pef-bundle` | GET | IntegrationService | **MS-07.** FHIR R4 de solo lectura para clientes máquina (alcances `fhir.*`); lista explícita: cualquier otro recurso FHIR es 404 del gateway |
| `/api/v1/monitoring/stations/{id}/heartbeat` | POST | MonitoringService | **MS-05.** Única ruta de Monitoring publicada: el latido de un cliente de estación de escritorio (rol Operador, institución validada en el servicio). El portal no la necesita (reenvía el latido del navegador por la red interna) |
| `/ws` | GET (Upgrade) | VisionWorker | El worker comprueba `Origin` y el ticket HMAC |

**No publicadas** (respuesta 404 JSON del propio gateway):
- Administración de identidad: `/api/v1/auth/users`, `roles` y `sessions/revoke`. Se gestiona desde el portal.
- AuditService y el resto de MonitoringService (`/services`, `/history`, `/summary`, `/stations`, `/alerts`): son consolas de Administración y del Supervisor dentro del portal.
- **MS-07:** `/api/v1/integration/*` (destinos FHIR y entregas): consola del Administrador, no la necesita ningún cliente externo.
- **MS-06:** `/api/v1/sessions/reports/export` (`location =` exacta → 404). El CSV/PDF lo descarga el navegador a través del portal; ningún cliente externo lo requiere. Los demás reportes (`/reports/summary`, `/stations`…) siguen cubiertos por la regla de sesiones porque el cliente de escritorio del Supervisor ya los consume.
- Todo `/health/*` de los servicios y la interfaz Swagger (`/docs`).
- Las rutas HTTP de VisionWorker (`bind`, `video`, `state`, `/auth/revoke`).
- Las rutas de diagnóstico y de pruebas de EvidenceService (`/api/v1/whoami`, `/__test__`).
- Las rutas por cookie de AuthService (`/login`, `/verify`…) en el host de la API.
- El portal en el host de la API.

Un método no permitido en una ruta publicada responde 403 (`limit_except`); `TRACE` responde 405.

## 3. Controles

- **Identidad:** nunca se reenvía `Cookie` ni `X-PEF-Signature` del cliente, y se oculta `Set-Cookie` de las respuestas de la API (solo Bearer). `X-Forwarded-For` se **sustituye** por la dirección real del cliente. AuthService confía solo en `AUTH_TRUSTED_PROXIES` saltos.
- **Trazabilidad:** si llega un `X-Correlation-ID` válido (8–128 caracteres `[A-Za-z0-9._:-]`) se conserva; si no, el gateway genera uno. Se devuelve una sola vez en la respuesta.
- **Abuso:**
  - límite de peticiones por dirección: API 20 r/s (ráfaga 40), login 10 r/min (ráfaga 5), portal 50 r/s (ráfaga 200);
  - hasta 50 conexiones por dirección;
  - cuerpo máximo de 1 MB, o 10 MB en evidencias;
  - tiempos de espera: 5 s para conectar y 30 s de lectura (300 s en `/ws`).

  Los errores del gateway (413, 429, 502) son JSON y no revelan direcciones internas.
- **Cabeceras:**
  - `Server: nginx` sin versión;
  - se ocultan `Server` y `X-Powered-By` del servicio;
  - `X-Content-Type-Options: nosniff`;
  - HSTS en el listener TLS.
- **Registro:** método, ruta **sin** parámetros de consulta, estado, tiempo y correlación. Nunca cabeceras, tokens ni cuerpos.
- **Sin pool `keepalive` hacia los servicios:** una conexión cerrada por el servicio no se convierte en un 502 para un POST que no se puede reintentar.

## 4. Configuración

Archivos:
- `infra/gateway/templates/00-pef-http.conf.template`: upstreams, mapas y zonas de límite.
- `templates/pef/proxy.conf.template`, `ws.conf.template` y `routes.conf.template`: reglas comunes y lista de rutas.
- `servers/http.conf.template`: desarrollo.
- `servers/tls.conf.template`: producción.

La imagen oficial de nginx sustituye solo las variables `${PEF_GW_*}` al arrancar. Compose las alimenta desde `GATEWAY_*` (ver `.env.example`).

| Variable (`.env`) | Por defecto | Uso |
|---|---|---|
| `GATEWAY_HTTP_PORT` | 8080 | Desarrollo, solo en 127.0.0.1 |
| `GATEWAY_RATE_API` / `GATEWAY_BURST_API` | 20r/s / 40 | API |
| `GATEWAY_RATE_AUTH` / `GATEWAY_BURST_AUTH` | 10r/m / 5 | Login, refresh, logout y cambio de contraseña |
| `GATEWAY_RATE_PORTAL` / `GATEWAY_BURST_PORTAL` | 50r/s / 200 | Portal (producción) |
| `GATEWAY_MAX_CONN_PER_IP` | 50 | Conexiones simultáneas |
| `GATEWAY_MAX_BODY` / `GATEWAY_EVIDENCE_MAX_BODY` | 1m / 10m | Tamaño de cuerpo |
| `GATEWAY_UPSTREAM_TIMEOUT` / `GATEWAY_WS_TIMEOUT` | 30s / 300s | Tiempos de espera |
| `GATEWAY_TLS_CERT_DIR` | — (obligatoria en TLS) | `fullchain.pem` y `privkey.pem` del operador, montados solo lectura |
| `GATEWAY_API_HOST` / `GATEWAY_PORTAL_HOST` | — (obligatorias en TLS) | Nombres públicos |
| `GATEWAY_PORTAL_ORIGIN` / `GATEWAY_PORTAL_WS_URL` | — (obligatorias en TLS) | `https://…` y `wss://…/ws` |

## 5. Desarrollo frente a producción

| Aspecto | Desarrollo | Producción |
|---|---|---|
| Arranque | `docker compose --profile gateway up -d gateway` | `docker compose -f docker-compose.yml -f docker-compose.gateway-tls.yml --profile gateway up -d` |
| Transporte | HTTP en 127.0.0.1:8080 | TLS 1.2/1.3 en 443; el 80 solo redirige (308) |
| Certificado | No aplica | El del operador; el repositorio no genera ni incluye ninguno |
| Portal y login | Directos (5000 y 5001), como antes | Detrás del gateway en un solo origen HTTPS (`AUTH_BROWSER_URL=<origen>/auth`) |
| Puertos de `auth` y `web` | Publicados | Cerrados (`!reset`); solo el gateway es público |
| `AUTH_TRUSTED_PROXIES` | 0 (AuthService también es accesible directamente) | 1 |
| Cookies | `AUTH_COOKIE_SECURE=false`, `PORTAL_COOKIE_SECURE=false` | `true` y `true` (cookie CSRF `__Host-`) |
| WebSocket | `ws://127.0.0.1:5002/ws` (o `/ws` del gateway) | `wss://<portal>/ws` |

En desarrollo, con el perfil activo, todos los clientes que entran por el gateway comparten su dirección ante el bloqueo de login de AuthService (`AUTH_TRUSTED_PROXIES=0`). Así se evita aceptar `X-Forwarded-For` desde el puerto 5001, que es público.

## 6. Pruebas

- **E2E real:** `scripts/e2e_gateway.py` (ver `GUIA_PRUEBAS.md` §2.5). Prueba el modo de desarrollo y el de producción con un certificado desechable creado en un directorio temporal.
- **Validación de sintaxis** (`nginx -t`): la arranca el propio contenedor. Comando equivalente (los upstreams deben resolver):

  ```bash
  docker run --rm -e PEF_GW_...=... -v infra/gateway/...:/etc/nginx/templates/... nginx:1.27.4-alpine nginx -t
  ```
- **Configuración de producción:** `docker compose -f docker-compose.yml -f docker-compose.gateway-tls.yml --profile gateway config` debe mostrar puertos no locales solo en `gateway` (443 y 80) y `garage` (3900).

## 7. Pendientes

- **Garage (S3):** sigue publicado sin TLS para las URLs prefirmadas de evidencias. Debería ir detrás de un nombre HTTPS propio.
- **Límites distribuidos:** el límite de peticiones es por instancia de nginx. Con varias réplicas haría falta uno compartido.
- **WAF y mTLS:** no hay WAF ni mTLS para clientes máquina (DS08, `integration_client`). Se abordan en MS-07.
- **Rutas no publicadas:** AuditService y MonitoringService no están publicados. Si un cliente de escritorio del Supervisor los necesita, se añaden a la lista con la misma regla de solo lectura.
