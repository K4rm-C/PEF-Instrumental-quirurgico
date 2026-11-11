package com.example.cursobasico.ui

import com.example.cursobasico.Fixtures
import com.example.cursobasico.R
import com.example.cursobasico.RoutingDispatcher
import com.example.cursobasico.auth.InMemoryTokenStore
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.SessionRepository
import com.example.cursobasico.json
import com.example.cursobasico.network
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.viewmodel.EvidenceViewModel
import com.example.cursobasico.ui.viewmodel.EvidenceViewerState
import com.example.cursobasico.ui.viewmodel.LoginViewModel
import com.example.cursobasico.ui.viewmodel.SessionsViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelTest {
    @get:Rule
    val timeout: Timeout = Timeout.seconds(30)

    private lateinit var server: MockWebServer
    private val routes = RoutingDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.dispatcher = routes
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
        Dispatchers.resetMain()
    }

    private fun <T> StateFlow<T>.await(predicate: (T) -> Boolean): T = runBlocking { withTimeout(5_000) { first(predicate) } }

    @Test
    fun `login con campos vacios no llama al backend`() {
        val vm = LoginViewModel(server.network().sessionManager)
        vm.submit()
        assertEquals(R.string.login_error_missing_fields, vm.state.value.errorRes)
        assertTrue(routes.requests.isEmpty())
    }

    @Test
    fun `login fallido muestra error claro, borra la contrasena y no autentica`() {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.error("invalid_credentials", "Invalid credentials"), 401) }
        val network = server.network()
        val vm = LoginViewModel(network.sessionManager)
        vm.onEmailChange("x@example.test")
        vm.onPasswordChange("wrong")
        vm.submit()
        vm.submit() // pulsación repetida mientras carga: ignorada

        val state = vm.state.await { !it.loading }
        assertEquals(R.string.login_error_invalid_credentials, state.errorRes)
        assertEquals("", state.password)
        assertEquals(1, routes.count("POST", "/api/v1/auth/login"))
        assertTrue(network.sessionManager.currentAccessToken() == null)
    }

    @Test
    fun `mapeo de errores de login`() {
        assertEquals(R.string.login_error_rate_limited, LoginViewModel.loginErrorRes(AppError.RateLimited(null, null)))
        assertEquals(R.string.error_offline, LoginViewModel.loginErrorRes(AppError.Network))
        assertEquals(R.string.error_server, LoginViewModel.loginErrorRes(AppError.Server(500, null, null)))
    }

    @Test
    fun `sesiones vacias producen estado Empty y el rol sin acceso NotPermitted`() {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.loginJson()) }
        routes.on("GET", "/api/v1/sessions") { json("[]") }
        val network = server.network()
        runBlocking { network.sessionManager.login("s@example.test", "pw") }
        val vm = SessionsViewModel(SessionRepository(network.sessionApi), network.sessionManager)
        vm.load()
        assertEquals(UiState.Empty, vm.state.await { it !is UiState.Loading })

        val noSession = SessionsViewModel(SessionRepository(server.network().sessionApi), server.network().sessionManager)
        noSession.load()
        assertEquals(UiState.NotPermitted, noSession.state.value)
    }

    @Test
    fun `filtro de estado recarga desde el backend`() {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.loginJson()) }
        routes.on("GET", "/api/v1/sessions") { json("[${Fixtures.summaryJson(status = "scheduled")}]") }
        val network = server.network()
        runBlocking { network.sessionManager.login("s@example.test", "pw") }
        val vm = SessionsViewModel(SessionRepository(network.sessionApi), network.sessionManager)
        vm.load()
        vm.state.await { it is UiState.Success }
        vm.setStatusFilter("scheduled")
        vm.state.await { it is UiState.Success }
        assertEquals("scheduled", routes.requests.last().requestUrl?.queryParameter("status"))
    }

    @Test
    fun `evidencia bloqueada no intenta descargar imagen`() {
        routes.on("GET", "/api/v1/sessions/${Fixtures.SESSION_ID}/evidence") {
            json("""[{"id":"ev1","session_id":"${Fixtures.SESSION_ID}","kind":"jpeg","blocked":true,"purged":false}]""")
        }
        routes.on("GET", "/api/v1/evidence/ev1") {
            json("""{"id":"ev1","session_id":"${Fixtures.SESSION_ID}","kind":"jpeg","blocked":true,"purged":false,"signed_url":null}""")
        }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()))
        val vm = EvidenceViewModel(Fixtures.SESSION_ID, EvidenceRepository(network.evidenceApi, network.imageClient))
        vm.load()
        val list = (vm.list.await { it is UiState.Success } as UiState.Success).data
        vm.open(list.single())
        val viewer = vm.viewer.await { it !is EvidenceViewerState.Loading && it != EvidenceViewerState.Idle }
        assertTrue(viewer is EvidenceViewerState.Unavailable)
        vm.close()
        assertEquals(EvidenceViewerState.Idle, vm.viewer.value)
    }

    @Test
    fun `evidencia con acceso denegado muestra error`() {
        routes.on("GET", "/api/v1/evidence/ev1") { json(Fixtures.error("forbidden", "Forbidden"), 403) }
        val network = server.network(InMemoryTokenStore(Fixtures.storedSession()))
        val vm = EvidenceViewModel(Fixtures.SESSION_ID, EvidenceRepository(network.evidenceApi, network.imageClient))
        vm.open(com.example.cursobasico.data.model.EvidenceDto("ev1", Fixtures.SESSION_ID, "jpeg"))
        val viewer = vm.viewer.await { it is EvidenceViewerState.Failed }
        assertTrue((viewer as EvidenceViewerState.Failed).error is AppError.Forbidden)
    }

    @Test
    fun `discrepancias del dashboard solo para supervisor`() {
        routes.on("POST", "/api/v1/auth/login") { json(Fixtures.loginJson().replace("\"spd_supervisor\"", "\"station_operator\"")) }
        val network = server.network()
        runBlocking { network.sessionManager.login("op@example.test", "pw") }
        val vm = com.example.cursobasico.ui.viewmodel.DashboardViewModel(
            SessionRepository(network.sessionApi),
            com.example.cursobasico.data.repository.NotificationRepository(network.notificationsApi),
            network.sessionManager,
            com.example.cursobasico.preferences.PreferencesRepository(
                androidx.datastore.preferences.core.PreferenceDataStoreFactory.create { java.io.File.createTempFile("p", ".preferences_pb").also { it.delete() } }
            ),
            com.example.cursobasico.data.repository.DiscrepancyStatsRepository(network.sessionApi, network.catalogApi),
        )
        vm.loadDiscrepancies()
        assertEquals(UiState.NotPermitted, vm.discrepancies.value)
        assertEquals(0, routes.count("GET", "/api/v1/sessions/reports/summary"))
    }
}
