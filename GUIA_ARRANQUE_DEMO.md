# PEF — Arranque local con Docker Compose y guion de demostración

Entorno principal: **Docker Compose local**. La VM de Google Cloud es una alternativa futura (`infra/vm/`, `GUIA_DESPLIEGUE_GCP_CENTOS10.md`).

## 1. Qué se levanta

| Grupo | Contenedores |
| :--- | :--- |
| Datos | `postgres` (16), `redis` (AOF: las revocaciones sobreviven a un reinicio), `mongo`, `garage` (S3) |
| Esquema | `db-migrate` (de una sola ejecución, MS-11): aplica 004–019 idempotentes a una base existente antes de arrancar cualquier servicio. Una base nueva recibe 001, 002, 003 y 004–019 por `initdb` |
| Nueve servicios | `auth`, `catalog`, `session`, `evidence`, `audit`, `notifications`, `monitoring`, `integration`, `vision` |
| Workers | `notifications-worker`, `monitoring-collector`, `integration-worker` |
| Portal | `web`: Administración y Operador (lógica en el monolito); Supervisor, que usa los microservicios |
| Gateway | `gateway` (perfil `gateway`): nginx. En HTTP de desarrollo solo sirve el API; con `docker-compose.gateway-tls.yml` publica portal y API sobre TLS |

## 2. Primer arranque

```powershell
python scripts/prepare_local.py           # crea .env e infra/garage.toml con secretos aleatorios (ignorados por git)
# Pesos provisionales (nunca en el repositorio): copiar best.pt a apps/VisionWorker/weights/best.pt
docker compose up -d --build              # db-migrate corre primero; luego los servicios
docker compose ps                         # todo "healthy" (db-migrate: "exited (0)")
```

- Portal: `http://localhost:5000`. Usuarios de demostración del seed 003, solo para demostraciones y nunca para producción: `admin@instrumed.com`, `supervisor@instrumed.com`, `operator@instrumed.com`. Las contraseñas están en `data/seeds/003_seed_demo.sql`.
- API Gateway (opcional): `docker compose --profile gateway up -d gateway`, en `http://127.0.0.1:8080`.
- **Nunca** `docker compose down -v` si se quieren conservar los datos: borra los volúmenes.

## 3. Pila aislada de pruebas integrales (MS-11)

```powershell
python scripts/compose_ms11.py up          # proyecto pef_ms11, puertos 26xxx, Gateway TLS (portal.pef.test / api.pef.test)
python scripts/e2e_ms11_compose.py         # E2E de los tres roles sobre los contenedores reales
python scripts/compose_ms11.py down        # conserva los volúmenes (no hay opción para borrarlos)
```

Configuración generada en `.e2e/ms11_compose/`, ignorada por git: secretos aleatorios, `garage.toml`, certificado de prueba autofirmado y un *override* de prueba con `best.pt` en solo lectura y un servidor **HAPI FHIR de pruebas** local. No toca la pila principal ni otros proyectos.

## 4. Guion de demostración (unos 20 minutos)

1. **Administrador**
   - Tablero.
   - Modelos: subir `best.pt` (hash SHA-256 verificado), mapear clases, activar. En **Evaluaciones** se muestra que PEF no reporta precisión sin un registro de evaluación.
   - Configuración: una cámara de la estación (o una fuente **simulada**, que se marca como tal) con su ROI.
   - Clientes máquina: crear uno; el secreto se muestra una sola vez.
2. **Supervisor**: programar una sesión (kit "Video Demo Kit 1", estación, médico y aviso de privacidad).
3. **Operador**:
   - Iniciar la sesión y elegir la cámara (VisionWorker recibe su ROI, resolución y FPS).
   - Subir `.e2e/videos/ms031_one_removed.avi` y registrar el conteo inicial.
   - Ejecutar YOLO (el *tracking* asigna IDs estables).
   - El conteo final es distinto y **se rechaza**. Mostrar el formulario de escalamiento.
   - Subir `ms031_masked.avi`: la recaptura es igual a la referencia y se acepta.
   - La validación humana no deja cambiar el total.
4. **Supervisor**:
   - Detalle de la sesión: evidencias reales de Garage; en la línea de tiempo, el resumen del *tracking* ("no es un conteo").
   - Resolver discrepancias y cerrar (el sistema pide solo los reconocimientos que aplican).
   - Notificaciones, reportes (CSV/PDF), salud de servicios y estaciones, auditoría.
5. **Resiliencia** (opcional):
   - `docker compose stop catalog`: el Gateway responde 502, el colector registra la caída y el Administrador recibe una alerta de componente.
   - `docker compose start catalog`: el componente se marca recuperado.
6. **Integración**: sincronización incremental con el cliente máquina; entrega FHIR al HAPI **de pruebas** (solo en la pila aislada).

## 5. Qué no demuestra esta demostración

- No valida hardware físico: una fuente simulada no es una cámara.
- No usa el modelo definitivo: `best.pt` es provisional.
- No hay integración con un HIS real ni un mapeo FHIR aprobado (D5).
- No incluye decisiones funcionales pendientes: D1 a D8 en `docs/DECISIONES_PENDIENTES.md`.
