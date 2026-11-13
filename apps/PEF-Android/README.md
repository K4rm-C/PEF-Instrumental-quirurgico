# PEF Android — cliente de trazabilidad de instrumental quirúrgico

Aplicación Android (Kotlin + Jetpack Compose) del PEF. Consume los microservicios **exclusivamente a través del
API Gateway nginx** con Retrofit + OkHttp + Coroutines. El diseño y la navegación originales del equipo se conservan.
Este documento describe la integración ANDROID-01.

> No hay contraseñas, tokens ni datos hospitalarios en este repositorio ni en este documento. Los datos de prueba
> unitaria son sintéticos.

---

## 1. Arquitectura

```
ui (Compose)            views/*, composables/*, ui/screens/SettingsView.kt, ui/components/*
   │  estados UiState (Loading / Success / Empty / Error / NotPermitted; Offline y Unauthorized se derivan del error)
ui/viewmodel            ViewModels (StateFlow) — lógica de presentación pura en core/Models.kt (testeable)
   │
data/repository         SessionRepository, EvidenceRepository, NotificationRepository  → ApiResult<T>
auth                    SessionManager (JWT, refresh con rotación, logout), TokenStore cifrado
preferences             PreferencesRepository (DataStore), LocaleApplier
   │
data/remote             Retrofit (PefApis.kt), OkHttp (interceptores, autenticador), mapeo de errores (ApiResult.kt)
data/model              DTO idénticos a los serializadores del backend
   │
API Gateway nginx  ──►  AuthService · SessionService · EvidenceService · NotificationsService
```

* **Inyección de dependencias**: manual (`AppContainer` + `PefApplication`); la app es pequeña y no justifica Hilt.
* **Navegación**: la misma Navigation3 del equipo (`core/AppNavigator.kt`, `core/Routes.kt`). Las claves de detalle ahora
  llevan el id real (`SessionInProgressDetail(sessionId)`, `OnHoldDetail(sessionId)`, `SessionScheduledDetail(sessionId)`,
  `PersonalDetail(operatorId, name)`); se añadió `Settings`. La pila está protegida por `AuthState`: sin sesión válida
  solo existe `Login`; con sesión, `Login` nunca queda en la pila.
* **Paquetes**

| Paquete | Contenido |
|---|---|
| `auth` | `SessionManager`, `AuthState`, `TokenStore`, `SecureTokenStore` (Android Keystore AES-GCM) |
| `data/model` | DTO de Auth, Session, Evidence y Notifications |
| `data/remote` | Interfaces Retrofit, `NetworkModule`, `AuthInterceptor`, `TokenAuthenticator`, `ApiResult`/`AppError` |
| `data/repository` | Repositorios (solo lectura de sesiones; marcar notificaciones como leídas) |
| `preferences` | `PreferencesRepository` (DataStore), `AppLanguage`, `ThemeMode`, `LocaleApplier` |
| `core` | Rutas, navegación y modelos de presentación derivados (`computeDashboard`, `liveCount`, …) |
| `ui/theme` | Paleta semántica clara/oscura (`PefColors`) y `PefTheme` |
| `ui/components`, `ui/state`, `ui/viewmodel`, `ui/screens` | Componentes de estado, formateo, ViewModels, Ajustes |
| `views`, `composables` | Pantallas originales del equipo, ahora conectadas al backend |

## 2. Dependencias añadidas

| Dependencia | Versión | Motivo |
|---|---|---|
| `com.squareup.retrofit2:retrofit` + `converter-kotlinx-serialization` | 3.0.0 | Cliente HTTP declarativo (requisito) |
| `com.squareup.okhttp3:okhttp` | 4.12.0 | Interceptores, autenticador de refresh, timeouts |
| `org.jetbrains.kotlinx:kotlinx-serialization-json` | 1.9.0 | JSON (el proyecto ya usaba kotlinx-serialization) |
| `org.jetbrains.kotlinx:kotlinx-coroutines-android` | 1.10.2 | Asincronía |
| `androidx.lifecycle:lifecycle-viewmodel-compose`, `lifecycle-runtime-compose` | 2.10.0 | ViewModel y `collectAsStateWithLifecycle` (se corrigió el grupo `android.lifecycle` mal escrito del catálogo) |
| `androidx.datastore:datastore-preferences` | 1.1.7 | Preferencias y almacenamiento del blob cifrado |
| `androidx.appcompat:appcompat` | 1.7.1 | Idioma por aplicación (`AppCompatDelegate.setApplicationLocales`) también en API < 33 |
| Pruebas: `okhttp3:mockwebserver` 4.12.0, `kotlinx-coroutines-test` 1.10.2 | | Pruebas de red aisladas |

No se añadió ninguna biblioteca de imágenes ni de inyección de dependencias.

## 3. Configuración del Gateway, emulador y teléfono

### 3.1 URL base (Retrofit)
La URL base es un `BuildConfig.API_BASE_URL` por tipo de compilación, configurable sin tocar código:

| Propiedad Gradle | Valor por defecto | Uso |
|---|---|---|
| `pef.apiBaseUrl.debug` | `http://10.0.2.2:8080/` | Emulador estándar → Gateway de desarrollo de `docker-compose.yml` |
| `pef.apiBaseUrl.release` | `https://api.pef.invalid/` (marcador) | **Debe** ser HTTPS; la compilación release falla si no lo es |

```bash
./gradlew installDebug -Ppef.apiBaseUrl.debug=http://127.0.0.1:27080/
./gradlew assembleRelease -Ppef.apiBaseUrl.release=https://api.mi-hospital.example/
```
En **Android Studio**: `gradle.properties` del usuario (`~/.gradle/gradle.properties`) con
`pef.apiBaseUrl.debug=http://127.0.0.1:27080/`, o *Settings → Build → Gradle → Command-line options*. La URL activa se ve en
**Ajustes → Acerca de**. Debe terminar en `/`.

### 3.2 Puertos

| Pila | Gateway (API) | Garage S3 (URLs firmadas) | Publicación |
|---|---|---|---|
| Principal `docker-compose.yml` (`--profile gateway`) | `127.0.0.1:${GATEWAY_HTTP_PORT:-8080}` HTTP | `${S3_API_PORT}` (**todas las interfaces**) | dev |
| Aislada ANDROID-02 `pef_android_it` (§9) | `127.0.0.1:27080` HTTP | `127.0.0.1:27900` | todo en loopback |
| Producción `docker-compose.gateway-tls.yml` | `443` HTTPS (+80 → 308) | `${S3_API_PORT}` | ver §3.6 |

Los servicios internos (5001–5009) nunca se usan desde Android: solo el Gateway.

### 3.3 Emulador Android (verificado con AVD `Medium_Phone`, Android 37)
Opción recomendada — `adb reverse` (lo que se usó en la prueba integral):
```bash
adb reverse tcp:27080 tcp:27080            # Gateway (o tcp:8080 en la pila principal)
./gradlew installDebug -Ppef.apiBaseUrl.debug=http://127.0.0.1:27080/
```
El `adb reverse` se pierde cada vez que se reinicia el emulador o adb. Para no repetirlo a mano, configure
`local.properties` (por máquina, no versionado) y el build debug lo hace solo antes de cada compilación, también al
pulsar **Run** en Android Studio (tarea `pefAdbReverse`; si no hay dispositivo conectado solo avisa, no falla):
```properties
pef.apiBaseUrl.debug=http\://127.0.0.1\:27080/
pef.adbReverse=27080                       # varios puertos: 27080,27900
pef.buildDir=C\:/Users/<usuario>/AppData/Local/pef-android-build   # solo si el proyecto está en OneDrive
```
El dispositivo debe estar encendido antes de compilar. Si lo reinicia con la app ya instalada, vuelva a pulsar Run (o
`./gradlew pefAdbReverse`).
Alternativa — `10.0.2.2` (valor por defecto): funciona para la **API**, pero las imágenes de evidencias no, porque el
backend firma la URL para el host de `S3_PUBLIC_ENDPOINT` y **el `Host` forma parte de la firma SigV4** (probado:
la misma URL pedida con otro host → 403). Ver §3.5.

### 3.4 Teléfono físico
* **USB / depuración inalámbrica (recomendado)**: igual que el emulador, `adb reverse tcp:<puerto del Gateway> ...` y
  `pef.apiBaseUrl.debug=http://127.0.0.1:<puerto>/`. El Gateway sigue publicado solo en loopback de la computadora.
* **Wi-Fi sin adb**: el Gateway de desarrollo está ligado a `127.0.0.1` a propósito
  (`infra/gateway/servers/http.conf.template`: "Never publish this listener on a public interface"). No se recomienda
  abrirlo en la LAN; para eso existe la configuración TLS (§3.6). Si aun así se publica en una red de confianza con un
  *override* local no versionado, hay que añadir esa IP a `app/src/debug/res/xml/network_security_config.xml`.

### 3.5 Evidencias y URLs firmadas
Hallazgos verificados contra la pila real (`signed_url` de `GET /api/v1/evidence/{id}`):

| Prueba | Resultado |
|---|---|
| URL firmada válida | 200, JPEG íntegro |
| Firma alterada / sin firma | 403 (Garage) |
| Misma URL con otro host (`localhost` en vez de `127.0.0.1`) | 403 — el host está firmado |
| La app envía el JWT a Garage | No (cliente de imágenes sin Bearer, verificado en pruebas) |

Opciones para que Android alcance la URL firmada **sin exponer Garage públicamente**:

1. **Sin cambios de backend (desarrollo)**: `S3_PUBLIC_ENDPOINT=http://127.0.0.1:<puerto S3>` +
   `adb reverse tcp:<puerto S3> tcp:<puerto S3>`. Garage queda en loopback (en la pila aislada) y el portal web del PC
   también puede abrir la URL.
2. **Por el Gateway nginx (propuesto, probado en la pila aislada)**: una ruta `^~ /<bucket>/` que solo acepta `GET`
   con `X-Amz-Signature`, reenvía ruta y `Host` sin cambios (son parte de la firma), quita `Authorization`/`Cookie`,
   aplica los límites del API y `Cache-Control: private, no-store`; `S3_PUBLIC_ENDPOINT` = URL pública del Gateway.
   Garage sigue validando firma y caducidad (TTL 300 s); el bucket sigue privado. Resultado en la pila aislada: firma
   válida 200; firma alterada, sin firma, otro host, `POST` y listado del bucket → 403; regresión de la API sin cambios;
   el log de acceso no registra la query firmada. Con esto Android solo necesita alcanzar el Gateway (y en producción
   todo viaja por HTTPS 443). **No se aplicó al repositorio del backend**: el parche está en
   `docs/backend-proposal/gateway-presigned-evidence.patch` para que el equipo lo revise. Nota: el *error log* de nginx
   sí incluye la línea de petición de las solicitudes denegadas (p. ej. un `POST`), por lo que una firma podría aparecer
   ahí; caduca en 5 min y es de solo lectura.

La app nunca construye ni reescribe URLs de Garage y la imagen solo vive en memoria.

### 3.6 HTTPS
* Release exige `https://` (comprobación en `app/build.gradle.kts`) y la configuración de red base no permite texto en
  claro. Se usan solo las CA del sistema: **no** hay `TrustManager` propio ni validación desactivada.
* Para producción: `docker compose -f docker-compose.yml -f docker-compose.gateway-tls.yml --profile gateway up -d` con un
  certificado emitido para `GATEWAY_API_HOST` por una CA de confianza del dispositivo, y
  `-Ppef.apiBaseUrl.release=https://<GATEWAY_API_HOST>/`.
* Las pilas de prueba con certificado autofirmado (p. ej. MS-11, `api.pef.test`) **no** funcionan con la app tal cual,
  a propósito: confiar en ese certificado exigiría agregar una CA de prueba solo para debug (`<debug-overrides>`), lo
  cual no se hizo.

### 3.7 Login de prueba
Use las cuentas demo de la semilla `data/seeds/003_seed_demo.sql` del repositorio PEF (supervisor, operador y
administrador `@instrumed.com`). **Las contraseñas no se copian aquí**; están en los comentarios de esa semilla y solo
sirven para entornos de demostración. Roles: `supervisor@…` → `spd_supervisor` (ve toda la institución), `operator@…` →
`station_operator` (solo sus sesiones), `admin@…` → `it_admin` (sin acceso clínico; solo notificaciones).

## 4. Seguridad de red
* `src/main/res/xml/network_security_config.xml` (release y base): **solo HTTPS**, CAs del sistema. La validación TLS
  nunca se desactiva y no hay `TrustManager` personalizado.
* `src/debug/res/xml/network_security_config.xml` (solo debug): HTTP permitido únicamente para `10.0.2.2`,
  `localhost` y `127.0.0.1`. Verificado en los APK: el release no contiene `domain-config` con texto en claro.
* El registro HTTP de depuración (solo `BuildConfig.DEBUG`) incluye método, ruta, código y `X-Correlation-ID`; **nunca**
  cabeceras, cuerpos, JWT ni contraseñas. No se usa `HttpLoggingInterceptor`.
* Cada petición lleva un `X-Correlation-ID` propio; los errores muestran la referencia para soporte.

## 5. Autenticación (AuthService)
Rutas reales publicadas por el Gateway (verificadas en `apps/BackendAuthService/api_v1.py` y `routes.conf.template`):

| Acción | Ruta | Notas |
|---|---|---|
| Login | `POST /api/v1/auth/login` `{email,password}` | Devuelve `access_token`, `refresh_token`, `expires_in`, `user` (roles, ui_preferences) |
| Refresh | `POST /api/v1/auth/refresh` `{refresh_token}` | **Rotación**: el refresh presentado queda revocado y se entrega un par nuevo |
| Logout | `POST /api/v1/auth/logout` Bearer + `{refresh_token}` | 204; revoca access **y** refresh |
| Usuario | `GET /api/v1/auth/me` | Perfil y roles actuales (cambios de rol) |
| Permisos | `GET /api/v1/auth/permissions` | Disponible en `AuthApi` (no usado por ahora) |

* **Revocación**: no existe una ruta de revocación administrativa publicada para clientes (la administración de
  identidad vive en el portal y el Gateway no la publica). La revocación efectiva es el logout; la app detecta
  revocaciones/expiraciones del servidor cuando `/refresh` responde 401.
* **JWT**: `SessionManager` mantiene el par en memoria (para el interceptor) y en `SecureTokenStore`: JSON cifrado con
  AES-256-GCM, clave no exportable en **Android Keystore**, en un DataStore propio (`pef_secure_session`) **excluido de
  los respaldos** (`backup_rules.xml`, `data_extraction_rules.xml`). Separado de las preferencias no sensibles. La
  contraseña nunca se guarda (y se borra del estado tras cada intento). `StoredSession.toString()` no imprime tokens.
* **Refresh**: `TokenAuthenticator` actúa ante un 401, una sola vez por petición. `refreshTokens()` está protegido por
  un `Mutex`: si varias peticiones fallan a la vez, solo una renueva y las demás reutilizan el token nuevo (con la
  rotación del backend, presentar dos veces el mismo refresh revocaría la sesión). El cliente autenticado usa su
  propio `Dispatcher` de OkHttp para evitar un interbloqueo detectado en pruebas (5 peticiones bloqueadas en el
  autenticador agotaban los cupos por host y la llamada de refresh nunca salía).
* Límite del Gateway: login/refresh comparten `10r/m` (burst 5) por IP; por eso no hay refresh proactivo periódico,
  solo ante 401 y al restaurar.
* **Restauración**: al abrir la app se lee la sesión cifrada y se valida con `GET /me` (renovando si hace falta).
  Si el servidor la rechaza → Login con "sesión expirada o revocada". Si no hay conexión → se conserva la sesión local
  en modo *offline* (Ajustes lo indica) y cada pantalla muestra el error de red con reintento.
* **Logout**: confirma, llama a `/logout`; si el access caducó (401) renueva una vez y repite con el refresh nuevo. Pase
  lo que pase borra las credenciales locales; si el servidor no pudo confirmar la revocación, el Login lo indica.
* **Roles**: `station_operator` y `spd_supervisor` tienen acceso clínico (sesiones/evidencias). Otros roles (p. ej.
  `it_admin`) pueden iniciar sesión y ver sus notificaciones; las pantallas clínicas muestran "tu rol no tiene acceso"
  sin llamar al backend, que de todos modos respondería 403.

## 6. Retrofit, repositorios y ViewModels
* `PefApis.kt` declara solo rutas publicadas. JSON (`Accept: application/json`); campos desconocidos se ignoran; un
  tipo incompatible o JSON malformado es `AppError.InvalidResponse`.
* `safeApiCall` convierte cualquier fallo en `ApiResult.Failure(AppError)`: `Network`, `Timeout`, `BadRequest`
  (400/409/410/413/422), `Unauthorized` (401), `Forbidden` (403), `NotFound` (404), `RateLimited` (429), `Server` (5xx),
  `InvalidResponse`, `Unexpected`. Se conserva el `message` y `correlation_id` del sobre DS09.
* Los ViewModels exponen `StateFlow<UiState<T>>`, evitan cargas duplicadas y pulsaciones repetidas, y nunca hacen
  polling permanente: se recarga al entrar a la pantalla o con el botón **Actualizar**.

## 7. DataStore, idioma y tema
* `PreferencesRepository` (archivo `pef_ui_preferences`): `language` (SYSTEM / SPANISH / ENGLISH), `theme`
  (SYSTEM / LIGHT / DARK), `inAppNotifications`, banderas `languageChosen`/`themeChosen` y `schemaVersion` para
  migraciones futuras. Persisten tras cerrar la app.
* **Idioma**: todos los textos visibles están en `res/values/strings.xml` (inglés, idioma por defecto del diseño) y
  `res/values-es/strings.xml` (español), con plurales. `LocaleApplier` usa el idioma por aplicación de Android
  (`AppCompatDelegate.setApplicationLocales`; en API 33+ también aparece en Ajustes del sistema gracias a
  `res/xml/locales_config.xml`). No se traducen identificadores, datos de catálogo ni mensajes del servidor (el mensaje de
  una notificación se muestra tal cual; su **tipo** sí se rotula con una cadena local).
* **Tema**: `PefTheme` con paleta semántica `PefColors` (claro = colores exactos del diseño original; oscuro =
  misma jerarquía con contraste AA). Los nombres de color originales (`Navy`, `TextPrimary`, …) se conservaron como
  propiedades que leen el tema activo, y los `Color(0xFF…)`/`Color.White` fijos de las vistas se sustituyeron por tokens.
  Ajustes ofrece Automático/Claro/Oscuro; se aplica al instante.
* **Sincronización con el servidor**: `GET /me` y el login devuelven `ui_preferences {locale, theme}`; se aplican como
  valor inicial **solo** si el usuario no eligió otra cosa en el teléfono. La escritura (`POST /preferences` de
  AuthService) es una ruta con cookies del portal **no publicada** por el Gateway, así que no se usa (ver §13).
* **Notificaciones**: la preferencia es de interfaz (insignia de no leídas y consulta del contador). No es un permiso push.

## 8. Pruebas
```bash
./gradlew testDebugUnitTest            # 60 pruebas JVM con MockWebServer + 3 de integración (se omiten sin PEF_IT_*)
./gradlew lintDebug
./gradlew assembleDebug assembleRelease assembleDebugAndroidTest
./gradlew connectedDebugAndroidTest    # emulador/dispositivo (desinstala la app al terminar: se pierden sus datos)
```
| Clase | Cubre |
|---|---|
| `auth/SessionManagerTest` (15) | Login correcto/incorrecto, 429, expiración + refresh con rotación, un solo refresh concurrente, refresh revocado, restauración (válida, sin conexión, sin sesión), cambio de rol, logout (204, con access expirado, sin red), token inválido tras logout, timeout, `toString` sin JWT |
| `data/RepositoryTest` (21) | Contrato `expand=detail`, Bearer y cabeceras, filtro de estado, detalle, campos nuevos; 400/401/403/404/500/502, cuerpo no JSON, timeout, red caída, desconexión, JSON inesperado/incompleto/malformado; evidencias sin JWT hacia Garage, URL firmada denegada, límite de tamaño; notificaciones y contador |
| `core/PresentationModelsTest` (11) | Etiqueta WS- (misma regla que NotificationsService), indicadores del Dashboard, filtros, personal derivado, conteo en vivo, timeline, discrepancias, ruta por estado, nota de escalación |
| `preferences/PreferencesRepositoryTest` (5) | Valores por defecto, persistencia tras reinicio, cambios en tiempo de ejecución, sincronización inicial con el servidor, mapeos |
| `ui/ViewModelTest` (7) | Login (validación, error, doble pulsación), Empty/NotPermitted, filtro de estado, evidencia bloqueada / 403 |
| `integration/GatewayIntegrationTest` (3) | **Backend real** por el Gateway con el código de red de la app: login, `/me`, todas las sesiones y sus detalles contra los DTO, filtro de estado, 404, evidencia firmada + descarga, notificaciones, refresh con rotación, logout y token rechazado después; `it_admin` → 403; contraseña errónea → 401. Solo lectura + login/logout |
| `androidTest/SecureTokenStoreTest` | Cifrado real con Android Keystore; el archivo no contiene los JWT en claro |

Variables para la prueba de integración (nunca en el repositorio):
```bash
export PEF_IT_BASE_URL=http://127.0.0.1:27080/
export PEF_IT_SUPERVISOR_EMAIL=supervisor@instrumed.com PEF_IT_SUPERVISOR_PASSWORD=...   # de la semilla 003
export PEF_IT_ADMIN_EMAIL=admin@instrumed.com PEF_IT_ADMIN_PASSWORD=...                  # opcional
./gradlew testDebugUnitTest --tests "*GatewayIntegrationTest*"
```

## 9. Prueba integral con Docker Compose (ANDROID-02)

### 9.1 Pila aislada
Para no tocar la pila principal ni las de otros checkouts, la prueba usa un proyecto Compose propio, `pef_android_it`,
creado desde el repositorio PEF con volúmenes nuevos, secretos aleatorios y todos los puertos en `127.0.0.1` (27xxx).
La configuración se genera en `<repo PEF>/.e2e/android_it/` (ignorado por git):
```bash
python -I tools/backend-it/setup_android_it_stack.py <repo PEF> <carpeta con best.pt>
cd <repo PEF>
docker compose -p pef_android_it --env-file .e2e/android_it/compose.env -f docker-compose.yml \
  -f .e2e/android_it/override.yml --profile gateway up -d --build --wait
#   + -f .e2e/android_it/override-gateway-evidence.yml   para probar la ruta de evidencias por el Gateway (§3.5)
curl http://127.0.0.1:27080/healthz
# Detener sin borrar datos:  ... stop    (nunca `down -v`)
```
Solo para pruebas, esa pila usa access tokens de 2 minutos (para ejercitar el refresh) y un límite de login de 60/min.
La semilla 003 aporta 6 sesiones (todas las fases) pero no evidencias ni notificaciones; se crearon **mediante la API
real**: el operador subió un JPEG sintético (patrón de prueba, no clínico) a su sesión en curso y el supervisor programó
una sesión con referencias reales del catálogo, lo que generó una notificación `session_scheduled`.

### 9.2 Resultados (emulador `Medium_Phone`, Android 37, 2026-11-11)

| # | Pantalla / caso | Resultado |
|---|---|---|
| 1 | Login con contraseña errónea | "Invalid email or password." + referencia; no navega |
| 1 | Login supervisor / operador / admin | OK |
| 2 | Dashboard | Indicadores reales (7 sesiones: 3 programadas, 1 en curso, 1 revisión, 1 corrección, 1 cerrada; 1 discrepancia) |
| 3 | Sesiones + filtro de estado | OK; el filtro va al servidor (`status=in_progress`) |
| 4–6 | Detalle: sesión, inventario, timeline, conteo en vivo | OK; sin conteos automáticos se muestra "—" |
| 7 | Evidencias | Listado + imagen por URL firmada a través del Gateway (prototipo §3.5) |
| 8–9 | Alertas: notificación real, marcar como leída, ir a la sesión | OK (PATCH 200, insignia desaparece, navega al detalle según estado real) |
| 8 | Sesiones en espera + discrepancia `shortage` | OK |
| 10–11 | Perfil y Ajustes | Nombre, correo, roles reales |
| 12–14 | Idioma ES, tema claro, persistencia tras matar el proceso | OK; tema oscuro inicial venía de `ui_preferences` del servidor |
| 15 | Logout | POST 204, blob cifrado vaciado, "Cerraste sesión." |
| — | Access caducado | 401 → **un** refresh 200 → reintento OK (log del Gateway) |
| — | Revocación en servidor (mismo mecanismo que la administración) | 401 → refresh 401 → Login "sesión expiró o fue revocada" |
| — | Sin conexión | "Sin conexión con el servidor" + Reintentar; recupera al reintentar |
| — | Rol `it_admin` | "Tu rol no tiene acceso"; la app no llama a SessionService |
| — | 404 | Cubierto por la prueba de integración (`/sessions/{uuid inexistente}`) |

Correcciones hechas tras el recorrido: iconos de la barra de estado ilegibles en Login (ahora oscuros sobre fondo claro),
botón "Actualizar" sin función en Ajustes (eliminado) y textos de filtros cortados en español (ahora con "…").

`tools/backend-it/ui_driver.py` es el pequeño controlador (uiautomator + `adb input` + capturas) usado en el recorrido.

## 10. Errores frecuentes

| Síntoma | Causa / solución |
|---|---|
| "No connection to the server" en el emulador | Gateway caído (`curl …/healthz`) o falta `adb reverse` (se pierde al reiniciar el emulador): pulsar Run otra vez con `pef.adbReverse` configurado, o `./gradlew pefAdbReverse`, y luego Retry. Con `10.0.2.2`, el Gateway debe estar en el puerto de la URL |
| `CLEARTEXT communication … not permitted` | Host HTTP que no está en `src/debug/res/xml/network_security_config.xml` (solo `10.0.2.2`, `localhost`, `127.0.0.1`), o build release con `http://` |
| La imagen de evidencia da 403 | El host con el que se pide la URL no es el firmado (`S3_PUBLIC_ENDPOINT`); ver §3.5. O la URL caducó (5 min): volver a abrir la evidencia |
| La imagen no carga pero el listado sí | `S3_PUBLIC_ENDPOINT` apunta a un host inalcanzable desde Android (p. ej. `localhost:3900` sin `adb reverse`) |
| "Too many attempts" / 429 en login | Límite del Gateway (`GATEWAY_RATE_AUTH`, 10/min por defecto) o bloqueo de AuthService tras intentos fallidos |
| Vuelve al Login con "sesión expiró o fue revocada" | El refresh fue rechazado: inactividad, duración máxima, logout en otro lado o revocación administrativa |
| Tras `connectedDebugAndroidTest` la app pide login y vuelve al inglés | Gradle desinstala la app al terminar las pruebas instrumentadas |
| `Unable to delete directory …\app\build` (Android Studio o Gradle) | OneDrive bloquea la carpeta de build. Añadir a `local.properties` (no versionado) `pef.buildDir=C\:/Users/<usuario>/AppData/Local/pef-android-build` y sincronizar Gradle: los builds salen de OneDrive. Ahí mismo se puede poner `pef.apiBaseUrl.debug=http\://127.0.0.1\:27080/` |
| Pila Compose: `postgres is unhealthy` en el primer arranque | La primera inicialización tarda más que el healthcheck; repetir `up -d --wait` (no borrar volúmenes) |

## 11. Compilación
* AGP 9.5 (Kotlin integrado), Kotlin 2.2.10, Gradle 9.6, compileSdk/targetSdk 37, minSdk 26, JDK 17+.
* Requiere `local.properties` con `sdk.dir` (no versionado).
* Si el proyecto está dentro de OneDrive, la sincronización puede bloquear `app/build` ("Unable to delete directory");
  pausar OneDrive o compilar con un directorio de build fuera de la carpeta sincronizada.

## 12. Matriz de pantallas

| Pantalla | Estado | Fuente real |
|---|---|---|
| Login | ✅ Real | `POST /auth/login`; errores claros; no navega si falla |
| Dashboard (6 indicadores + sesiones activas/en espera) | ✅ Real (derivado) | `GET /sessions?expand=detail` agregado en el cliente; campana = `GET /notifications/unread-count` |
| Dashboard — pestaña Discrepancies (por instrumento / por familia + sesiones activas/en espera) | ✅ Real (solo `spd_supervisor`) | `GET /sessions/reports/summary?from=` (`discrepancies.by_family`) + `GET /catalog/families` y `/catalog/categories` para agrupar por categoría; periodo 7/30/90/365 días (90 por defecto); más de 4 grupos → los 3 mayores + "Other" |
| Alerts / On Hold — Notificaciones | ✅ Real | `GET /notifications` (filtros estado/tipo/fecha en servidor), `PATCH /{id}/read`, `PATCH /read-all`, navegación a la sesión |
| Alerts / On Hold — Sesiones en espera | ✅ Real | `GET /sessions?expand=detail&status=awaiting_spd_review,correction_required` |
| Sessions (búsqueda, estado, sala, procedimiento) | ✅ Real | `GET /sessions?expand=detail[&status=]` |
| Detalle en curso — Session / Inventory / Timeline / Live Count | ✅ Real | `GET /sessions/{id}?expand=detail` |
| Detalle — Evidence | ✅ Real (verificado en emulador) | `GET /sessions/{id}/evidence`, `GET /evidence/{id}` (URL prefirmada; ver §3.5 para alcanzarla) |
| Detalle en espera / cerrada — Session (discrepancias) / Timeline / Evidence | ✅ Real | idem |
| Detalle programada — Session / Inventory | ✅ Real | idem |
| Comentarios (FAB en detalle en espera) | ⛔ No disponible | No hay endpoint publicado; diseño conservado, entrada deshabilitada |
| Personal | 🟡 Parcial (derivado) | Operadores de las sesiones visibles; no hay directorio publicado |
| Detalle de personal (activas / programadas / historial) | 🟡 Parcial (derivado) | idem |
| Ajustes (nuevo) | ✅ | DataStore + `GET /me` + `POST /logout` |

Indicadores: "Sessions today" cuenta sesiones con `scheduled_at` o `started_at` en el día local; "Open discrepancies" es
la suma de `open_discrepancies` de las sesiones visibles. Sin datos (error/sin permiso) se muestra "—", nunca un número.

## 13. Limitaciones e integraciones pendientes (contratos mínimos propuestos)
1. **Comentarios de sesión** — `GET/POST /api/v1/sessions/{id}/comments` (`{id, author{id,name}, body, created_at}`),
   mismas reglas de alcance que la lectura de sesiones, auditado.
2. **Directorio de personal** — p. ej. `GET /api/v1/auth/directory?role=` solo para `spd_supervisor`, campos mínimos
   (`id, name, roles`), sin correo ni datos sensibles. Hoy la app deriva operadores de las sesiones.
3. **Sincronizar preferencias** — publicar `PATCH /api/v1/auth/me/preferences {locale, theme}` con Bearer (hoy
   `POST /preferences` solo existe con cookies del portal).
4. **Push (FCM)** — no implementado. Requiere: registro de token de dispositivo (`POST /api/v1/notifications/devices`
   `{platform, token}`; `DELETE` al hacer logout), un canal FCM en el *outbox worker* de NotificationsService, credenciales
   del proyecto Firebase en el servidor y `google-services.json` + permiso `POST_NOTIFICATIONS` en la app.
5. **Resultado de inferencia por evidencia** y **confianza por familia**: EvidenceService y el snapshot no los publican;
   la app muestra conteos del snapshot y lo indica en pantalla.
6. **Acciones del Supervisor** (resolver discrepancias, cierre seguro): existen en SessionService para el portal, pero la
   app es de solo lectura a propósito — nunca cierra sesiones ni omite la revisión humana.
7. **URLs firmadas en producción**: hoy Garage se publica sin TLS para las URLs prefirmadas; se propone servirlas por el
   Gateway (`docs/backend-proposal/gateway-presigned-evidence.patch`, probado en la pila aislada, no aplicado).
8. **Miniaturas de evidencia**: no se piden URLs firmadas para todas (cada emisión se audita); la imagen se descarga al
   abrirla, sin caché en disco, solo en memoria.
9. Paginación de notificaciones: se muestran las 50 más recientes del filtro (la API pagina; falta "cargar más").
10. Los ViewModels comparten el `ViewModelStore` de la Activity (Navigation3 sin decorador de ViewModel por entrada);
   se usan claves por sesión y se recarga al entrar a cada pantalla.
