package com.example.cursobasico.data

import com.example.cursobasico.Fixtures
import com.example.cursobasico.RoutingDispatcher
import com.example.cursobasico.auth.InMemoryTokenStore
import com.example.cursobasico.bearer
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.repository.DiscrepancyStatsRepository
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.NotificationRepository
import com.example.cursobasico.data.repository.SessionRepository
import com.example.cursobasico.json
import com.example.cursobasico.network
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

class RepositoryTest {
    @get:Rule
    val timeout: Timeout = Timeout.seconds(30)

    private lateinit var server: MockWebServer
    private val routes = RoutingDispatcher()
    private val sessionsPath = "/api/v1/sessions"
    private val detailPath = "/api/v1/sessions/${Fixtures.SESSION_ID}"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = routes
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** Red con la sesión restaurada (como al abrir la app): GET /me válido y luego se limpia el registro. */
    private fun repo(readTimeoutMillis: Long = 30_000): SessionRepository {
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.userJson()) }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()), readTimeoutMillis)
        runBlocking { network.sessionManager.restore() }
        routes.requests.clear()
        return SessionRepository(network.sessionApi)
    }

    // ------------------------------------------------------------------ respuestas correctas y filtros

    @Test
    fun `listado usa expand=detail, envia Bearer y parsea el contrato`() = runBlocking {
        routes.on("GET", sessionsPath) { json("[${Fixtures.summaryJson()}]") }
        val result = repo().listSessions()

        val sessions = (result as ApiResult.Success).data
        assertEquals(1, sessions.size)
        assertEquals(SessionStatus.IN_PROGRESS, sessions[0].status.code)
        assertEquals("Operator One", sessions[0].operator?.name)
        assertEquals("OR-3", sessions[0].operation?.room?.code)
        val request = routes.requests.single()
        assertEquals("detail", request.requestUrl?.queryParameter("expand"))
        assertNull(request.requestUrl?.queryParameter("status"))
        assertEquals("access-1", request.bearer())
        assertEquals("application/json", request.getHeader("Accept"))
        assertTrue(request.getHeader("X-Correlation-ID")!!.length >= 8)
    }

    @Test
    fun `filtro por estado se delega al backend`() = runBlocking {
        routes.on("GET", sessionsPath) { json("[]") }
        repo().listSessions(SessionStatus.ON_HOLD.sorted())
        assertEquals("awaiting_spd_review,correction_required", routes.requests.single().requestUrl?.queryParameter("status"))
    }

    @Test
    fun `detalle incluye inventario, eventos, discrepancias y equipo`() = runBlocking {
        routes.on("GET", detailPath) { json(Fixtures.detailJson()) }
        val detail = (repo().getSession(Fixtures.SESSION_ID) as ApiResult.Success).data
        assertEquals(2, detail.expectedInventory.size)
        assertEquals(2, detail.events.size)
        assertEquals(1, detail.discrepancies.size)
        assertEquals("Synthetic Surgeon", detail.surgicalTeam.single().name)
        assertEquals("detail", routes.requests.single().requestUrl?.queryParameter("expand"))
    }

    @Test
    fun `campos nuevos del backend se ignoran`() = runBlocking {
        routes.on("GET", sessionsPath) { json("[${Fixtures.summaryJson().dropLast(1)},\"new_field\":{\"x\":1}}]") }
        assertTrue(repo().listSessions() is ApiResult.Success)
    }

    // ------------------------------------------------------------------ errores

    private fun assertError(code: Int, expected: Class<out AppError>) = runBlocking {
        routes.on("GET", sessionsPath) { json(Fixtures.error("x", "server says $code", "corr-$code"), code) }
        routes.on("POST", "/api/v1/auth/refresh") { json(Fixtures.error("unauthorized", "revoked"), 401) }
        val error = (repo().listSessions() as ApiResult.Failure).error
        assertTrue("$code -> $error", expected.isInstance(error))
        if (code != 401) {
            assertEquals("server says $code", error.serverMessage)
            assertEquals("corr-$code", error.correlationId)
        }
    }

    @Test fun `400 se mapea a BadRequest`() = assertError(400, AppError.BadRequest::class.java)
    @Test fun `401 sin refresh posible se mapea a Unauthorized`() = assertError(401, AppError.Unauthorized::class.java)
    @Test fun `403 se mapea a Forbidden`() = assertError(403, AppError.Forbidden::class.java)
    @Test fun `404 se mapea a NotFound`() = assertError(404, AppError.NotFound::class.java)
    @Test fun `500 se mapea a Server`() = assertError(500, AppError.Server::class.java)
    @Test fun `502 del gateway se mapea a Server`() = assertError(502, AppError.Server::class.java)

    @Test
    fun `error sin cuerpo JSON conserva el estado`() = runBlocking {
        routes.on("GET", sessionsPath) { MockResponse().setResponseCode(503).setBody("<html>oops</html>") }
        val error = (repo().listSessions() as ApiResult.Failure).error as AppError.Server
        assertEquals(503, error.status)
        assertNull(error.serverMessage)
    }

    @Test
    fun `timeout se mapea a Timeout`() = runBlocking {
        routes.on("GET", sessionsPath) { MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE) }
        assertEquals(AppError.Timeout, (repo(readTimeoutMillis = 300).listSessions() as ApiResult.Failure).error)
    }

    @Test
    fun `falla de red se mapea a Network`() = runBlocking {
        val repository = repo()
        server.shutdown()
        assertEquals(AppError.Network, (repository.listSessions() as ApiResult.Failure).error)
    }

    @Test
    fun `conexion cortada a mitad de respuesta se mapea a Network`() = runBlocking {
        routes.on("GET", sessionsPath) { MockResponse().setBody("[]").setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST) }
        assertEquals(AppError.Network, (repo().listSessions() as ApiResult.Failure).error)
    }

    @Test
    fun `JSON inesperado se mapea a InvalidResponse`() = runBlocking {
        routes.on("GET", sessionsPath) { json("""{"unexpected":"object instead of list"}""") }
        assertEquals(AppError.InvalidResponse, (repo().listSessions() as ApiResult.Failure).error)
    }

    @Test
    fun `JSON sin campos obligatorios se mapea a InvalidResponse`() = runBlocking {
        routes.on("GET", detailPath) { json("""{"id":"x"}""") }
        assertEquals(AppError.InvalidResponse, (repo().getSession(Fixtures.SESSION_ID) as ApiResult.Failure).error)
    }

    @Test
    fun `JSON malformado se mapea a InvalidResponse`() = runBlocking {
        routes.on("GET", sessionsPath) { json("[{\"id\":") }
        assertEquals(AppError.InvalidResponse, (repo().listSessions() as ApiResult.Failure).error)
    }

    // ------------------------------------------------------------------ evidencias

    @Test
    fun `evidencias se listan y la imagen se descarga sin enviar el JWT a Garage`() = runBlocking {
        val png = byteArrayOf(1, 2, 3, 4)
        val signedUrl = server.url("/garage/bucket/key.jpg?X-Amz-Signature=abc").toString()
        routes.on("GET", "/api/v1/sessions/${Fixtures.SESSION_ID}/evidence") {
            json("""[{"id":"ev1","session_id":"${Fixtures.SESSION_ID}","kind":"jpeg","content_type":"image/jpeg",
                "size_bytes":4,"sha256":"x","storage_provider":"s3","retention_until":null,"created_at":"2026-11-10T16:05:00Z",
                "blocked":false,"purged":false,"signed_url":null,"signed_url_expires_at":null}]""")
        }
        routes.on("GET", "/api/v1/evidence/ev1") {
            json("""{"id":"ev1","session_id":"${Fixtures.SESSION_ID}","kind":"jpeg","blocked":false,"purged":false,
                "signed_url":"$signedUrl","signed_url_expires_at":"2026-11-10T16:20:00Z"}""")
        }
        routes.on("GET", "/garage/bucket/key.jpg") { MockResponse().setBody(Buffer().write(png)) }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()))
        val repository = EvidenceRepository(network.evidenceApi, network.imageClient)

        val list = (repository.listForSession(Fixtures.SESSION_ID) as ApiResult.Success).data
        assertNull(list.single().signedUrl)
        val signed = (repository.getSigned("ev1") as ApiResult.Success).data
        val bytes = (repository.downloadImage(signed.signedUrl!!) as ApiResult.Success).data

        assertArrayEquals(png, bytes)
        val imageRequest = routes.requests.last()
        assertEquals("/garage/bucket/key.jpg", imageRequest.requestUrl?.encodedPath)
        assertNull(imageRequest.getHeader("Authorization"))
    }

    @Test
    fun `URL prefirmada caducada o denegada se informa como Forbidden`() = runBlocking {
        routes.on("GET", "/garage/key.jpg") { MockResponse().setResponseCode(403) }
        val network = server.network()
        val repository = EvidenceRepository(network.evidenceApi, network.imageClient)
        val error = (repository.downloadImage(server.url("/garage/key.jpg").toString()) as ApiResult.Failure).error
        assertTrue(error is AppError.Forbidden)
    }

    @Test
    fun `imagen mayor al limite se rechaza`() = runBlocking {
        routes.on("GET", "/garage/big.jpg") { MockResponse().setBody(Buffer().write(ByteArray(2048))) }
        val network = server.network()
        val repository = EvidenceRepository(network.evidenceApi, network.imageClient, maxImageBytes = 1024)
        assertTrue((repository.downloadImage(server.url("/garage/big.jpg").toString()) as ApiResult.Failure).error is AppError.BadRequest)
    }

    // ------------------------------------------------------------------ notificaciones

    @Test
    fun `notificaciones paginadas, marcar leida y contador de no leidas`() = runBlocking {
        var unread = 2
        routes.on("GET", "/api/v1/notifications") { req ->
            assertEquals("unread", req.requestUrl?.queryParameter("status"))
            json("""{"items":[{"id":"n1","type":"review_pending","status":"unread","title":"Session pending review",
                "message":"Counting session WS-0A1B2C3D is waiting for SPD review and close.","session_id":"${Fixtures.SESSION_ID}",
                "data":{"session_label":"WS-0A1B2C3D"},"created_at":"2026-11-10T16:00:00Z","read_at":null}],
                "page":1,"per_page":50,"total":1,"total_pages":1,"unread":$unread,"sort":"-created_at"}""")
        }
        routes.on("PATCH", "/api/v1/notifications/n1/read") {
            unread = 1
            json("""{"id":"n1","type":"review_pending","status":"read","session_id":null,"created_at":"2026-11-10T16:00:00Z",
                "read_at":"2026-11-10T16:30:00Z"}""")
        }
        routes.on("GET", "/api/v1/notifications/unread-count") { json("""{"unread":$unread}""") }
        routes.on("PATCH", "/api/v1/notifications/read-all") { json("""{"updated":1,"unread":0}""") }
        val repository = NotificationRepository(server.network(InMemoryTokenStore(Fixtures.storedSession())).notificationsApi)

        val page = (repository.list(status = "unread") as ApiResult.Success).data
        assertTrue(page.items.single().isUnread)
        assertEquals(2, repository.unread.value)

        val marked = (repository.markRead("n1") as ApiResult.Success).data
        assertEquals("read", marked.status)
        assertEquals(1, repository.unread.value)

        repository.markAllRead()
        assertEquals(0, repository.unread.value)
    }

    // ------------------------------------------------------------------ estadísticas de discrepancias (Dashboard)

    @Test
    fun `discrepancias del dashboard combinan reporte y catalogo`() = runBlocking {
        routes.on("GET", "/api/v1/sessions/reports/summary") { req ->
            assertEquals("2026-08-13", req.requestUrl?.queryParameter("from"))
            json("""{"filters":{"from":"2026-08-13T00:00:00Z","to":"2026-11-11T18:00:00Z","tz":"UTC"},
                "sessions":{"total":7},"discrepancies":{"total":3,"open":1,"resolved":2,"timed":2,
                "avg_resolution_minutes":12.5,"by_reason":[],"by_family":[
                {"family_id":"f1","code":"FARABEUF","name":"Farabeuf retractor","total":2,"open":1},
                {"family_id":null,"code":null,"name":null,"total":1,"open":0}]},"ai_agreement":{}}""")
        }
        routes.on("GET", "/api/v1/catalog/families") {
            json("""[{"id":"f1","code":"FARABEUF","name":"Farabeuf retractor","category_code":"retraction","active":true}]""")
        }
        routes.on("GET", "/api/v1/catalog/categories") { json("""[{"id":"c1","code":"retraction","name":"Retraction"}]""") }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()))
        val repository = DiscrepancyStatsRepository(network.sessionApi, network.catalogApi)

        val charts = (repository.load("2026-08-13") as ApiResult.Success).data
        assertEquals(3, charts.total)
        assertEquals(1, charts.open)
        assertEquals(listOf("Farabeuf retractor", null), charts.byInstrument.map { it.label })
        assertEquals(listOf("Retraction", null), charts.byFamily.map { it.label })

        repository.load("2026-08-13")   // el catálogo se reutiliza de memoria
        assertEquals(1, routes.count("GET", "/api/v1/catalog/families"))
        assertEquals(2, routes.count("GET", "/api/v1/sessions/reports/summary"))
    }

    @Test
    fun `reporte de discrepancias denegado se informa como Forbidden`() = runBlocking {
        routes.on("GET", "/api/v1/sessions/reports/summary") { json(Fixtures.error("forbidden", "Forbidden"), 403) }
        routes.on("GET", "/api/v1/catalog/families") { json("[]") }
        routes.on("GET", "/api/v1/catalog/categories") { json("[]") }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()))
        val error = (DiscrepancyStatsRepository(network.sessionApi, network.catalogApi).load(null) as ApiResult.Failure).error
        assertTrue(error is AppError.Forbidden)
    }
}
