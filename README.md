# PEF-Instrumental-quirurgico
PEF UDEM: conteo y trazabilidad asistidos por visión de instrumental quirúrgico en charola cenital (prototipo web + YOLO).

# Conteo y trazabilidad de instrumental quirúrgico (charola cenital)

Proyecto de Evaluación Final (PEF) — Ingeniería en Tecnologías Computacionales, Universidad de Monterrey, en colaboración con Linnaeus University.

Sistema asistido por **visión computacional** para detectar y contar instrumental metálico en vista cenital, con **validación humana**, sesiones autenticadas y registro auditable. Prototipo / MVP en desarrollo (no es un dispositivo médico certificado).

## Equipo

| Integrante | Matrícula |
| :--- | :--- |
| Benjamin Charles Legorreta | 599860 |
| Pedro Elidio Sora Gonzalez | 596630 |
| Angel Uriel Muñoz Moreno | 604386 |

**Asesor:** Dr. Raúl Morales Salcedo

## Alcance del MVP

- Detección por **familias** de instrumental (cajas YOLO) sobre charola fija
- Cliente **web** (React + Vite + Tailwind) + backend **Python** (FastAPI, WebSocket, JWT)
- Inferencia en **servidor local** (PyTorch / Ultralytics); no en el navegador
- Persistencia: PostgreSQL + Redis; despliegue con Docker Compose
- Metodología de trabajo: **Crystal Clear** + investigación aplicada

## Stack previsto

- **Front:** React, Vite, Tailwind CSS  
- **Back:** Python, FastAPI, WebSocket, JWT  
- **ML:** PyTorch, Ultralytics YOLO  
- **Datos:** PostgreSQL, Redis, Docker Compose  

## Estado

Documentación y planificación del anteproyecto. El código de aplicación se incorporará conforme al cronograma (dataset → entrenamiento → integración).
