# PEF Monitor (Electron) — semáforo de instrumental quirúrgico

**Integración con PEF-Instrumental-quirurgico:** este cliente usa AuthService para iniciar sesión,
SessionService para consultar la estación y emitir tickets de solo lectura, y VisionWorker para observar
la inferencia por WebSocket. Configure `VISION_MONITOR_SECRET` con el mismo valor en SessionService y
VisionWorker. En la pantalla de configuración indique la URL del gateway (o las URLs directas de AuthService
y SessionService), el UUID de la estación y la URL de VisionWorker `/ws` si no se accede mediante el gateway.
La app permanece sin conectar hasta que esos servicios estén disponibles y se guarde esa configuración.
Debajo del video y el semáforo muestra el nombre del paciente, el procedimiento y el médico de la
sesión asignada. El idioma se obtiene de `ui_preferences.locale` de AuthService (`es-MX` o `en`)
al iniciar sesión y se vuelve a consultar periódicamente; los nombres propios permanecen como están
registrados. Los procedimientos del catálogo inicial se muestran traducidos.
Para reproducir un video procesado, VisionWorker necesita `ffmpeg` y `ffprobe`; la imagen de Docker
los instala. Si VisionWorker se ejecuta directamente en Windows, instálelos en el `PATH` del host.

Cliente de escritorio para Windows que muestra, en la estación de conteo, la sesión que el Operador está
llevando en el portal web: cámara procesada por VisionWorker, detecciones, conteo contra la referencia vigente
y un semáforo legible a distancia.

**Es exclusivamente un monitor visual.** No crea, inicia, valida, aprueba, rechaza ni cierra sesiones; no
registra conteos; no abre la cámara; no carga `best.pt` ni ejecuta YOLO. Todas las decisiones oficiales siguen
en el sistema web (Operador y Supervisor). Es el tercer cliente del PEF junto al portal web y la app Android.

---

## 1. Arquitectura

```text
                 ┌──────────── Estación de conteo (Windows) ────────────┐
 Navegador del   │  Portal web (Operador)            PEF Monitor        │
 Operador ───────┤   cámara → /ws (frame.submit)     ├─ main (Node)     │
                 │                                   │   PefApi  ─────────HTTPS Bearer──▶ API Gateway ─▶ AuthService (/api/v1/auth)
                 │                                   │   StationMonitor ──HTTPS Bearer──▶ API Gateway ─▶ SessionService (/stations/{id}/monitor…)
                 │                                   │   VisionObserver ──WSS ticket m1──▶ VisionWorker /ws (monitor.subscribe, solo lectura)
                 │                                   ├─ preload (sandbox, 10 funciones)  │
                 │                                   └─ renderer React (sin red, sin Node)│
                 └──────────────────────────────────────────────────────┘
```

| Proceso | Responsabilidad | Archivos |
|---|---|---|
| Principal | Configuración, credenciales cifradas, HTTP/WS, orquestación, bandeja, instancia única, IPC validado | `src/main/*.ts` |
| Preload | Puente mínimo `window.pef` (10 funciones), sin `ipcRenderer` expuesto | `src/preload/preload.ts` |
| Renderer | Vista pura (React). CSP `connect-src 'none'`: no puede hablar con la red | `src/renderer/**` |
| Compartido | Lógica pura: fases, semáforo, estabilización, contratos IPC | `src/shared/*.ts` |

Fuentes de verdad (nunca una variable local de Electron):

* **Estado oficial, fase, referencia vigente y resultado oficial** → SessionService (PostgreSQL).
* **Conteo en vivo** → VisionWorker (la misma inferencia que produce la captura del Operador).

## 2. Hallazgos de la inspección (Fase 0)

| Tema | Realidad del repositorio (rama `main`) | Consecuencia para Electron |
|---|---|---|
| Estados de sesión | `scheduled`, `in_progress`, `or_hold`, `awaiting_spd_review`, `correction_required`, `closed`, `aborted` (`data/seeds/002_seed_catalogs.sql`) | Se usan tal cual; no se inventan estados |
| Fases | `start`, `demo_phase_1..3`, `final_count` (`cat_operation_phase`) | `start` = conteo inicial; `final_count` = verificación final; resto = procedimiento |
| Inicio de sesión | Portal `POST /operator/sessions/{id}/begin` → `rf_session.start_session` (PostgreSQL) y *bind* a VisionWorker | Electron lo detecta leyendo SessionService |
| Cambio de fase | Portal `POST /operator/sessions/{id}/capture/phase` | Se refleja en el siguiente sondeo (≤ 2 s) |
| Validación | Portal `vision-validation` → `manual_close` → `awaiting_spd_review` o `correction_required` | Verde confirmado / rojo oficial |
| Cierre | Supervisor (portal o SessionService) → `closed` | Sesión terminada → espera |
| Captura física | **El navegador del Operador** abre la cámara (`capture-live-ws.js`) y envía JPEG a VisionWorker `/ws`; o sube un video | Electron **no abre la cámara**: muestra lo que VisionWorker ya procesó (una sola fuente, sin conflicto de propiedad) |
| WebSocket existente | VisionWorker `/ws` con ticket HMAC del portal; las detecciones se envían **solo** al cliente que envía fotogramas; un segundo `session.bind` reiniciaría la ejecución en vivo | Se añadió un modo **observador** de solo lectura (`monitor.subscribe`) |
| Eventos de sesión | No hay bus/WS de cambios de sesión o fase | Sincronización por sondeo autenticado a SessionService (el mecanismo existente) |
| Estación ↔ sesión | `work_session.station_id` (fijada al programar); `capture_station` → `operating_room.institution_id` | Asociación por estación + institución + Operador asignado |
| Cámaras | `capture_camera` (incl. `simulated`), `station_status` (MS-05) | Se informan en la pantalla de espera sin abrir dispositivos |
| Gateway | Publica `/api/v1/auth/*`, `/api/v1/sessions[/...]` (GET/POST/PUT) y `/ws` | Los endpoints nuevos quedan cubiertos sin tocar nginx |
| Bloqueo del final incorrecto (MS-03.1) | Descrito en las guías, **no presente** en el portal de `main`: un faltante se registra como discrepancia al validar | El rojo es solo visual; documentado como limitación |

## 3. Cambios mínimos en el backend

| Componente | Cambio | Archivos |
|---|---|---|
| SessionService | 3 endpoints de solo lectura para el monitor (rol `station_operator`) | `services/station_monitor.py`, `routes/sessions.py`, `config.py`, `main.py`, `openapi.yaml`, `tests/test_electron_station_monitor.py` |
| VisionWorker | Modo observador `monitor.subscribe` en `/ws` + ticket `m1` + `last_frame_at` | `live_monitor.py`, `monitor_ticket.py`, `live_ws.py` (+7 líneas), `inference.py` (+4), `main.py` (+3), `tests/test_electron_monitor.py` |
| Compose / `.env.example` | `VISION_MONITOR_SECRET`, `VISION_MONITOR_TICKET_TTL`, `SESSION_MONITOR_WS_URL` | `docker-compose.integral.yml`, `.env.example` |

No hay microservicio nuevo, migraciones, tablas, cambios de gateway, del portal, de Android, de reglas de conteo,
del modelo ni de Legacy.

### 3.1 Contratos nuevos (SessionService, publicados por el gateway)

Todos exigen `Authorization: Bearer` (JWT de AuthService, sesión Redis no revocada, usuario e institución
activos), rol `station_operator` (otro rol → 403 auditado como `DENIED_*`), y que la estación pertenezca a la
institución del usuario (si no, 404). Una estación deshabilitada → 409 `station_disabled`. Solo se consideran
sesiones **asignadas al usuario** (regla existente "Operador: sus propias sesiones"). La vista de la
estación muestra el nombre del paciente, el procedimiento y el médico de la operación asignada.
No escriben nada.

| Método y ruta | Respuesta |
|---|---|
| `GET /api/v1/sessions/stations/{station_id}/monitor` | `{server_time, station, camera_registry, state: active\|idle\|ambiguous, session, candidates, live_monitor_enabled}` |
| `GET /api/v1/sessions/stations/{station_id}/monitor/{session_id}` | `{server_time, session}` (cualquier estado: sirve para mostrar revisión/cierre) |
| `POST /api/v1/sessions/stations/{station_id}/monitor/{session_id}/live-ticket` | `{session_id, ticket, ws_url, expires_at, ttl_s, scope: "observe"}` · 409 `live_not_available` fuera de `in_progress`/visión · 503 `monitor_unavailable` sin secreto |

`session` incluye `status_code`, `phase_code`, `capture_mode`, `camera` (si la sesión registró una),
`reference` (`total`, `version` = hash de `expected_inventory`, `families`) y `official.result`:
`pending | validated_match | discrepancy | closed_clean | closed_with_discrepancies | aborted` (espejo de
PostgreSQL; ninguna regla nueva). `state = ambiguous` cuando hay más de una sesión en curso: **no se elige ninguna**.

### 3.2 Protocolo observador de VisionWorker (`/ws`)

```jsonc
→ {"type":"monitor.subscribe","session_id":"<uuid>","auth_token":"m1.<sid>.<exp>.<nonce>.<hmac>"}
← {"type":"monitor.subscribed","session_id":"…","expires_at":…}
← {"type":"monitor.state","session_id":"…","worker":{status,capture_source,run_id,frames_processed,last_frame_at,frame_age_s},
    "frame":{"jpeg_b64":"…","frames_processed":n}|null,"counts_by_family":[…],"detections":[{family_code,confidence,bbox}]}
→ {"type":"monitor.renew","auth_token":"m1…"}      ← {"type":"monitor.renewed"}
← {"type":"error","code":"unauthorized|session_expired|busy|monitor_disabled|forbidden"}
```

* Ticket firmado con `VISION_MONITOR_SECRET` (distinto de `VISION_WS_SECRET`) y dominio MAC propio: un ticket de
  monitor **no** puede vincular, enviar fotogramas ni cancelar; un ticket del portal **no** sirve como monitor.
* Fotograma solo cuando hay una inferencia nueva, como máximo cada 0.4 s; latido sin imagen cada 2 s.
* Caducidad comprobada en cada ciclo; máximo 3 observadores por sesión.
* Nunca toca la ejecución del Operador (no `prepare_live_run`, no `cancel`), ni conteos, ni evidencias.

## 4. Requisitos

* Windows 10/11 x64 (desarrollo también probado con Node 24).
* Node.js ≥ 20 y npm (solo para compilar).
* Pila PEF accesible: AuthService, SessionService (con `VISION_MONITOR_SECRET`), VisionWorker (con el mismo
  secreto) y, recomendado, el API Gateway.
* Cuenta con rol `station_operator`.

Dependencias (todas de desarrollo; el instalador solo empaqueta el código compilado): `electron 44.10.1`,
`react`/`react-dom 19.1`, `vite 7.1` + `@vitejs/plugin-react`, `typescript 5.9`, `vitest 3.2`,
`electron-builder 26.0`, `ws` (servidor de pruebas), `playwright-core` (E2E de la UI). Sin dependencias de
ejecución adicionales: HTTP con `fetch` y WebSocket nativos de Node 24 dentro de Electron.

## 5. Instalación y desarrollo

```powershell
cd apps\PEF-Electron
npm install
node node_modules\electron\install.js   # si npm 11 bloqueó el script que descarga el binario de Electron
npm run build          # iconos + main/preload (tsc) + renderer (vite)
npm start              # abre la app (o: npm run dev  → con DevTools)
npm run demo           # modo demostración: fuente SIMULADA, sin backend
```

> En terminales de VS Code existe `ELECTRON_RUN_AS_NODE=1`, que hace que Electron arranque como Node.
> Elimínela (`Remove-Item Env:ELECTRON_RUN_AS_NODE`) antes de `npm start`.

## 6. Configuración y asociación con la estación

Primer arranque → pantalla **Configuración de la estación**:

| Campo | Ejemplo | Notas |
|---|---|---|
| URL del API Gateway | `https://api.hospital.example` · desarrollo: `http://127.0.0.1:8080` | Fuera de este equipo solo `https`/`wss` (certificado validado) |
| Identificador de la estación | UUID de `capture_station` (Administración → Configuración → Estaciones) | |
| Avanzado (sin gateway) | AuthService `http://127.0.0.1:5001`, SessionService `http://127.0.0.1:5004`, WS `ws://127.0.0.1:5002/ws` | Con VisionWorker en el host, el `/ws` del gateway no lo alcanza: indique el WS directo |

Luego el Operador inicia sesión (correo y contraseña de AuthService). Se rechaza cualquier cuenta sin rol
`station_operator`.

* `userData\station-config.json`: solo URLs, estación y preferencias (sin secretos).
* `userData\credentials.bin`: **solo** el *refresh token*, cifrado con `safeStorage` (DPAPI de Windows, ligado
  al usuario de Windows). La contraseña nunca se guarda. El *access token* vive solo en memoria.
* Sin cifrado disponible, el token se conserva solo en memoria (hay que iniciar sesión tras reiniciar).
* La sesión de Operador de AuthService dura como máximo `OP_MAX_SESSION_HOURS` (24 h): después hay que volver a
  iniciar sesión (la app lo pide; el semáforo queda en gris).
* `PEF_MONITOR_USER_DATA` permite un perfil aislado (pruebas, varias estaciones en un equipo).
* `PEF_MONITOR_ALLOW_INSECURE_LAN=1` permite `http`/`ws` en la LAN (solo laboratorio; queda en el log).

El backend vuelve a verificar en **cada** petición la institución, la estación y la asignación: el cliente
nunca decide qué sesión puede ver.

## 7. Sincronización con el portal y flujo de sesiones

1. Al iniciar: carga configuración → renueva credencial → `GET …/stations/{id}/monitor`.
2. `idle` → pantalla de espera ("Esperando sesión del Operador"), semáforo gris. Sondeo cada 4 s.
3. `active` (el Operador pulsó *Start* en el portal) → la ventana se muestra/enfoca (también desde la bandeja),
   se pide un ticket observador y se abre el WebSocket. Sondeo cada 2 s (fase, referencia, estado).
4. Cambios de fase del portal → se reflejan; el estabilizador se reinicia.
5. La sesión deja `in_progress` → se cierra el WebSocket, se liberan fotograma/detecciones/lecturas y se consulta
   `…/monitor/{session_id}`: revisión (`awaiting_spd_review`, `correction_required`, `or_hold`) se muestra hasta
   3 min; terminada (`closed`, `aborted`) 1 min; después vuelve a espera (y a la bandeja si se activó
   "ocultar en espera").
6. `ambiguous` → espera con aviso y lista de sesiones; no se elige ninguna.

| Situación | Comportamiento |
|---|---|
| Reinicio de Electron | El primer sondeo recupera la misma sesión y fase |
| Reinicio de SessionService / red | Gris "Reconectando", reintentos 4→15 s, recupera la sesión |
| Reinicio de VisionWorker | WebSocket reconecta 1→15 s con ticket nuevo; "VisionWorker sin la sesión" hasta que el portal vuelva a vincular |
| Ticket expirado | Se pide uno nuevo; tras 3 rechazos seguidos se detiene |
| Revocación (logout, usuario desactivado, contraseña restablecida) | El siguiente sondeo/renovación recibe 401 → se detiene el video y se pide iniciar sesión (≤ 2 s por sondeo; ≤ 20 s por renovación del ticket) |
| Sesión de otra estación / institución | El servidor responde 404; el cliente descarta además cualquier sesión o mensaje con otra estación/sesión |
| Sesiones duplicadas | Una sola instancia, un solo observador por sesión, una sola activación de ventana |

## 8. Inicio automático en Windows

Un servidor **no puede** abrir un programa cerrado en otra computadora. Soluciones implementadas:

1. **Iniciar con Windows** (menú de la bandeja u Opciones): entrada `HKCU\…\Run` por usuario con `--hidden`.
   La app arranca minimizada en la bandeja, sondea, y **muestra la ventana sola** cuando el Operador inicia una
   sesión en esa estación.
2. **Bandeja**: cerrar la ventana la oculta; "Salir" está en el menú de la bandeja.
3. **Protocolo `pef-monitor://open`** (registrado por el instalador): un enlace en el mismo equipo abre o enfoca
   la app. Solo se acepta exactamente `open`; nunca se toman ids de la URL.
4. **Instancia única**: un segundo lanzamiento enfoca la ventana existente.

## 9. Semáforo

| Color | Cuándo | Texto / icono |
|---|---|---|
| **Verde provisional** | Conteo observado = referencia, familias coinciden, lecturas recientes y suficientes (cualquier fase en curso) | ✓ "Verde provisional" · "LECTURA PROVISIONAL · NO AUTORIZA CIERRE" |
| **Verde confirmado** | Solo desde el resultado oficial: validación del Operador sin discrepancias (`validated_match`) o cierre limpio | ✓ "CONFIRMADO OFICIALMENTE" |
| **Amarillo** | Durante conteo inicial/procedimiento: faltan piezas (pueden estar en uso), sobrante, o total igual con clasificación distinta | ⚠ "Faltan N…: no es una pérdida confirmada" |
| **Rojo** | En la fase oficial `final_count`: faltante o sobrante; o discrepancia oficial abierta | ⛔ "Instrumental faltante durante la verificación final. Requiere conciliación." |
| **Gris** | Sin sesión, sin autorización, servidor o VisionWorker no disponible, reconexión, sin captura, datos de más de 6 s, menos de 3 lecturas en 8 s, sesión manual, sin referencia, cierre con discrepancias, abortada | ? / ⏸ con el motivo |

* El conteo observado = cajas asignadas a familias de la referencia + cajas sin familia (material físico). Si hay
  cajas sin clasificación confiable, la tabla muestra "Sin determinar" en lugar de inventar qué familia falta.
* **Estabilización visual** (`src/shared/semaphore.ts`, `UiStabilizer`): un color nuevo se pinta tras **3
  lecturas nuevas consecutivas** y **≥ 1.5 s** (≈ 2.5 fps del observador). Justificación: un fotograma aislado
  (oclusión, mano) no hace parpadear la luz, y el retardo máximo (~1.5–2 s) es menor que el de un humano
  revisando la charola. Los grises de seguridad y los resultados oficiales se aplican **inmediatamente**: nunca
  queda verde si no se puede verificar. Fotogramas repetidos o fuera de orden no cuentan. Cambio de referencia o
  de fase reinicia la ventana.
* La estabilización no modifica conteos, capturas, resultados de YOLO, referencia ni reglas: solo decide qué color
  pintar.
* Un video subido y ya procesado (`ready`) conserva su último resultado, marcado "(resultado del video procesado,
  no en vivo)".

## 10. Fases

| Concepto | Backend real | Monitor |
|---|---|---|
| Esperando sesión | sin `in_progress` en la estación | Pantalla de espera |
| Conteo inicial | `in_progress` + `start` | Verde solo provisional |
| Procedimiento | `in_progress` + `demo_phase_*` (u otra) | Amarillo ante faltantes |
| Verificación final | `in_progress` + `final_count` (cambio hecho por el Operador en el portal) | Rojo ante faltantes |
| Validación y revisión | `awaiting_spd_review`, `correction_required`, `or_hold` | Resultado oficial, sin video |
| Terminada / no resuelta | `closed`, `aborted` | Resultado oficial → espera |

El monitor nunca infiere que la cirugía terminó porque la cámara ve menos objetos. Decisiones D1–D8 sin aplicar.

## 11. Interfaz

* Encabezado: logo, estación y quirófano, sesión (referencia corta), fase, estado de conexión, menú.
* Centro: cámara (~68 % del ancho) con insignias de fuente (**física registrada**, **simulada**, **video
  pregrabado**, **demostración**) y del enlace; a la derecha, semáforo grande, nombre del estado, `12 / 15`,
  faltantes/sobrantes, mensaje e indicador provisional/confirmado.
* Inferior: barra discreta de conexión, VisionWorker, fuente y última sincronización. La tabla por familia
  permanece disponible en los datos del backend, pero no se muestra en el monitor.
* Pantallas de espera, configuración, error, reconexión (banner), revisión y terminada. Tema oscuro de alto
  contraste con el teal del portal; se adapta a ventanas estrechas.
* Sin funciones administrativas.

## 12. Seguridad

`contextIsolation`, `sandbox` (también `app.enableSandbox()`), `nodeIntegration: false`, `webSecurity`, sin
`allowRunningInsecureContent`; renderer servido por protocolo propio `pef-app://monitor` con CSP estricta
(`script-src 'self'`, `connect-src 'none'`, sin `unsafe-*`) y sin traversal; navegación, `window.open`,
`<webview>`, descargas y permisos (cámara incluida) denegados; errores TLS rechazados y almacén de certificados
de Windows añadido a las raíces; IPC con validación de origen (solo `pef-app://monitor`) y de esquema de cada
mensaje; tokens solo en el proceso principal; logs con redacción de JWT, tickets, contraseñas e imágenes
(`userData\logs\monitor.log`, rotación 3 × 1 MB).

## 13. Pruebas

```powershell
npm test                 # Vitest: 80 pruebas (semáforo, fases, IPC, configuración, credenciales, logs, API, WebSocket real, orquestador)
npm run test:electron    # 15 comprobaciones sobre el binario real de Electron (modo demo)
npm run typecheck
# Backend
cd ..\SessionService; python -m pytest -q tests/test_electron_station_monitor.py   # PostgreSQL/Redis reales
cd ..\VisionWorker;   python -m pytest -q tests/test_electron_monitor.py           # servidor WebSocket real
```

### Prueba E2E con Docker Compose (pila aislada)

```powershell
# .env propio con puertos desplazados y VISION_MONITOR_SECRET; proyecto separado: no toca otras pilas ni volúmenes
docker compose -p pef_electron_e2e -f docker-compose.integral.yml --env-file <e2e.env> --profile gateway up -d --build
python apps/PEF-Electron/e2e/e2e_electron.py <e2e.env>
```

`e2e_electron.py` usa la pila real (Auth, Session, gateway, portal, PostgreSQL, Redis) y la app Electron real
conducida por su interfaz (`e2e/electron-ui.mjs`, Playwright). **Simula** el detector YOLO y la cámara
(`e2e/vision_sim_worker.py` ejecuta el código real de VisionWorker con un detector sintético;
`e2e/camera_sim.py` sustituye la cámara del navegador). Ninguna de las dos se presenta como hardware real.

## 14. Compilación y ejecutable para Windows

```powershell
npm run dist:win   # release\PEF-Monitor-Setup-<versión>.exe (NSIS, x64, por usuario)
npm run dist:dir   # release\win-unpacked\PEFMonitor.exe sin instalador
```

* Iconos generados desde código (`scripts/make-icons.mjs` → `build/icon.png`, `build/tray.png`).
* Firma: defina `CSC_LINK`/`CSC_KEY_PASSWORD` en el entorno de compilación (nunca en Git). Sin ellos el
  instalador sale sin firma y SmartScreen lo advierte.
* Si electron-builder falla al extraer `winCodeSign` con "Cannot create symbolic link", active el *Modo de
  desarrollador* de Windows o compile en una consola de administrador. Alternativa sin editar el ejecutable
  (sin icono incrustado en el .exe):
  `npx electron-builder --win nsis -c.win.signAndEditExecutable=false`.
* `release/`, `dist/`, `build/` y `node_modules/` están en `.gitignore`.

## 15. Solución de problemas

| Síntoma | Causa / acción |
|---|---|
| La app se abre como Node (`app` undefined) | `ELECTRON_RUN_AS_NODE=1` heredado de VS Code: elimínelo |
| "Estación no encontrada…" | UUID incorrecto o estación de otra institución |
| "La estación está deshabilitada" | Reactívela en Administración |
| Siempre gris "Video no disponible" | Falta `VISION_MONITOR_SECRET` en SessionService o VisionWorker (deben coincidir) |
| Gris "VisionWorker sin la sesión" | El worker se reinició: el Operador debe reabrir la captura en el portal |
| Gris "Sin captura" | El Operador no transmite cámara ni procesa video |
| Gris "Conectando video" permanente con gateway | VisionWorker corre en el host: configure el WS directo (`ws://127.0.0.1:5002/ws`) |
| Pide iniciar sesión cada día | Límite `OP_MAX_SESSION_HOURS` de AuthService |
| "Varias sesiones activas" | Más de una sesión `in_progress` del Operador en la estación: resuélvalo en el portal |

## 16. Limitaciones conocidas

* La cámara física sigue sin prueba integral. ELECTRON-03 sí procesó `Video Camino Feliz.mp4` con el `best.pt` real
  en una pila Docker aislada y reprodujo el derivado H.264 en Electron; véase
  [reporte y capturas](../../docs/electron03/REPORTE.md). Los E2E anteriores de cámara siguen siendo simulados.
* La sincronización es por sondeo (2–4 s): no existe un bus de eventos de sesión en la arquitectura actual.
* El monitor muestra sesiones asignadas a la cuenta con la que inició sesión (regla existente); una cuenta
  compartida de estación requeriría una decisión de roles (no se creó ninguna).
* El bloqueo MS-03.1 del final incorrecto no existe en el portal de `main`: el rojo es solo visual y la
  diferencia se registra como discrepancia al validar.
* El *refresh token* del Operador caduca a las 24 h (política de AuthService).
* El ticket observador no lleva usuario: la revocación se aplica en la renovación (≤ TTL/3) y en el sondeo HTTP.
* Las pruebas MS de `apps/VisionWorker/tests` previas (p. ej. `test_live_ws_auth.py`) apuntan a funciones de la
  rama de microservicios que no están en el VisionWorker de `main`; no forman parte de este cambio.
