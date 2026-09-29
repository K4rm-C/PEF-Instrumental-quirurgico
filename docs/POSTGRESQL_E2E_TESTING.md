# PostgreSQL E2E Test Setup

> **Language rule.** English is the canonical language for persisted database business data and catalog data. UI localization is handled at the application layer.

Guía para la primera validación del monolito contra **PostgreSQL real**.

> **Estado: en curso (reportado por el equipo, 2026-09-29).** Sobre PostgreSQL 17 (`pef_instrumental_dev`,
> usuario `pef_app`): `001`, `002`, `004` y `005_canonical_english_data.sql` aplicados; ambos servicios, el login
> real, las WorkSessions y el flujo E2E funcionan con `FRONTEND_DEMO_MODE=false`. **Pendiente:** probar el
> cambio de idioma EN → ES → EN del apartado 7 (nuevo). Las pruebas automatizadas siguen usando SQLite temporal.
>
> **Language rule (UI).** La interfaz está disponible en inglés (`en`, predeterminado) y español (`es`); la base
> de datos sigue guardando únicamente valores canónicos en inglés.

## 0. Pruebas automatizadas (sin PostgreSQL)

Desde la raíz del repositorio, con las dependencias de desarrollo instaladas:

```bash
pip install -r apps/BackendWebFlask/requirements-dev.txt -r apps/BackendAuthService/requirements-dev.txt
python -m pytest apps/BackendWebFlask/tests apps/BackendAuthService/tests -s
```

Cada suite puede ejecutarse por separado (`python -m pytest apps/BackendWebFlask/tests/test_review.py -s`)
o directamente como script (`python apps/BackendWebFlask/tests/scenarios/review.py`). La comprobación
estática del seed E2E (`test_seed_e2e.py`) usa además el parser real de PostgreSQL si el paquete opcional
`pglast` está instalado (`pip install pglast`).

## 1. Crear la base de datos

```bash
createdb -U postgres pef_e2e
```

PostgreSQL 15+; `001_init.sql` ejecuta `CREATE EXTENSION IF NOT EXISTS pgcrypto` (requiere permisos).

## 2. Esquema y seeds (en este orden)

```bash
psql -U postgres -d pef_e2e -v ON_ERROR_STOP=1 -f data/migrations/001_init.sql
psql -U postgres -d pef_e2e -v ON_ERROR_STOP=1 -f data/seeds/002_seed_catalogs.sql
psql -U postgres -d pef_e2e -v ON_ERROR_STOP=1 -f data/seeds/004_seed_e2e.sql
```

- `002` incluye los catálogos que el flujo actual necesita (`low_confidence`, `correction_requested`).
- `004_seed_e2e.sql` es idempotente y crea: institución "Hospital E2E (testing)", quirófano `E2E-OR-1`
  ("E2E Operating Room 1"), estación "Overhead Capture Station E2E-OR-1", roles `it_admin` / `operator_cde` /
  `supervisor_quality` ("IT Administrator", "Operator CDE", "Supervisor CDE / Quality"),
  tres usuarios, familias `KELLY`, `MOSQUITO`, `METZ`, `MAYOHEG`, `FARABEUF`, el kit
  "General Surgery Kit E2E" (Kelly Forceps 6, Mosquito Forceps 8, Metzenbaum Scissors 2, Mayo-Hegar Needle Holder 2, Farabeuf Retractor 2), `ProcedureKit` + tres `ProcedurePhase` requeridas
  (`pre_incision`, `pre_closure`, `final_count`) para `appendectomy`, paciente, médica, una operación
  **scheduled** y el `YoloModel` de desarrollo `e2e-controlled-dev` (activo, sin pesos) con
  `ModelClass` 0–4 → KELLY, MOSQUITO, METZ, MAYOHEG, FARABEUF.
- Si la base ya tenía otro modelo activo, el seed lo desactiva (solo puede haber uno activo).
- `003_seed_demo.sql` **no** es necesario (escenario histórico; sus contraseñas son placeholders).

### Base de datos ya existente (sembrada antes del cambio a inglés)

Si la base se sembró con la versión anterior (valores en español) de `002`/`004`, convertirla con:

```bash
psql -U pef_app -d pef_instrumental_dev -v ON_ERROR_STOP=1 -f data/migrations/005_canonical_english_data.sql
```

Es solo de datos, transaccional e idempotente (una segunda ejecución no cambia nada). En una instalación nueva
no hace falta: `002` y `004` ya producen los valores en inglés.

## 3. Variables de entorno

`apps/BackendAuthService/.env`:

```env
DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/pef_e2e
JWT_SECRET_KEY=<cadena aleatoria larga>
AUTH_COOKIE_SECURE=false          # solo local por http
HOST=127.0.0.1
PORT=5001
```

`apps/BackendWebFlask/.env`:

```env
DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/pef_e2e
SESSION_SECRET_KEY=<cadena aleatoria>
AUTH_SERVICE_URL=http://127.0.0.1:5001
FRONTEND_DEMO_MODE=false
CAPTURE_STORAGE_ROOT=             # vacío -> apps/BackendWebFlask/instance/evidence
CAPTURE_MAX_BYTES=10485760
VISION_INFERENCE_PROVIDER=controlled
VISION_CONFIDENCE_THRESHOLD=0.70
```

## 4. Arrancar los servicios

```bash
cd apps/BackendAuthService && pip install -r requirements.txt && python main.py     # :5001
cd apps/BackendWebFlask   && pip install -r requirements.txt && python app.py      # :5000
```

`requirements.txt` de BackendWebFlask incluye ahora `Flask-Babel`, `Babel` y `pytz`: en un entorno ya creado
hay que volver a ejecutar `pip install -r requirements.txt` (sin eso la app no arranca: `No module named
'flask_babel'`). Los catálogos compilados (`translations/es/LC_MESSAGES/messages.mo`) están en el repositorio;
no hace falta compilarlos para arrancar.

Abrir `http://127.0.0.1:5000/sign-in`.

## 5. Credenciales E2E

**Solo para pruebas locales — no usar en producción.** Contraseña de los tres usuarios: `E2eTest#2026`.

| Rol | Usuario |
|---|---|
| Administrator (`it_admin`) | `admin.e2e@pef.local` |
| Operator (`operator_cde`) | `operator.e2e@pef.local` |
| Supervisor (`supervisor_quality`) | `supervisor.e2e@pef.local` |

## 6. Flujo manual

Valores sugeridos para que el resultado sea verificable. Umbral de confianza 0.70.

**Administrator**
- [ ] Login correcto; Admin Audit Log muestra `LOGIN`.
- [ ] Instrument Families, Instruments, Kits (composición 6/8/2/2/2), Procedures (`Appendectomy` con kit y 3 fases), Configuration (quirófano y estación), Users/Roles y Vision Models (`e2e-controlled-dev` activo, 5 clases).
- [ ] Logout → `LOGOUT` en el audit log.

**Operator**
- [ ] Login → New Counting Session: aparece la operación E2E; kit, fase (`Initial Count Before Incision`) y estación seleccionables; vista previa del inventario esperado (20 piezas).
- [ ] Crear la sesión → redirige a `/operator/sessions/<uuid>/capture`; inventario esperado congelado; estado `Open`.
- [ ] Subir un JPEG → la imagen se ve; estado `Counting` (`open → counting`); archivo en `instance/evidence/institutions/<inst>/sessions/<ws>/captures/`.
- [ ] Run AI Analysis (controlled): clase 0 = 6 (0.95), 1 = 7 (0.91), 2 = 2 (0.90), 3 = 2 (0.88), 4 = 2 (0.60) → resultados: Mosquito `Shortage` (-1), Farabeuf `Low confidence`; estado `Validating` (`counting → validating`).
- [ ] Human Validation: Mosquito = 8 con justificación; resto sin cambios → Validation Summary muestra IA 7 ≠ Humano 8.

**Supervisor**
- [ ] Login → Discrepancies: 2 abiertas (Mosquito shortage, Farabeuf low confidence) en `Under Review`.
- [ ] Review: evidencia visible, esperado / IA / humano, historial de corrección.
- [ ] Request Correction sobre Mosquito (nota obligatoria) → `Correction Required`.

**Operator**
- [ ] Correction Requested muestra la nota; enviar 8 (confirmación) → vuelve a `Under Review`.
- [ ] Ready to Close indica "Not ready to close" y el botón está deshabilitado. Opcional: un POST directo a `/operator/sessions/<uuid>/close` (p. ej. desde las herramientas del navegador) responde **409** y deja `close_blocked` + `CLOSE_SESSION denied` en el audit log.

**Supervisor**
- [ ] Approve Mosquito y Farabeuf → `Approved`, `resolved_at` informado.

**Operator**
- [ ] Ready to Close → Close Session → estado `Closed`; Closed Session Details con esperado, IA, humano, correcciones, decisiones, evidencia y actividad.
- [ ] Segundo POST de cierre → sin duplicados.

**Dashboards y auditoría**
- [ ] Operator Dashboard: 1 sesión cerrada, 0 discrepancias pendientes.
- [ ] Supervisor Dashboard / Indicators / Reports: Sessions 1, Discrepancies 2, Resolved 2, Human Corrections 1, AI-Human Agreement 80.0 % (4 de 5 filas coinciden), Average Resolution Time distinto de N/A.
- [ ] Admin Dashboard: mismas métricas operativas.
- [ ] Admin Audit Log y Supervisor Audit Log: `CREATE_SESSION`, `START_COUNT`, `CAPTURE_EVIDENCE`, `VISION_RESULT`, `CREATE_DISCREPANCY`, `HUMAN_CORRECTION`, `HUMAN_CONFIRMATION`, `SUBMIT_VALIDATION`, `REQUEST_CORRECTION`, `SUPERVISOR_REVIEW`, `APPROVE_DISCREPANCY`, `CLOSE_SESSION`.

## 7. Idioma de la interfaz (EN → ES → EN)

El selector **EN / ES** de la cabecera (y de las páginas públicas) envía `POST /preferences/locale`.

**Anónimo**
- [ ] Abrir `http://127.0.0.1:5000/` en una ventana privada → inglés (`<html lang="en">`, "Sign In").
- [ ] Selector → **Español** → la misma página en español ("Iniciar sesión", "Cómo funciona"); ir a Sign In → español.

**Operator** (`operator.e2e@pef.local`)
- [ ] Iniciar sesión → la interfaz usa la preferencia guardada del usuario (`en` por defecto), aunque la
      página pública estuviera en español.
- [ ] En *Counting Sessions* elegir **Español** → misma pantalla en español; la sesión sigue iniciada.
- [ ] Recorrer Panel, Nueva sesión de conteo (procedimiento "Apendicectomía", fase "Conteo inicial antes de la
      incisión", familias "Pinza Kelly", …), Captura, Conteo sugerido por IA ("Inferencia controlada (proveedor de
      desarrollo)"; `e2e-controlled-dev`, IDs de clase y confianzas sin traducir), Validación humana, Historial y
      Mi perfil ("Operador CDE") → todo en español.
- [ ] Nombres creados por usuarios y texto libre sin traducir: kit "General Surgery Kit E2E", "E2E Operating
      Room 1", "E2E Patient 001", "Dr. E2E Surgeon", justificaciones escritas.
- [ ] Cerrar sesión → Sign In sigue en español → iniciar sesión de nuevo → español restaurado.
- [ ] Elegir **English** → inglés; cerrar sesión e iniciar de nuevo → inglés.

**Supervisor** y **Administrator** (verificación básica)
- [ ] Supervisor → **Español**: Discrepancias ("En revisión", motivo "Faltante respecto al inventario esperado"),
      Revisión (Aprobar / Solicitar corrección / Rechazar), Reportes, Indicadores, Bitácora de auditoría (los
      códigos `CREATE_SESSION`, … no se traducen) → volver a **English**.
- [ ] Administrator → **Español**: Familias de instrumental ("Pinza Kelly"; al editar, el campo sigue mostrando
      "Kelly Forceps", el valor canónico guardado), Procedimientos, Usuarios, Roles, Modelos de visión,
      Configuración → volver a **English**.

**Verificación en la base de datos**
```sql
-- preferencia: solo cambia "locale", el resto de claves se conserva
SELECT email, ui_preferences FROM "user" WHERE email LIKE '%.e2e@pef.local' ORDER BY email;
-- los valores canónicos NO cambian al usar la interfaz en español
SELECT code, name FROM instrument_family WHERE code IN ('KELLY','MOSQUITO','METZ','MAYOHEG','FARABEUF') ORDER BY code;
SELECT code, name FROM cat_procedure_type WHERE code = 'appendectomy';
SELECT code, name FROM cat_session_status ORDER BY code;
SELECT code, description FROM role WHERE institution_id = 'e2e00000-0000-4000-8000-000000000001' ORDER BY code;
```
Esperado: `Kelly Forceps`, `Appendectomy`, `Open`/`Counting`/`Validating`/`Closed`… en inglés siempre, y
`ui_preferences` con `"locale": "es"` o `"en"` según la última elección (más `"theme"`).

## 8. Consultas de verificación

```sql
SELECT st.code, ws.ended_at, ws.closed_by_user_id FROM work_session ws JOIN cat_session_status st ON st.id = ws.status_id;
SELECT source, count(*) FROM expected_inventory GROUP BY source;
SELECT et.code, count(*) FROM count_event ce JOIN cat_event_type et ON et.id = ce.event_type_id GROUP BY et.code;
SELECT r.code, d.resolved, d.resolved_at FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id;
SELECT count(*) FROM human_correction;
SELECT kind, storage_provider, object_key, size_bytes FROM media_asset;
SELECT action, outcome, count(*) FROM access_audit GROUP BY action, outcome ORDER BY action;
```

Registrar cualquier diferencia de comportamiento respecto a SQLite (JSONB, TIMESTAMPTZ, índices parciales,
`CAST(... AS uuid/inet)` del auth service) en `docs/IMPLEMENTATION_STATUS.md`.
