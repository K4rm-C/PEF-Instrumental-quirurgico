package com.example.cursobasico

import android.app.Application
import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStore
import com.example.cursobasico.auth.SecureTokenStore
import com.example.cursobasico.auth.SessionManager
import com.example.cursobasico.auth.TokenStore
import com.example.cursobasico.data.remote.AuthApi
import com.example.cursobasico.data.remote.AuthInterceptor
import com.example.cursobasico.data.remote.AuthPublicApi
import com.example.cursobasico.data.remote.CatalogApi
import com.example.cursobasico.data.remote.EvidenceApi
import com.example.cursobasico.data.remote.NetworkModule
import com.example.cursobasico.data.remote.NotificationsApi
import com.example.cursobasico.data.remote.SessionApi
import com.example.cursobasico.data.remote.TokenAuthenticator
import com.example.cursobasico.data.repository.DiscrepancyStatsRepository
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.NotificationRepository
import com.example.cursobasico.data.repository.SessionRepository
import com.example.cursobasico.preferences.PreferencesRepository
import com.example.cursobasico.auth.AuthState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.Dispatcher
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Grafo de red independiente de Android (se usa tal cual en las pruebas con MockWebServer).
 * - Cliente público: login y refresh (sin Bearer, sin autenticador).
 * - Cliente autenticado: Bearer + renovación automática ante 401.
 * - Cliente de imágenes: sin Bearer y sin caché en disco, solo para URLs prefirmadas de evidencias.
 */
class PefNetwork(
    baseUrl: String,
    tokenStore: TokenStore,
    debugLogging: Boolean = false,
    readTimeoutMillis: Long = 30_000,
) {
    private val baseClient = NetworkModule.baseClient(debugLogging, readTimeoutMillis)
    private val publicRetrofit = NetworkModule.retrofit(baseUrl, baseClient)

    val sessionManager = SessionManager(tokenStore, publicRetrofit.create(AuthPublicApi::class.java))

    // Dispatcher PROPIO para el cliente autenticado: el autenticador bloquea su hilo mientras espera el refresh;
    // si compartiera Dispatcher con el cliente público, 5 peticiones con 401 ocuparían los 5 cupos por host y la
    // llamada de refresh nunca se ejecutaría (interbloqueo detectado en SessionManagerTest).
    private val authedClient = baseClient.newBuilder()
        .dispatcher(Dispatcher())
        .addInterceptor(AuthInterceptor(sessionManager))
        .authenticator(TokenAuthenticator(sessionManager))
        .build()
    private val authedRetrofit = NetworkModule.retrofit(baseUrl, authedClient)

    val imageClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS)
        .build()

    val authApi: AuthApi = authedRetrofit.create(AuthApi::class.java)
    val sessionApi: SessionApi = authedRetrofit.create(SessionApi::class.java)
    val catalogApi: CatalogApi = authedRetrofit.create(CatalogApi::class.java)
    val evidenceApi: EvidenceApi = authedRetrofit.create(EvidenceApi::class.java)
    val notificationsApi: NotificationsApi = authedRetrofit.create(NotificationsApi::class.java)

    init {
        sessionManager.attach(authApi)
    }
}

private val Context.uiPreferencesStore: DataStore<Preferences> by preferencesDataStore(name = "pef_ui_preferences")

/** Contenedor manual de dependencias (sin framework de inyección: la app es pequeña). */
class AppContainer(application: Application) {
    val network = PefNetwork(
        baseUrl = BuildConfig.API_BASE_URL,
        tokenStore = SecureTokenStore(application),
        debugLogging = BuildConfig.DEBUG,
    )
    val sessionManager: SessionManager get() = network.sessionManager
    val sessionRepository = SessionRepository(network.sessionApi)
    val discrepancyStatsRepository = DiscrepancyStatsRepository(network.sessionApi, network.catalogApi)
    val evidenceRepository = EvidenceRepository(network.evidenceApi, network.imageClient)
    val notificationRepository = NotificationRepository(network.notificationsApi)
    val preferencesRepository = PreferencesRepository(application.uiPreferencesStore)

    /** Ámbito de aplicación: sobrevive a la recreación de la Activity (cambio de idioma o tema). */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val restoreStarted = AtomicBoolean(false)

    init {
        appScope.launch {
            sessionManager.state.collect { state ->
                when (state) {
                    // Sincronización de solo lectura con ui_preferences (GET /me o login).
                    is AuthState.LoggedIn -> preferencesRepository.applyServerDefaults(state.user.uiPreferences)
                    is AuthState.LoggedOut -> notificationRepository.clear()
                    AuthState.Restoring -> Unit
                }
            }
        }
    }

    /** Restaura la sesión cifrada una sola vez por proceso. */
    fun restoreSessionOnce() {
        if (restoreStarted.compareAndSet(false, true)) {
            appScope.launch { sessionManager.restore() }
        }
    }
}

class PefApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
