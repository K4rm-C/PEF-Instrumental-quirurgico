> **HISTÓRICO (20-oct-2026).** Este documento describe el ZIP integrado anterior, que todavía usaba AdminService. **No refleja la arquitectura actual.** Consultar [GUIA_ADMIN_MONOLITO.md](GUIA_ADMIN_MONOLITO.md).

# PEF - Integración inicial de ambos ZIP (20-oct-2026)

## Decisión sobre Administración

El **AdminService más reciente implementa más funciones** que las antiguas rutas de Administración del monolito. Las rutas antiguas de modelos de visión, configuración y auditoría no tenían persistencia completa. No se eliminaron los avances recientes.

Por seguridad, este paquete **mantiene la interfaz de Administración RF dentro de BackendWebFlask**, pero las operaciones de Administración **siguen ejecutándose en AdminService mediante HTTP**. No se afirma que la lógica de Admin esté migrada al monolito. El paso siguiente es redistribuir esa lógica hacia Auth, Catalog, Evidence y otros servicios oficiales, sin reemplazarla por las rutas antiguas incompletas.

Las rutas antiguas de Admin pueden consultarse como referencia en `docs/integracion/admin_monolito_anterior.txt`; NO están registradas ni se ejecutan.

## Criterios para la fusión

- Base: ZIP `PEF-Instrumental-quirurgico-main(3)` para conservar Auth v1 JSON/XML, CatalogService, SessionService, EvidenceService y restricciones multiinstitución de Operador/Supervisor.
- Integrado: del ZIP `PEF-Instrumental-quirurgico-Admin` se incorporan AdminService, cliente/presentadores/rutas de Admin, páginas recientes de Admin, traducciones, configuraciones y pruebas exclusivas.
- El blueprint `web` registra exactamente **un juego de rutas** de Administración (el nuevo). Las antiguas rutas de Admin se archivan sin activarse.
- El AdminService se publicó en puerto **5006**, eliminando la colisión con CatalogService (5003). SessionService es 5004; EvidenceService es 5005.
- Se conserva la migración `data/migrations/004_media_asset_session.sql` del ZIP anterior.
- Los documentos de estándares DS03/DS04/DS05/DS09 de la rama base se mantienen y las versiones diferentes de Administración quedan en `docs/integracion/version_admin/` para conciliarlas.
- Se excluyeron `.env`, `.venv`, entornos de desarrollo, cachés y archivos pesados. **Configurar credenciales reales localmente antes de iniciar.**

## Puesta en marcha (estado transitorio)

1. Instalar Docker Desktop y Python 3.12 o superior.
2. Copiar `.env.example` a `.env` en la raíz y **cambiar TODAS las credenciales marcadas CAMBIAR_**; mantener los mismos JWT_SECRET_KEY entre servicios. Para las versiones locales, las URLs `127.0.0.1` funcionan contra puertos publicados. No subir `.env` a Git.
3. Ejecutar `docker compose config` para comprobar configuración y `docker compose up -d --build` para iniciar PostgreSQL, Redis, MongoDB, Garage, Catalog, Session, Evidence y Admin.
4. Primera inicialización de PostgreSQL: `docker compose` ejecuta los SQL de `data/migrations`/`data/seeds` **solo si el volumen está vacío**. Si se reutiliza una base existente, revisar las migraciones en la documentación original.
5. Iniciar **BackendAuthService**, **BackendWebFlask** y **VisionWorker** localmente (en tres terminales), instalando antes sus `requirements.txt`. Copiar el `.env` de la raíz a las carpetas de los servicios que se ejecuten localmente, o ejecutar `start-services.sh`/`start-services.ps1` para preparar el entorno. Comprobar dependencias adicionales del modelo YOLO y sus pesos en `apps/VisionWorker/weights/best.pt`.
6. Verificar `http://127.0.0.1:5006/health`, `http://127.0.0.1:5003/health/ready`, `http://127.0.0.1:5004/health/ready`, `http://127.0.0.1:5005/health/ready`, y posteriormente la interfaz web en `http://127.0.0.1:5000`.

## Puertos

| Componente | Puerto local | Ejecución en esta integración |
| --- | ---: | --- |
| Web/Operador/Supervisor/Admin RF | 5000 | Python local |
| Auth/Identity | 5001 | Python local |
| VisionWorker | 5002 | Python local (requiere pesos) |
| Catalog | 5003 | Docker |
| Session | 5004 | Docker |
| Evidence | 5005 | Docker |
| AdminService (transitorio) | 5006 | Docker |
| PostgreSQL | 5433 | Docker |
| Redis | 6379 | Docker |
| MongoDB | 27017 | Docker |

## Validación realizada y alcance

Se verificaron estructura de archivos, sintaxis de Python, fusión de variables/Compose, unicidad de las rutas Admin y preservación estática de reglas multiinstitución. **No se han realizado pruebas integrales contra Postgres/Redis/Mongo/Garage ni recorrido manual de los tres roles en este entorno.** No se debe afirmar funcionalidad 100% comprobada hasta ejecutar esas pruebas.

## Pendientes de los requisitos del profesor

- Redistribuir AdminService a los servicios oficiales, incluidas transacciones y ownership de datos.
- Integrar flujos web de Supervisor con SessionService sin comprometer Operador.
- Implementar MongoDB (DS06, `checkpoint_frame`, `session_telemetry`) con inserción/consultas/updates/agregaciones/índices reales.
- Dockerizar también Auth, Web y VisionWorker; completar XML, XSD, OpenAPI/Swagger, documentación individual, pruebas unitarias e integración y evidencias HTTP reales.
- Conciliar las variantes de DS03/DS04/DS05/DS09 y el esquema físico.

## Seguridad

No se incluyen credenciales `.env` activas. Al compartir estos ZIP, considerar rotación de claves si algún archivo original del equipo contenía valores sensibles reutilizados.
