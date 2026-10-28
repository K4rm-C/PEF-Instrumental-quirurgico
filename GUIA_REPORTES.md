# PEF — Reportes del Supervisor

Las pantallas **Reportes** (`/supervisor/reports`) e **Indicadores** (`/supervisor/indicators`) del Supervisor muestran datos reales de PostgreSQL.

- Las cifras las calcula **SessionService**, el dueño de sesiones, discrepancias y hechos de conteo, en cuatro endpoints de solo lectura.
- El portal solo traduce etiquetas y da formato; no calcula nada.
- No se creó un microservicio de reportes.
- Contrato: [DS09 §1.3.4](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.3.0.
- Swagger: `http://127.0.0.1:5004/api/v1/sessions/docs`.

## 1. Análisis inicial

| Elemento | Antes | Ahora |
| :--- | :--- | :--- |
| Reportes: 4 tarjetas | Fijas: "Last generated: 10 mins ago", "View Report" sin destino | Fecha real de generación; "Ver reporte" lleva a cada sección con datos reales |
| Reportes: filtros | Solo de presentación | Periodo, kit, operador y estado, aplicados en SQL |
| Indicadores: sesiones, discrepancias, abiertas | Reales (conteo en el portal) | Reales, desde el mismo resumen y con periodo mensual funcional |
| Indicadores: acuerdo IA–humano y tiempo de resolución | `n/a` | Calculados (el acuerdo requiere al menos 5 pares) |
| Indicadores: "por familia" | En realidad mostraba resueltas / abiertas | Familias reales, más una tarjeta por estado de la discrepancia |

Ni AuditService ni CatalogService tienen esos datos: la auditoría registra acciones, no cantidades, y el catálogo no conoce las sesiones. Por eso los endpoints están en SessionService. El kit (nombre y versión) se lee de la misma base, como en el resto de sus lecturas.

## 2. Endpoints

`GET /api/v1/sessions/reports/summary`, `/kits`, `/operators` y `/options`.

Acceso y formato:
- Solo `spd_supervisor`. Operador, Administrador u otro rol reciben 403, registrado en `access_audit`.
- Validación completa del token: JWT, sesión Redis y usuario activo.
- Todas las consultas parten de un único CTE de sesiones filtrado por `operation.institution_id` de la institución del usuario. El aislamiento está en SQL, no en la interfaz.
- Transacción `READ ONLY`: PostgreSQL rechaza cualquier escritura, así que un reporte nunca modifica sesiones, discrepancias ni inventarios.
- JSON o XML (`session-reports.xsd`), errores DS09, paginación en kits y operadores.

Filtros:
- `from` / `to`: fecha (día completo en `tz`) o fecha-hora; por defecto los últimos 30 días; máximo 366.
- `tz`, `kit_id`, `operator_id` y `status`.
- Un kit u operador de otra institución responde 400 sin revelar datos.
- Un parámetro desconocido responde 400.

## 3. Cálculos

| Métrica | Definición |
| :--- | :--- |
| Periodo | `operation.scheduled_at` en [from, to) |
| Sesiones por estado | `scheduled`, `in_progress`, pendientes de revisión (`awaiting_spd_review` + `correction_required`), `closed`, `aborted` |
| Duración promedio | `avg(ended_at − started_at)`: del inicio al reporte del Operador, solo sesiones con ambas fechas |
| % con discrepancias | Sesiones reportadas (`ended_at`) con al menos una discrepancia / sesiones reportadas |
| Tiempo de resolución | `resolved_at − COALESCE(evento de conteo de origen, inicio de la sesión)`; misma fórmula que el panel de Administración |
| Por motivo / familia | `cat_discrepancy_reason` / `instrument_family`; sin motivo o fuera del kit = su propio grupo |
| Cantidad esperada / reportada (kit) | Suma de `expected_inventory`; último `manual_count` por sesión y familia; también la esperada de las sesiones contadas, para comparar |
| Versión del kit | Versión actual (`kit.version`): las sesiones no registran la versión que usaron |
| Conteos realizados (Operador) | Sesiones cuyo conteo terminó el Operador; familias contadas |
| Acuerdo IA–humano | Pares (validación humana de un conteo de visión, sugerencia de YOLO que validó): `payload.ai_suggested_quantity` o el último lote `auto_count` final. **No** se compara con el inventario esperado; es exactamente la regla corregida de `services/admin_local/dashboard.py`, y un test verifica que el SQL sea idéntico. Con menos de 5 pares: "Datos insuficientes" |
| Correcciones humanas | Pares donde el humano cambió la sugerencia de YOLO |

Una métrica sin denominador es `null` y se muestra como "Sin datos" o "Datos insuficientes", nunca como 0.

## 4. Gráficas (Highcharts 11.4.8)

- **Reportes:**
  - sesiones por estado;
  - actividad por día (sesiones y sesiones con discrepancias);
  - discrepancias por motivo;
  - sesiones por kit;
  - sesiones y discrepancias por operador.
- **Origen de los datos:** el JSON que trae la página, construido con la respuesta del servicio. No hay valores generados.
- **Paleta:**
  - colores categóricos en orden fijo, con su versión para modo oscuro;
  - validada con el script del método de visualización contra las superficies del portal;
  - tres tonos claros quedan bajo 3:1 sobre blanco, por eso cada gráfica tiene etiquetas de datos y una tabla con las mismas cifras.
- **Sin datos:** un estado vacío explícito, sin gráfica.

## 5. Pruebas

```powershell
cd apps/SessionService; python -m pytest -q tests/test_reports.py   # PostgreSQL real, resultados calculados a mano
cd apps/BackendWebFlask; python -m pytest -q tests/test_reports_remote.py
python scripts/e2e_reports.py <carpeta_videos>                      # servicios vivos: web, Auth, VisionWorker, session
```

`scripts/e2e_reports.py` hace cuatro cosas:
1. Recalcula en Python, desde filas crudas, cada cifra de los cuatro endpoints, para todo el periodo y con cada filtro.
2. Ejecuta un flujo real: programar, conteo YOLO inicial y final, validación humana con un faltante, resolución y cierre. Luego comprueba que los reportes cambien exactamente en lo que esos hechos implican.
3. Prueba el aislamiento con un Supervisor de otra institución creado por Administración, además de permisos, parámetros y XML.
4. Comprueba que las gráficas y tarjetas de las pantallas RF muestren las mismas cifras.

## 6. Revisión final de consistencia y seguridad

### 6.1 Dashboard del Administrador: alcance institucional

Corregido. Los indicadores operativos ahora solo cuentan datos de la institución del administrador (`services/admin_local/dashboard.py`):
- total de sesiones, discrepancias y discrepancias abiertas;
- correcciones humanas;
- acuerdo IA–humano;
- tiempo promedio de resolución.

Antes sumaban todas las instituciones. AuditService ya limita al administrador a su institución, también en los `count_event`.

**Filtro aplicado:** `work_session → operation.institution_id`. Las sesiones sin operación no tienen institución y no se cuentan, igual que en SessionService. La regla del acuerdo IA–humano no cambió: `apps/SessionService/tests/test_reports.py` comprueba que su SQL sea idéntico al de los reportes.

**Siguen siendo globales a propósito:**
- usuarios activos e inactivos, roles e instituciones: el administrador los gestiona para todas las instituciones (GUIA_AUTH);
- familias, procedimientos y modelos de visión: catálogos sin institución.

Los kits, instrumentos, quirófanos y estaciones ya estaban acotados.

**Pruebas:** `apps/BackendWebFlask/tests/test_admin_dashboard_isolation.py`, sobre PostgreSQL real:
- los hechos de la institución B no cambian lo que ve A;
- los de A lo cambian exactamente en su cantidad;
- los indicadores globales siguen globales;
- los kits están acotados.

Antes de aceptarla, ejecuté la prueba contra la versión anterior: falla 2 de 4.

### 6.2 Indicadores del Supervisor sin SessionService

| Situación | Resultado |
| :--- | :--- |
| SessionService configurado y responde | Indicadores reales del resumen |
| Configurado y no responde, 5xx o error 4xx | 503 con aviso controlado, sin cifras; nunca otra fuente |
| No configurado (`SESSION_SERVICE_URL` vacío) | 503 con aviso "no configurado" |
| No configurado y `SUPERVISOR_INDICATORS_LOCAL_FALLBACK=true` (solo desarrollo, explícito) | Agregados locales anteriores, con el aviso visible "Modo de desarrollo: indicadores parciales…" |

Pruebas:
- `tests/test_reports_remote.py`: servicio caído, 500, 403 o 400 sin usar la fuente local; sin configurar y sin la variable; con la variable.
- `scripts/e2e_reports.py`, paso 18: detiene el contenedor `session` y comprueba que Indicadores y Reportes devuelvan 503 sin cifras, y que se recuperen al reiniciarlo.

### 6.3 Reglas del faltante (verificadas, sin cambios)

| Regla | Dónde | Pruebas |
| :--- | :--- | :--- |
| Si el total final de YOLO no coincide con la referencia (inicial + incorporaciones confirmadas), no se avanza a la validación humana: queda `close_blocked` y la sesión sigue en curso. Un POST directo a la validación también se rechaza (409) | `rf_session.finish_vision_counting`, `vision_final_state`, `submit_manual_close` (`close_path = vision_validation`) | `test_initial_15_final_14_blocks_validation`, `test_final_16_against_adjusted_reference_17_is_blocked`, `test_addition_after_final_requires_a_new_final_capture`, `test_retry_after_14_of_15_uses_only_the_new_capture` |
| Si los totales coinciden, se revisan las clasificaciones | `rf_session` (etapa `final`) | `test_initial_15_final_15_allows_validation`, `test_matching_total_with_wrong_classification_goes_to_human_review`, `test_initial_15_plus_2_confirmed_then_final_17` |
| Un faltante físico descubierto en la revisión humana se registra como discrepancia y escala: `correction_required`, aviso `discrepancy_pending` al Supervisor | `rf_session.submit_manual_close` | `test_missing_instrument_found_in_review_escalates`, `test_human_total_below_initial_escalates_even_if_families_match`, `test_manual_report_cannot_hide_a_known_physical_shortage` |
| El Supervisor no confirma el cierre seguro con una discrepancia abierta ni fuera de `awaiting_spd_review`. **Un faltante físico nunca es cierre seguro** (ver §7) | SessionService `session_review` y portal `rf_session` | `test_open_discrepancy_blocks_close`, `test_wrong_status_is_409[...]`, `tests/test_physical_shortage.py` (10 casos) |

El caso E2E de la etapa 12 (`scripts/e2e_reports.py`, pasos 6–8) cumple esas reglas:
1. el total final de YOLO coincide con la referencia (`/detection-summary`);
2. la validación humana declara un faltante y la sesión pasa a `correction_required` con su discrepancia;
3. desde la verificación del cierre seguro (§7), una nota ya no basta: el Supervisor registra la pieza como perdida y cierra como faltante no conciliado (`safe_close_confirmed = false`).

## 7. Verificación del cierre seguro (faltante físico)

### 7.1 Reglas que existían

| Resultado | Efecto | ¿Constancia física? |
| :--- | :--- | :--- |
| `resolved` | Solo agrega la nota a la discrepancia | No |
| `lost` | Una pieza de la familia (o la indicada) pasa a `lost` y sale del inventario; si no hay ninguna en servicio, se resolvía sin efecto | Registra la pérdida, no la recuperación |
| `recovered` | Una pieza `lost` de la familia vuelve a `available` (no necesariamente la que faltó en esta sesión) | No de esta pieza |

El cierre exigía `awaiting_spd_review`, cero discrepancias abiertas, `material_recovered = true` y, tras una contingencia, `contingency_reviewed`. Después escribía **siempre** `safe_close_confirmed = true`.

**Vulnerabilidad confirmada.** Un faltante físico no localizado podía resolverse con `resolved` y una nota, por ejemplo "Found in tray", y la sesión quedaba registrada como cierre seguro. La pieza seguía disponible en inventario aunque físicamente faltara. Una prueba existente del portal (`test_multitenant::test_same_tenant_full_flow`) hacía exactamente eso.

### 7.2 Clasificación frente a faltante físico

- **Error de clasificación.** YOLO asigna mal una familia y el humano lo corrige con el total igual a la referencia. No genera discrepancia; queda como corrección humana.
- **Faltante físico confirmado.** Discrepancia con motivo `shortage`: el conteo humano de una familia es menor que lo esperado, o el total es menor que la referencia (sin familia).
- **Pieza de más.** `surplus` no es un faltante y conserva la resolución por nota.

### 7.3 Regla nueva

Se aplica igual en SessionService y en la implementación local de desarrollo del portal:

1. **Resolución.** Un faltante físico no se resuelve con `resolved`: 409 `physical_shortage_needs_piece_outcome`, sin cambios. Se usa `lost` (la pieza sale del inventario, con evento de ciclo y auditoría) o `recovered`.
   - Si `lost` no encuentra pieza en servicio, la descripción lo dice.
2. **Cierre.** El sistema no tiene información para documentar una conciliación segura:
   - no registra qué pieza concreta entró a la cirugía (programar no reserva piezas);
   - no hay un reconteo después del reporte del Operador.

   Por eso una sesión con faltante físico **nunca** queda como cierre seguro. El cierre exige el reconocimiento explícito `unreconciled_shortage_acknowledged`; sin él, 409 `unreconciled_physical_shortage`. Una confirmación genérica, como "material recuperado" o "contingencia revisada", no basta. El evento `session_close` queda con:
   - `safe_close_confirmed = false`;
   - `unreconciled_physical_shortages = [ids]`.
3. **Trazabilidad.** Las incidencias y las decisiones quedan registradas: la descripción de la discrepancia con la decisión y la nota del Supervisor, `discrepancy.resolve` y `session.spd_close` en `access_audit`, y el evento de ciclo `lost` de la pieza.
4. **Operador.** Su flujo no cambia: termina su participación (`correction_required`) y el caso pasa al Supervisor.
5. **Pantallas RF.**
   - **Revisión:** en un faltante físico no se ofrece "solo notas", y se explica que queda como incidente no conciliado.
   - **Cierre:** si hay faltantes físicos, se muestra una advertencia y la casilla obligatoria de reconocimiento.

### 7.4 Limitación (pendiente)

Hoy no se puede documentar que un instrumento faltante **apareció**. Haría falta una de estas dos cosas:
- registrar la identidad de cada pieza que entra a la cirugía (reservas por pieza al programar);
- un reconteo físico posterior, es decir, una nueva captura de la charola que el sistema mida.

Mientras no exista, el faltante queda como incidencia no conciliada.

Además, las sesiones cerradas **antes** de este cambio con un faltante resuelto por nota conservan `safe_close_confirmed = true` en su evento histórico. No se reescribió el historial.
