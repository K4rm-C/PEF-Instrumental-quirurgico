package com.example.cursobasico.auth

import com.example.cursobasico.Fixtures
import com.example.cursobasico.RoutingDispatcher
import com.example.cursobasico.bearer
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.json
import com.example.cursobasico.network
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.concurrent.atomic.AtomicInteger

class SessionManagerTest {
    @get:Rule
    val timeout: Timeout = Timeout.seconds(30)

    private lateinit var server: MockWebServer
    private val routes = RoutingDispatcher()

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

    @Test
    fun `login exitoso guarda tokens cifrables, usuario y pasa a LoggedIn`() = runBlocking {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.loginJson()) }
        val store = InMemoryTokenStore()
        val manager = server.network(store).sessionManager

        val result = manager.login("  Supervisor@Example.Test ", "secret")

        assertTrue(result is ApiResult.Success)
        assertTrue(manager.state.value is AuthState.LoggedIn)
        assertEquals("access-1", store.read()?.accessToken)
        assertEquals("refresh-1", store.read()?.refreshToken)
        val body = routes.requests.single().body.readUtf8()
        assertTrue(body.contains("\"email\":\"supervisor@example.test\""))
        assertTrue(body.contains("\"password\":\"secret\""))
    }

    @Test
    fun `credenciales incorrectas no crean sesion`() = runBlocking {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.error("invalid_credentials", "Invalid credentials"), 401) }
        val store = InMemoryTokenStore()
        val manager = server.network(store).sessionManager

        val result = manager.login("x@example.test", "bad")

        assertTrue((result as ApiResult.Failure).error is AppError.Unauthorized)
        assertEquals("Invalid credentials", result.error.serverMessage)
        assertFalse(manager.state.value is AuthState.LoggedIn)
        assertNull(store.read())
    }

    @Test
    fun `login limitado por el gateway devuelve RateLimited`() = runBlocking {
        routes.on("POST", "/api/v1/auth/login") { json("""{"error":"too_many_requests","message":"Rate limit exceeded; retry later"}""", 429) }
        val result = server.network().sessionManager.login("x@example.test", "pw")
        assertTrue((result as ApiResult.Failure).error is AppError.RateLimited)
    }

    @Test
    fun `access expirado se renueva con rotacion y se reintenta la peticion`() = runBlocking {
        routes.on("GET", "/api/v1/auth/me") { req ->
            if (req.bearer() == "access-2") json(Fixtures.userJson()) else json(Fixtures.error("unauthorized", "expired"), 401)
        }
        routes.on("POST", "/api/v1/auth/refresh") { req ->
            assertTrue(req.body.readUtf8().contains("\"refresh_token\":\"refresh-1\""))
            json(Fixtures.tokenJson("access-2", "refresh-2"))
        }
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val network = server.network(store)
        network.sessionManager.restore()

        assertTrue(network.sessionManager.state.value is AuthState.LoggedIn)
        assertEquals("access-2", store.read()?.accessToken)
        assertEquals("refresh-2", store.read()?.refreshToken) // el refresh anterior quedó revocado por rotación
        assertEquals(1, routes.count("POST", "/api/v1/auth/refresh"))
    }

    @Test
    fun `varios 401 simultaneos provocan un unico refresh`() = runBlocking {
        val refreshes = AtomicInteger()
        routes.on("GET", "/api/v1/auth/me") { req ->
            if (req.bearer() == "access-2") json(Fixtures.userJson()) else json(Fixtures.error("unauthorized", "expired"), 401)
        }
        routes.on("POST", "/api/v1/auth/refresh") {
            refreshes.incrementAndGet()
            Thread.sleep(150)
            json(Fixtures.tokenJson("access-2", "refresh-2"))
        }
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.loginJson()) }
        val manager = server.network().sessionManager
        manager.login("supervisor@example.test", "pw")

        val results = (1..5).map { async(kotlinx.coroutines.Dispatchers.IO) { manager.refreshProfile() } }.awaitAll()

        assertTrue(results.all { it is ApiResult.Success })
        assertEquals(1, refreshes.get())
    }

    @Test
    fun `refresh revocado cierra la sesion local con motivo de expiracion`() = runBlocking {
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.error("unauthorized", "expired"), 401) }
        routes.on("POST", "/api/v1/auth/refresh") { json(Fixtures.error("unauthorized", "Token is revoked or user is inactive"), 401) }
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val manager = server.network(store).sessionManager

        manager.restore()

        val state = manager.state.value as AuthState.LoggedOut
        val reason = state.reason as LogoutReason.SessionExpired
        assertEquals("Token is revoked or user is inactive", reason.serverMessage)
        assertNull(store.read())
        assertNull(manager.currentAccessToken())
    }

    @Test
    fun `restauracion sin conexion conserva la sesion local en modo offline`() = runBlocking {
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val manager = server.network(store).sessionManager
        server.shutdown()

        manager.restore()

        val state = manager.state.value as AuthState.LoggedIn
        assertTrue(state.offline)
        assertEquals("access-1", store.read()?.accessToken)
    }

    @Test
    fun `restauracion sin sesion guardada queda en LoggedOut sin llamar al backend`() = runBlocking {
        val manager = server.network().sessionManager
        manager.restore()
        assertEquals(AuthState.LoggedOut(), manager.state.value)
        assertTrue(routes.requests.isEmpty())
    }

    @Test
    fun `cambio de rol se refleja al consultar me`() = runBlocking {
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.userJson(roles = listOf("it_admin"))) }
        val store = InMemoryTokenStore(Fixtures.storedSession(roles = listOf("spd_supervisor")))
        val manager = server.network(store).sessionManager

        manager.restore()

        val user = (manager.state.value as AuthState.LoggedIn).user
        assertEquals(listOf("it_admin"), user.roles)
        assertFalse(user.canReadSessions)
        assertEquals(listOf("it_admin"), store.read()?.user?.roles)
    }

    @Test
    fun `logout revoca en el servidor con Bearer y refresh y limpia credenciales`() = runBlocking {
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.userJson()) }
        routes.on("POST", "/api/v1/auth/logout") { MockResponse().setResponseCode(204) }
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val manager = server.network(store).sessionManager
        manager.restore()

        val outcome = manager.logout()

        assertEquals(LogoutOutcome.REVOKED, outcome)
        val logout = routes.requests.last()
        assertEquals("access-1", logout.bearer())
        assertTrue(logout.body.readUtf8().contains("\"refresh_token\":\"refresh-1\""))
        assertNull(store.read())
        assertEquals(AuthState.LoggedOut(LogoutReason.UserInitiated(serverConfirmed = true)), manager.state.value)
    }

    @Test
    fun `logout con access expirado renueva y repite con el refresh nuevo`() = runBlocking {
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.userJson()) }
        routes.on("POST", "/api/v1/auth/refresh") { json(Fixtures.tokenJson("access-2", "refresh-2")) }
        routes.on("POST", "/api/v1/auth/logout") { req ->
            val body = req.body.readUtf8()
            if (req.bearer() == "access-2" && body.contains("refresh-2")) MockResponse().setResponseCode(204)
            else json(Fixtures.error("unauthorized", "expired"), 401)
        }
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val manager = server.network(store).sessionManager
        manager.restore()

        assertEquals(LogoutOutcome.REVOKED, manager.logout())
        assertEquals(2, routes.count("POST", "/api/v1/auth/logout"))
        assertEquals(1, routes.count("POST", "/api/v1/auth/refresh"))
        assertNull(store.read())
    }

    @Test
    fun `logout sin conexion borra igualmente las credenciales locales`() = runBlocking {
        val store = InMemoryTokenStore(Fixtures.storedSession())
        val network = server.network(store)
        server.shutdown()
        network.sessionManager.restore() // offline

        val outcome = network.sessionManager.logout()

        assertEquals(LogoutOutcome.LOCAL_ONLY, outcome)
        assertNull(store.read())
        assertEquals(AuthState.LoggedOut(LogoutReason.UserInitiated(serverConfirmed = false)), network.sessionManager.state.value)
    }

    @Test
    fun `token no valido tras logout no permite continuar`() = runBlocking {
        // Tras el logout el servidor rechaza el access y el refresh revocados.
        routes.on("GET", "/api/v1/auth/me") { json(Fixtures.error("unauthorized", "Token is revoked"), 401) }
        routes.on("POST", "/api/v1/auth/refresh") { json(Fixtures.error("unauthorized", "Token is revoked"), 401) }
        val manager = server.network(InMemoryTokenStore(Fixtures.storedSession())).sessionManager
        manager.restore()
        assertTrue(manager.state.value is AuthState.LoggedOut)
        assertNull(manager.currentAccessToken())
    }

    @Test
    fun `StoredSession no imprime los JWT`() {
        val text = Fixtures.storedSession(access = "secret-access", refresh = "secret-refresh").toString()
        assertFalse(text.contains("secret-access"))
        assertFalse(text.contains("secret-refresh"))
    }

    @Test
    fun `timeout de red se informa como Timeout`() = runBlocking {
        routes.on("POST", "/api/v1/auth/login") { MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE) }
        val result = server.network(readTimeoutMillis = 300).sessionManager.login("x@example.test", "pw")
        assertEquals(AppError.Timeout, (result as ApiResult.Failure).error)
    }
}
