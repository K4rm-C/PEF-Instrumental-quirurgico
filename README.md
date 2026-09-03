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
| Carlos Ignacio Huerta Carrizales | - |
| Juan Hermilo Reyes Pérez | - |

**Asesor:** Dr. Raúl Morales Salcedo

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
