# PEF — Conteo y trazabilidad de instrumental quirúrgico

Aplicación para administrar sesiones de conteo de instrumental, apoyar el recuento mediante visión por computadora (YOLO), registrar validaciones humanas y mantener evidencia, auditoría y seguimiento de discrepancias.

## Arquitectura

- **Portal web:** `apps/BackendWebFlask/` (Flask, Jinja, vistas de Operador y Supervisor y administración integrada en el monolito).
- **Servicios:** `apps/BackendAuthService/`, `CatalogService/`, `SessionService/`, `EvidenceService/`, `AuditService/`, `NotificationsService/` y `MonitoringService/`.
- **Detección:** `apps/VisionWorker/` (modelo YOLO externo; no se publica el peso entrenado).
- **Persistencia e infraestructura:** PostgreSQL, Redis, MongoDB, Garage (S3 compatible), Docker Compose.
- **Datos:** esquemas SQL, migraciones y semillas en `data/`.
- **Calidad:** pruebas de componentes y de extremo a extremo en `apps/*/tests/` y `scripts/`.

## Arranque local

**Requisitos:** Docker y Docker Compose; Python 3.12+ para generar la configuración (el proyecto también ofrece scripts de arranque para Linux y Windows). Los archivos de configuración privada no están incluidos en el repositorio.

1. Clona el repositorio y entra en su carpeta.
2. Genera las credenciales y la configuración de Garage exclusivamente en tu equipo:

   ```bash
   python scripts/prepare_local.py
   ```

   Esto crea `.env` e `infra/garage.toml` a partir de plantillas, con claves aleatorias. **No se publican en Git.** Si ya tienes un entorno en uso, no regeneres sus credenciales sin gestionar antes sus datos persistentes.
3. Coloca tu modelo entrenado en `apps/VisionWorker/weights/best.pt`. Este archivo no forma parte de GitHub. Sin los pesos, el servicio de visión puede iniciar, pero **no podrá ejecutar inferencia real**.
4. Inicia la plataforma:

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

5. Abre `http://127.0.0.1:5000` en tu equipo. Consulta los registros con `docker compose logs -f web`.

**Importante:** el archivo `.env.example` tiene marcadores de configuración, no contraseñas para producción. `infra/garage.toml.example` también es una plantilla sin secretos utilizables. El arranque con Docker no equivale a una validación funcional integral; ejecuta las pruebas apropiadas antes de desplegar. El stack Docker está orientado a desarrollo local: revisa puertos expuestos, CORS, HTTPS, cookies y secretos antes de usarlo en otra red.

## Documentación técnica

| Área | Guía |
| --- | --- |
| Puesta en marcha y Docker | [GUIA_DOCKER_INTEGRAL.md](GUIA_DOCKER_INTEGRAL.md) |
| Portal y administración | [GUIA_ADMIN_MONOLITO.md](GUIA_ADMIN_MONOLITO.md) |
| Identidad / JWT | [GUIA_AUTH.md](GUIA_AUTH.md) |
| Catálogos | [GUIA_CATALOG.md](GUIA_CATALOG.md) |
| Sesiones y conteo | [GUIA_SESSION.md](GUIA_SESSION.md) |
| Captura de video y visión | [GUIA_CAPTURA_VIDEO.md](GUIA_CAPTURA_VIDEO.md) |
| MongoDB y telemetría | [GUIA_MONGODB.md](GUIA_MONGODB.md) |
| Evidencias y trazabilidad / auditoría | [GUIA_AUDIT.md](GUIA_AUDIT.md) |
| Notificaciones | [GUIA_NOTIFICATIONS.md](GUIA_NOTIFICATIONS.md) |
| Monitoreo | [GUIA_MONITORING.md](GUIA_MONITORING.md) |
| Reportes e indicadores | [GUIA_REPORTES.md](GUIA_REPORTES.md) |
| Pruebas reproducibles | [GUIA_PRUEBAS.md](GUIA_PRUEBAS.md) |
| Contratos y normas de datos | [data/DataStandards/](data/DataStandards/) |
| Arquitectura y diagramas | [docs/](docs/) |

## Reglas funcionales de conteo

Al cierre de una sesión, la última captura estable debe coincidir en **cantidad total de instrumentos** con la referencia vigente (conteo inicial más incorporaciones confirmadas por Supervisor). Si la captura final detecta menos, el Operador debe repetir la captura antes de la validación humana. La validación revisa clasificación y trazabilidad, pero no autoriza omitir instrumentos físicos faltantes.

## Contenido no publicado

El repositorio excluye credenciales (`.env`, `infra/garage.toml`), datos y logs de ejecución, videos, archivos comprimidos de evidencias y pesos del modelo (`*.pt`). Las imágenes de referencia de UI y la documentación técnica se mantienen para facilitar cambios posteriores.

Proyecto académico en evolución; no destinado a uso clínico real sin validación, seguridad y autorizaciones pertinentes.
