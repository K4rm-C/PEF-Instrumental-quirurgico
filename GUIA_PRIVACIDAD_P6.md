# PEF — P6: avisos de privacidad por institución (decisión pendiente)

## 1. Estado

**Pendiente de decisión funcional.** No se modificó el esquema, el alcance de los avisos existentes, la aceptación de avisos ni ningún requisito del Operador.

## 2. Por qué no se pudo decidir con los RF

| Fuente | Dice | Consecuencia |
| :--- | :--- | :--- |
| RF-AD-04 | "PDF/HTML versionado **del hospital**"; el Administrador sube, versiona y activa | Sugiere avisos por hospital |
| RF-AD-04 / RF-SP-02 | El SPD elige una versión activa y la liga a **una** sesión; varias activas permitidas | No dice si un aviso puede servir a varias instituciones |
| Decisión D-2 (2026-11-01) | Límite documentado: avisos, familias, procedimientos y modelos **no** tienen `institution_id` y son compartidos | Lo vigente es global |
| Esquema | `privacy_notice_version` sin `institution_id`; `session_processing_agreement` con FK RESTRICT | Los acuerdos históricos dependen de la fila del aviso |

Los RF no definen qué pasa con los avisos ya publicados (¿de qué institución son?) ni si un aviso compartido debe duplicarse. Decidirlo implicaría inventar una regla de privacidad.

## 3. Lo que sí se entregó (solo lectura)

`scripts/p6_privacy_usage.py` (`--json` opcional; transacción `READ ONLY`): por cada aviso, sesiones acordadas por institución, usuarios y fechas; avisos usados por más de una institución; avisos nunca usados. Es el insumo para decidir. En la base de pruebas: ningún aviso está compartido entre instituciones.

## 4. Propuesta lista para cuando se decida (opción b, compatible)

1. `ALTER TABLE privacy_notice_version ADD COLUMN institution_id UUID NULL` (FK a `institution`), sin tocar filas existentes: **NULL = global** (los documentos históricos y sus acuerdos quedan intactos).
2. Avisos nuevos: con la institución del Administrador que los publica.
3. Lectura (Admin, SessionService al programar/acordar): avisos de la institución **más** los globales activos.
4. Transición: el equipo decide, con el informe del punto 3, qué avisos globales se asignan a una institución (solo los usados por una sola institución o nunca usados); un aviso usado por varias instituciones queda global.
5. Pruebas: aislamiento en Admin (RF-AD-04) y SessionService (RF-SP-02), acuerdos históricos sin cambios, migración idempotente.

Opción (a): mantenerlo global y documentarlo como restricción del despliegue (una institución por despliegue o avisos comunes).

## 5. MS-09: solución preparada y opciones de consentimiento

**Preparado (no aplicado):** `docs/propuestas/p6_avisos_institucionales.sql`: columna `institution_id` nula (NULL = global, los documentos y acuerdos históricos no cambian), índice y un *trigger* que impediría usar en una sesión el aviso de otra institución. No está en `data/migrations` ni montado en Compose: requiere la decisión.

**Lo que no se puede derivar de los RF (no implementado):** qué aviso debe aceptarse cuando existan a la vez avisos globales e institucionales activos.

| Opción | Regla | Impacto |
| :--- | :--- | :--- |
| 1. Global + institucionales | El Supervisor elige entre los activos de su institución y los globales (como hoy, más los suyos) | Sin cambio para el Operador; mínima migración; permite mantener un aviso común |
| 2. Institucional tiene prioridad | Si la institución tiene uno activo, solo ese; si no, los globales | Cambia lo que ve el Supervisor; requiere definir qué pasa con sesiones programadas con un global |
| 3. Solo institucionales | Los globales quedan solo como históricos; cada institución publica el suyo | Requiere que cada institución publique antes de programar; riesgo de bloquear la programación |

En ninguna opción se toca la aceptación del Operador ni los acuerdos ya registrados. Recomendación técnica: opción 1 (compatible y reversible), pendiente de aprobación de privacidad.
