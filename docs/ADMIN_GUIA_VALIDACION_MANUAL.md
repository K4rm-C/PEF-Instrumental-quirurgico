# Guía de validación en navegador — Administración (RF-AD-01 … RF-AD-10)

**Estado (2026-11-03, ADMIN-09).** Las verificaciones de las secciones 1 y 2 **se ejecutaron en un navegador real automatizado**: Chromium controlado por Playwright con `scripts/browser_validate_admin.py`, contra el entorno aislado y con un `best.pt` real **provisional** (versión anterior del modelo, no la definitiva). Resultado: **95 PASS · 0 FAIL · 0 BLOCKED**. Evidencia en `docs/evidencias/ADMIN-09/` (`results.md` con capturas por comprobación). **Todavía no las ha revisado una persona.** Lo que queda para ti está en la sección 3, con el formato de reporte en la sección 4.

Estados usados: **Aprobado** (ejecutado y correcto) · **Fallido** (ejecutado y con error) · **Bloqueado** (no se pudo ejecutar) · **Pendiente de validación manual** (requiere a una persona).

### Repetir la validación automatizada

```powershell
docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
python scripts/browser_validate_admin.py --weights <ruta al best.pt real>   # usa el venv del portal + playwright
```

### Abrir el entorno para probar a mano

```powershell
python scripts/run_admin_browser_env.py                 # almacenamiento local (.e2e/admin_storage)
python scripts/run_admin_browser_env.py --storage s3    # Garage de pruebas (bucket pef-admin-browser)
# Abrir http://127.0.0.1:15000/sign-in · Ctrl+C para detener
```

## 0. Preparación

1. Entorno aislado arriba: `docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait`.
2. Arrancar AuthService y el portal contra la BD de prueba con `python scripts/run_admin_browser_env.py`, o levantar la pila completa de `docker-compose.yml` con sus migraciones 009 y 010 aplicadas.
3. Cuentas demo (`data/seeds/003_seed_demo.sql`): `admin@instrumed.com / DemopwdADM78!` (it_admin), `supervisor@instrumed.com / DemopwdSPD78!` (spd_supervisor), `operator@instrumed.com / DemopwdOP78!` (station_operator).
4. Archivos de prueba: una foto JPG/PNG < 5 MB; un PDF y un HTML pequeños; un archivo renombrado (`.exe` → `.pdf`); un PDF > 10 MB; un `.pt` (sintético: `python -c "..."` con `tests/test_admin_vision_models.make_checkpoint`, o el `best.pt` real cuando esté disponible) y un `names.yaml`.
5. Para probar aislamiento: una segunda institución con su it_admin (se crea con SQL en la BD de prueba, nunca en una BD con datos reales).
6. Probar también en español (selector de idioma) y con tema claro/oscuro en al menos un RF.

## 1. Verificaciones por requisito

| RF | Ruta / rol | Datos | Acciones | Resultado esperado | Permisos | Errores a revisar | Resultado |
|---|---|---|---|---|---|---|---|
| **01 Dashboard** | `/admin/dashboard` · it_admin | Sesiones en `in_progress`, `or_hold`, `correction_required`, `awaiting_spd_review` (crear con el flujo del Operador/Supervisor) | Abrir; cambiar umbrales en Configuración y volver; detener Redis o Mongo (`docker compose stop redis`) y recargar | Tarjetas por estado con color según umbral; familias activas/inactivas, modelo activo, usuarios inactivos de la institución; "System Health" con Auth/PostgreSQL/Redis/MongoDB/almacenamiento; al detener Redis aparece "Unreachable" y el resto del panel carga | Supervisor/Operador → 403; anónimo → login | Tarda > 2 s con AuthService caído (timeout del ping); números de otra institución | Aprobado (navegador 4/4) · revisión visual: Pendiente de validación manual |
| **02 Familias** | `/admin/instrument-families` · it_admin | Una familia nueva; foto JPG/PNG; archivo no imagen | Crear, editar, buscar (inglés y español); desactivar una familia **mapeada en el modelo activo** sin y con la casilla de confirmación; subir foto, editar texto/orden, quitarla; subir un archivo renombrado y uno > 5 MB | Código duplicado → mensaje; desactivar sin confirmar → mensaje y sigue activa; con confirmar → inactiva (badge); foto visible en la tabla; archivos inválidos rechazados con mensaje | Supervisor → 403 | Imagen que no carga (ruta `/examples/<id>/image`); mensajes sin traducir | Aprobado (9/9) · Pendiente de validación manual (visual, es-MX) |
| **03 Procedimientos y catálogos** | `/admin/procedures`, `/admin/selection-catalogs/{phases,categories,reasons}` · it_admin | Procedimiento con 2 fases; nuevo motivo | Crear/editar procedimiento con fases y orden; ver "Associated Kits" (solo lectura); crear fase/categoría/motivo; intentar código con mayúsculas/espacios; desactivar una fase | Fases guardadas en orden; kits visibles pero no editables; códigos únicos e inmutables; fase inactiva con su estado | Supervisor → 403 | Orden de fase repetido; procedimiento con código duplicado | Aprobado (8/8) · Pendiente de validación manual (visual) |
| **04 Avisos de privacidad** | `/admin/privacy-notices` · it_admin | PDF, HTML, `.exe` renombrado, PDF > 10 MB | Publicar PDF; publicar el mismo documento con otra versión (aparece confirmación); descargar; desactivar/activar; activar "una sola versión activa" en Configuración y activar otra | Versión listada con checksum y vigencia; duplicado pide confirmación; descarga como adjunto; inválidos rechazados; con "una sola activa" solo queda una | Supervisor → 403; en la programación del Supervisor solo aparecen versiones activas | HTML abriéndose en línea (no debe); versión duplicada | Aprobado (10/10, incluido un aviso de 9 MB en Garage) · Pendiente de validación manual (visual, es-MX) |
| **05 Usuarios** | `/admin/users` · it_admin | Usuario nuevo `or_charge` con una sala; operador existente | Crear (institución fija, roles, sala); intentar email duplicado y contraseña corta (según política); editar; desactivar y comprobar que su sesión abierta en otro navegador termina; reactivar; restablecer contraseña y comprobar que la sesión anterior termina y solo la nueva contraseña funciona; intentar desactivarse a sí mismo | Mensajes de validación; sesiones cerradas tras desactivar/reset (si AuthService no responde, aviso "could not be closed"); no puede desactivarse ni quitarse it_admin | Supervisor → 403; usuarios de otra institución no aparecen y su URL da 404 | Lista que muestre otra institución; aviso de revocación ausente | Aprobado (8/8, incluida la sesión en otro navegador) · Pendiente de validación manual (visual) |
| **06 Roles** | `/admin/roles` · it_admin | — | Ver los 4 roles; editar descripción de `or_charge`; intentar `/admin/roles/new` | 4 códigos fijos en orden; sin botones crear/borrar; descripción guardada; `/new` → 404 | Supervisor → 403; rol de otra institución → 404 | Descripción vacía o > 255 | Aprobado (5/5) · Pendiente de validación manual (visual) |
| **07 Salas y estaciones** | `/admin/configuration` · it_admin | Sala nueva; estación con ROI 1920×1080 | Crear sala (código duplicado → mensaje); crear estación con ROI válido; editar con ROI fuera del encuadre; dejar campos vacíos (conserva); quitar ROI; desactivar sala | ROI guardado ("Configured"); errores claros; desactivar no borra histórico | Supervisor → 403 | ROI parcial aceptado (no debe) | Aprobado (8/8) · Pendiente de validación manual (visual) |
| **08 Modelos de visión** | `/admin/vision-models` · it_admin | `.pt` sintético o **`best.pt` real**; `names.yaml`; un `.onnx` | Subir pesos (paso 1); ver checksum; mapear clases (autocompletado), rechazar una; publicar sin y con confirmación de familias faltantes; activar (modal); intentar desactivar el activo con estaciones activas; subir de nuevo el mismo archivo | Paso 2 con clases leídas; confirmación de familias faltantes; un único modelo activo; checksum duplicado rechazado; desactivar bloqueado | Supervisor → 403 | Archivos grandes (tiempo de subida, timeout de gunicorn 300 s) | Aprobado con `best.pt` real (11/11): validación administrativa · inferencia real: Bloqueado por P1/P2 |
| **09 Auditoría** | `/admin/audit-log` · it_admin | Acciones realizadas en los pasos anteriores; eventos de otra institución | Filtrar por usuario, acción, resultado, entidad, rango de fechas; poner "From" posterior a "To"; paginar | Solo eventos de la institución; rango vacío muestra aviso y no lista; paginación correcta | Supervisor → 403 (su bitácora es `/supervisor/audit-log`) | Eventos de otra institución o sin institución visibles | Aprobado (6/6) · Pendiente de validación manual (visual) |
| **10 Configuración** | `/admin/configuration` · it_admin | — | Editar nombre de la institución; intentar desactivarla; cambiar parámetros (umbrales, contraseña, tamaños, MIME); poner un valor inválido (warning > danger, MIME vacío) | Cambios persistentes por institución; mensajes de validación; no se puede desactivar la propia institución | Supervisor → 403 | Parámetros que no afectan las subidas o la política de contraseña | Aprobado (4/4) · Pendiente de validación manual (visual, es-MX) |

## 2. Verificaciones transversales

| Verificación | Resultado (navegador automatizado) |
|---|---|
| Autenticación de los tres roles (login, navegación y logout; renovación silenciosa en el smoke real 51/51) | Aprobado |
| `or_charge`: AuthService lo autentica; el portal muestra "Your account has no application role." | Aprobado (comportamiento actual por diseño) |
| Supervisor, Operador, `or_charge` y anónimo sin acceso a `/admin/*`; Admin sin acceso a piezas/kits | Aprobado |
| Recursos de otra institución (usuarios, roles, salas, estaciones, auditoría) invisibles y 404 | Aprobado |
| Catálogos compartidos con `ADMIN_CATALOG_STEWARD_INSTITUTION_ID`: B recibe 403 con mensaje, A edita, B lee | Aprobado (tras corregir H1) |
| Persistencia tras reiniciar el portal y Garage; backend local tras reinicio | Aprobado |
| Supervisor tras cambios del Admin (familia desactivada fuera de altas nuevas, aviso inactivo fuera de la programación) | Aviso: Aprobado (integración 9/9). Familias inactivas en altas: **brecha P4** |
| Idioma es-MX y tema oscuro | **Pendiente de validación manual** |

## 3. Lo que debes validar tú (pendiente de validación manual)

Antes de empezar: `python scripts/run_admin_browser_env.py` y entra en `http://127.0.0.1:15000/sign-in` con `admin@instrumed.com`.

| # | Pantalla (URL) | Rol | Acción | Resultado esperado | Evidencia a guardar |
|---|---|---|---|---|---|
| M1 | Todas las de la tabla §1 | it_admin | Revisar las capturas `docs/evidencias/ADMIN-09/*.jpg` (o repetir a mano) | Textos claros, sin solapamientos ni elementos cortados | Lista de capturas con observaciones |
| M2 | `/admin/instrument-families`, `/admin/privacy-notices`, `/admin/vision-models`, `/admin/configuration` | it_admin | Cambiar el idioma a **ES** (selector superior) y recorrer listados, formularios y un error de validación | Todo traducido, incluido "Los catálogos compartidos los mantienen…" | Captura de cada pantalla en es-MX |
| M3 | `/admin/dashboard`, `/admin/users/new` | it_admin | Selector de tema → **Dark** | Contraste legible y badges distinguibles | 2 capturas |
| M4 | `/admin/users` → editar un usuario | it_admin | Repetir desactivar y reset en **Firefox o Edge**, con la sesión del usuario abierta en Chrome | Ese usuario vuelve a *sign-in* al recargar | Captura antes y después |
| M5 | `/admin/vision-models/new` | it_admin | Subir el `best.pt` real a mano (nueva versión: primero reactivar `yolo26l-demo` o usar otro `.pt`) | Paso 2 con 17 clases; el checksum igual al de `verify_weights_names.py` | Captura del paso 2 |
| M6 | Pila principal (`docker compose up`) | — | Solo si se decide usar Garage: seguir `GUIA_ADMIN_MONOLITO.md` §4.1 | Fotos, avisos y pesos se suben y se leen tras `restart web` | Salida de los comandos y capturas |
| M7 | Sesión de conteo del Operador | station_operator | **Después de P1/P2**: comprobar que el VisionWorker usa los pesos activados | `/health` del worker con el SHA-256 del modelo activo | Bloqueado hasta P1/P2 |

## 4. Cómo reportar un resultado incorrecto

Para cada fallo envía:

1. Id (M1…M7 o RF-AD-xx) y URL exacta.
2. Usuario de prueba con el que entraste (nunca la contraseña).
3. Pasos realizados, resultado esperado y resultado obtenido.
4. Captura completa y, si aparece, el texto del error.
5. Hora aproximada, para buscar en `.e2e/browser_web.log` y `.e2e/browser_auth.log`.
6. Si algo se rompe, ejecuta `python scripts/browser_validate_admin.py --weights …` y adjunta `docs/evidencias/ADMIN-09/results.md`.

Anota el resultado en esta guía (Aprobado / Fallido / Bloqueado) y en `PLAN_TRABAJO.md`.
