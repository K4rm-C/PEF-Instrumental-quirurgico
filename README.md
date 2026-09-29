# PEF-Instrumental-quirurgico
PEF UDEM: conteo y trazabilidad asistidos por visión de instrumental quirúrgico en charola cenital (prototipo web + YOLO).

# Conteo y Trazabilidad Asistidos por Visión Computacional

Proyecto de Evaluación Final (PEF) — Ingeniería en Tecnologías Computacionales, Universidad de Monterrey, en colaboración con Linnaeus University.

Sistema asistido por visión computacional para detectar y contar instrumental metálico en vista cenital, con validación humana, sesiones autenticadas y registro auditable.

## Equipo

| Integrante | Matrícula |
| :--- | :--- |
| Benjamin Charles Legorreta | 599860 |
| Pedro Elidio Sora Gonzalez | 596630 |
| Angel Uriel Muñoz Moreno | 604386 |

Equipo de apoyo

| Integrante | Matrícula |
| :--- | :--- |
| Luis Carlos Rodriguez Medrano | 606869 |
| Carlos Ignacio Huerta Carrizales | 600291 |
| Juan Hermilo Reyes Pérez | - |

**Asesor:** Dr. Raúl Morales Salcedo

## Estado actual de la implementación

La versión actual del repositorio es un **monolito Flask** (`apps/BackendWebFlask`, plantillas Jinja)
con autenticación separada en `apps/BackendAuthService` y persistencia en PostgreSQL
(`data/migrations/001_init.sql`). El flujo completo (catálogos → sesión → captura → análisis →
discrepancias → validación humana → supervisor → cierre → dashboards/auditoría) está implementado,
pero:

- la inferencia usa el provider temporal **`controlled`** (aún no hay worker YOLO real);
- las pruebas se han ejecutado con SQLite temporal; **falta la validación contra PostgreSQL real**.

El idioma canónico de los datos persistidos (negocio y catálogos) es el **inglés**; la localización de la interfaz se hace en la capa de aplicación.
La interfaz web está disponible en **inglés** (`en`, predeterminado) y **español** (`es`) con Flask-Babel: el selector EN / ES
guarda la elección en `user.ui_preferences.locale` (usuarios autenticados) o en la sesión web (visitantes), y los valores de
catálogo se traducen por su código estable, nunca por el nombre guardado. Detalles, reglas y cómo añadir traducciones:
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) (sección 19b).

Detalle y pendientes: [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).
Las secciones siguientes describen el alcance y el stack **objetivo** del proyecto.

## Alcance del MVP

- Detección por familias de instrumental (cajas YOLO) sobre charola cenital fija
- Cliente web (React + Vite + Tailwind) + backend **Python** (FastAPI, WebSocket, JWT)
- Inferencia en servidor local (PyTorch / Ultralytics)
- Persistencia: PostgreSQL (negocio y auditoría), MongoDB (checkpoints y telemetría), Redis (auth/TTL), GCS (media y pesos del modelo); despliegue con Docker Compose
- Gestión de kits configurables, inventario esperado por sesión y reservas de instrumental individual
- Reglas de discrepancia y cierre: faltantes bloquean cierre hasta resolución explícita
- Contrato de interoperabilidad HL7 FHIR R4 (identificadores `system|value`)
- **TTS** como retroalimentación de apoyo
- Metodología de trabajo: Crystal Clear + investigación aplicada

## Stack previsto

- **Front:** React, Vite, Tailwind CSS
- **Back:** Python, FastAPI, WebSocket, JWT
- **ML:** PyTorch, Ultralytics YOLO
- **Datos:** PostgreSQL, Redis, MongoDB (checkpoints/telemetría), Google Cloud Storage (media/pesos), Docker Compose
