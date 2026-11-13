package com.example.cursobasico.integration

import com.example.cursobasico.PefNetwork
import com.example.cursobasico.auth.AuthState
import com.example.cursobasico.auth.InMemoryTokenStore
import com.example.cursobasico.auth.LogoutOutcome
import com.example.cursobasico.auth.canReadSessions
import com.example.cursobasico.core.computeDashboard
import com.example.cursobasico.core.liveCount
import com.example.cursobasico.core.timelineRows
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.NotificationRepository
import com.example.cursobasico.data.repository.SessionRepository
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test

/**
 * ANDROID-02 — contratos REALES a través del API Gateway con el mismo código de red de la app
 * (Retrofit + OkHttp + SessionManager). Se OMITE si no hay entorno configurado:
 *
 *   PEF_IT_BASE_URL=http://127.0.0.1:27080/
 *   PEF_IT_SUPERVISOR_EMAIL / PEF_IT_SUPERVISOR_PASSWORD   (rol spd_supervisor)
 *   PEF_IT_ADMIN_EMAIL / PEF_IT_ADMIN_PASSWORD             (rol it_admin, opcional)
 *
 * Las credenciales nunca se guardan en el repositorio. Solo lee datos y hace login/refresh/logout:
 * no modifica sesiones, evidencias ni notificaciones.
 */
class GatewayIntegrationTest {
    private val env = System.getenv()
    private val baseUrl = env["PEF_IT_BASE_URL"]

    @Before
    fun requireEnvironment() {
        assumeTrue("PEF_IT_* no configurado: prueba contra backend real omitida", baseUrl != null &&
            env["PEF_IT_SUPERVISOR_EMAIL"] != null && env["PEF_IT_SUPERVISOR_PASSWORD"] != null)
    }

    private fun network() = PefNetwork(baseUrl!!, InMemoryTokenStore())

    @Test
    fun supervisorFlowAgainstRealGateway(): Unit = runBlocking {
        val network = network()
        val manager = network.sessionManager

        // Login real
        val login = manager.login(env["PEF_IT_SUPERVISOR_EMAIL"]!!, env["PEF_IT_SUPERVISOR_PASSWORD"]!!)
        assertTrue("login: $login", login is ApiResult.Success)
        val user = (manager.state.value as AuthState.LoggedIn).user
        assertTrue(user.canReadSessions)

        // Perfil
        assertTrue(manager.refreshProfile() is ApiResult.Success)

        // Sesiones: la lista y TODOS los detalles deben cumplir el contrato de los DTO
        val sessions = SessionRepository(network.sessionApi)
        val list = (sessions.listSessions() as ApiResult.Success).data
        assertTrue("la pila de pruebas debe tener sesiones", list.isNotEmpty())
        computeDashboard(list)
        list.forEach { summary ->
            val detail = sessions.getSession(summary.id)
            assertTrue("detalle ${summary.id}: $detail", detail is ApiResult.Success)
            val d = (detail as ApiResult.Success).data
            assertEquals(summary.status.code, d.status.code)
            d.timelineRows(); d.liveCount()
        }
        val onHold = (sessions.listSessions(SessionStatus.ON_HOLD) as ApiResult.Success).data
        assertTrue(onHold.all { it.status.code in SessionStatus.ON_HOLD })
        val missing = sessions.getSession("00000000-0000-4000-8000-000000000000")
        assertTrue((missing as ApiResult.Failure).error is AppError.NotFound)

        // Evidencias: listado por sesión; si hay alguna, URL firmada + descarga sin JWT
        val evidence = EvidenceRepository(network.evidenceApi, network.imageClient)
        val withEvidence = list.firstNotNullOfOrNull { s ->
            (evidence.listForSession(s.id) as? ApiResult.Success)?.data?.firstOrNull()
        }
        if (withEvidence != null) {
            val signed = (evidence.getSigned(withEvidence.id) as ApiResult.Success).data
            if (signed.signedUrl != null) {
                val bytes = evidence.downloadImage(signed.signedUrl!!)
                assertTrue("descarga firmada: $bytes", bytes is ApiResult.Success)
                assertTrue((bytes as ApiResult.Success).data.size.toLong() == (signed.sizeBytes ?: bytes.data.size.toLong()))
            }
        }

        // Estadísticas de discrepancias del Dashboard (reporte del supervisor + catálogo)
        val stats = com.example.cursobasico.data.repository.DiscrepancyStatsRepository(network.sessionApi, network.catalogApi)
            .load(java.time.LocalDate.now().minusDays(365).toString())
        assertTrue("estadísticas: $stats", stats is ApiResult.Success)
        val charts = (stats as ApiResult.Success).data
        assertEquals(charts.total, charts.byFamily.sumOf { it.count })

        // Notificaciones (solo lectura)
        val notifications = NotificationRepository(network.notificationsApi)
        assertTrue(notifications.list() is ApiResult.Success)
        assertTrue(notifications.refreshUnreadCount() is ApiResult.Success)

        // Refresh con rotación
        val before = manager.currentAccessToken()
        val renewed = manager.refreshTokens(before)
        assertTrue(renewed != null && renewed != before)

        // Logout: el servidor revoca y el token deja de servir
        assertEquals(LogoutOutcome.REVOKED, manager.logout())
        val code = OkHttpClient().newCall(
            Request.Builder().url(baseUrl + "api/v1/auth/me").header("Authorization", "Bearer $renewed").build()
        ).execute().use { it.code }
        assertEquals(401, code)
    }

    @Test
    fun itAdminCannotReadSessions(): Unit = runBlocking {
        assumeTrue(env["PEF_IT_ADMIN_EMAIL"] != null && env["PEF_IT_ADMIN_PASSWORD"] != null)
        val network = network()
        assertTrue(network.sessionManager.login(env["PEF_IT_ADMIN_EMAIL"]!!, env["PEF_IT_ADMIN_PASSWORD"]!!) is ApiResult.Success)
        val user = (network.sessionManager.state.value as AuthState.LoggedIn).user
        assertFalse(user.canReadSessions)
        val result = SessionRepository(network.sessionApi).listSessions()
        assertTrue((result as ApiResult.Failure).error is AppError.Forbidden)
        network.sessionManager.logout()
    }

    @Test
    fun wrongPasswordIsUnauthorized(): Unit = runBlocking {
        val result = network().sessionManager.login(env["PEF_IT_SUPERVISOR_EMAIL"]!!, "definitely-not-the-password")
        assertTrue((result as ApiResult.Failure).error is AppError.Unauthorized)
    }
}
