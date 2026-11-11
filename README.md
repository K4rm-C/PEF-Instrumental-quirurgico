# PEF — Conteo y trazabilidad de instrumental quirúrgico

Proyecto académico para apoyar el **conteo humano** mediante visión por computadora, sesiones controladas, revisión de discrepancias y evidencias. **No está validado para uso clínico.**

> **Estado del código:** nueve servicios desplegables cubren **20 dominios funcionales**, no 20 contenedores independientes. Consulte [`docs/MS12_DOMINIOS_FUNCIONALES.md`](docs/MS12_DOMINIOS_FUNCIONALES.md). Las pruebas históricas y sus resultados están documentados en las guías; no se vuelven a ejecutar por el hecho de descargar este repositorio.

## Componentes

- **Portal:** `apps/BackendWebFlask` (puerto 5000); Administrador y Operador ejecutan su lógica en el monolito, el Supervisor consume microservicios.
- **Microservicios:** Auth (5001), VisionWorker (5002), Catalog (5003), Session (5004), Evidence (5005), Audit (5006), Notifications (5007), Monitoring (5008) e Integration (5009).
- **Infraestructura:** PostgreSQL, Redis, MongoDB y Garage S3; workers de notificaciones, monitoreo y entregas; nginx Gateway (perfil opcional `gateway`, HTTP local 8080).
- **Android:** cliente Kotlin/Jetpack Compose en [`apps/PEF-Android/`](apps/PEF-Android/), con proyecto Gradle independiente; consume las APIs publicadas por el Gateway vía Retrofit. Véase su [README](apps/PEF-Android/README.md).
- **Legacy:** material histórico deliberadamente conservado.

## Requisitos

- **Docker Desktop** (Windows) o Docker Engine + Docker Compose v2 (Linux/macOS), en ejecución y con recursos suficientes.
- **Python 3** para generar la configuración local (script sin dependencias externas).
- **Pesos YOLO `best.pt`**, proporcionados por el equipo, solo si se necesita inferencia de visión real. No se incluyen por tamaño/licencias y no deben subirse a GitHub.
- Puertos locales sin conflictos; consulte `docker-compose.yml` y `.env.example`.

## Primer arranque con Docker Compose

Abra PowerShell o una terminal **en esta carpeta** (`PEF-Instrumental-quirurgico`):

```powershell
python scripts/prepare_local.py
# Colocar manualmente best.pt en apps/VisionWorker/weights/best.pt (para inferencia)
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

- Portal: **http://127.0.0.1:5000** (use este mismo host para la autenticación del portal).
- Para el API Gateway que consumirá Android:

```powershell
docker compose --profile gateway up -d gateway
docker compose --profile gateway ps
```

- Gateway HTTP de **desarrollo local**: **http://127.0.0.1:8080** (no exponer a internet).
- `db-migrate` es un contenedor de ejecución única: **salida 0 es el resultado esperado**; actualiza migraciones 004–019. En una base nueva, PostgreSQL aplica también el esquema y las semillas al inicializarse.
- Las cuentas de demostración provienen de `data/seeds/003_seed_demo.sql` y solo deben utilizarse en entornos de prueba; revise sus credenciales y cámbielas antes de usar cualquier información real.
- `prepare_local.py` crea `.env` e `infra/garage.toml` con claves generadas aleatoriamente. **No vuelve a generarlas si ya existen**: hacerlo manualmente con volúmenes persistentes puede inutilizar accesos.
- **Sin `best.pt` no se puede demostrar inferencia YOLO real.** El nombre de modelo semilla por sí solo no implica pesos disponibles.

### Operación habitual

```powershell
docker compose ps
docker compose logs -f web
docker compose logs -f vision
docker compose --profile gateway logs -f gateway
docker compose stop
# Para arrancar de nuevo:
docker compose up -d
```

**No ejecute `docker compose down -v` ni `docker volume prune`: eliminan datos persistidos.** `stop` conserva contenedores y volúmenes; `down` conserva volúmenes si no se usa `-v`.

## App Android

Abra `apps/PEF-Android` en **Android Studio** (archivo `settings.gradle.kts` en la raíz). Requiere el SDK/AVD adecuado y sincronización Gradle. El emulador puede acceder al Gateway local mediante `10.0.2.2`, o mediante `adb reverse` (recomendado para las URL firmadas cuando se utiliza el host `127.0.0.1`).

El **backend firma las URL S3 incluyendo el host**. Para ver evidencias desde Android se necesita acceso al host y puerto firmados, por ejemplo `adb reverse tcp:3900 tcp:3900` con `S3_PUBLIC_ENDPOINT=http://127.0.0.1:3900`, además del reverse del Gateway si se usa `127.0.0.1`. La propuesta de enrutar evidencias firmadas por nginx está en `apps/PEF-Android/docs/backend-proposal/` y **no está aplicada automáticamente al backend**. Revise la sección 3 del [README Android](apps/PEF-Android/README.md).

## Pruebas y demostración

- [`GUIA_PUBLICACION_GITHUB.md`](GUIA_PUBLICACION_GITHUB.md): cómo incorporar este paquete en el clon de GitHub existente sin perder historial.
- [`GUIA_ARRANQUE_DEMO.md`](GUIA_ARRANQUE_DEMO.md): arranque y guion de los tres roles.
- [`GUIA_DOCKER_INTEGRAL.md`](GUIA_DOCKER_INTEGRAL.md): detalles de Docker, volúmenes, puertos, servicios y pruebas.
- [`GUIA_PRUEBAS.md`](GUIA_PRUEBAS.md): suites y ejecución de pruebas.
- [`docs/MS12_DOMINIOS_FUNCIONALES.md`](docs/MS12_DOMINIOS_FUNCIONALES.md): 20 dominios y puertos propuestos frente a los nueve servicios reales.
- [`docs/DECISIONES_PENDIENTES.md`](docs/DECISIONES_PENDIENTES.md): reglas D1–D8 aún sujetas a decisiones funcionales o externas.
- [`GUIA_API_GATEWAY.md`](GUIA_API_GATEWAY.md): restricciones y rutas publicadas por nginx.
- [`GUIA_TRACKING.md`](GUIA_TRACKING.md): límites y validación del seguimiento temporal.

Las pruebas aisladas E2E pueden levantar su propia pila Compose y modificar datos sintéticos; no deben ejecutarse sobre volúmenes importantes sin revisar la guía y los scripts. Los videos y modelos locales de ejecución se excluyeron deliberadamente del paquete público.

## Reglas y límites

El conteo final de instrumentos físicos debe coincidir con la **referencia vigente** (inicial más incorporaciones confirmadas). Una diferencia bloquea la validación y exige recaptura o el flujo de escalamiento autorizado; la validación humana puede corregir **clasificaciones**, pero no alterar la cantidad física detectada. El tracking no cuenta instrumentos de forma autónoma.

**Pendientes de validación externa:** cámaras reales, pesos definitivos y métricas verificadas del modelo, integración con HIS real, y decisiones clínicas/de privacidad D1–D8. No afirmar aptitud clínica a partir de pruebas sintéticas o simuladas.

## Publicar en GitHub

Esta carpeta **no contiene el historial `.git` en el ZIP preparado**. Si ya tiene un repositorio Git, copie el contenido depurado a su clon sin borrar `Legacy` y revise `git status`; si parte de cero, inicialice Git. No versionar `.env`, archivos `.pt`, datos reales, certificados, claves, logs, artefactos Gradle ni evidencia sensible. La app Android ahora está incluida en **`apps/PEF-Android/`** como proyecto Gradle independiente, dentro del mismo repositorio Git. Abra esa subcarpeta directamente en Android Studio. **No inicialice Git dentro de `apps/PEF-Android/`**.
