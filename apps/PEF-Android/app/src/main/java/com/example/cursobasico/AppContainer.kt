package com.example.cursobasico

import android.app.Application
import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStore
import com.example.cursobasico.auth.AuthState
import com.example.cursobasico.auth.SecureTokenStore
import com.example.cursobasico.auth.SessionManager
import com.example.cursobasico.auth.TokenStore
import com.example.cursobasico.data.remote.AuthApi
import com.example.cursobasico.data.remote.AuthInterceptor
import com.example.cursobasico.data.remote.AuthPublicApi
import com.example.cursobasico.data.remote.BaseUrlHolder
import com.example.cursobasico.data.remote.BaseUrlInterceptor
import com.example.cursobasico.data.remote.CatalogApi
import com.example.cursobasico.data.remote.EvidenceApi
import com.example.cursobasico.data.remote.GatewayEndpointStore
import com.example.cursobasico.data.remote.GatewayLocator
import com.example.cursobasico.data.remote.NetworkModule
import com.example.cursobasico.data.remote.NotificationsApi
import com.example.cursobasico.data.remote.SessionApi
import com.example.cursobasico.data.remote.TokenAuthenticator
import com.example.cursobasico.data.remote.normalizeGatewayBaseUrl
import com.example.cursobasico.data.repository.DiscrepancyStatsRepository
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.NotificationRepository
import com.example.cursobasico.data.repository.SessionRepository
import com.example.cursobasico.preferences.PreferencesRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Dispatcher
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * network graph, Android-free (same thing in MockWebServer tests).
 * - public client: login + refresh (no Bearer, no authenticator)
 * - authed client: Bearer + auto-renew on 401
 * - image client: no Bearer, no disk cache, only for evidence pre-signed URLs
 * - [BaseUrlInterceptor] lets you switch WiFi/IP without reinstall (debug)
 */
class PefNetwork(
    initialBaseUrl: String,
    tokenStore: TokenStore,
    val baseUrlHolder: BaseUrlHolder = BaseUrlHolder(initialBaseUrl),
    debugLogging: Boolean = false,
    readTimeoutMillis: Long = 30_000,
) {
    private val baseClient = NetworkModule.baseClient(debugLogging, readTimeoutMillis).newBuilder()
        .addInterceptor(BaseUrlInterceptor(baseUrlHolder))
        .build()
    private val publicRetrofit = NetworkModule.retrofit(normalizeGatewayBaseUrl(initialBaseUrl), baseClient)

    val sessionManager = SessionManager(tokenStore, publicRetrofit.create(AuthPublicApi::class.java))

    // OWN Dispatcher for authed client: authenticator blocks its thread waiting on refresh;
    // if it shared Dispatcher with public client, 5x 401 would fill all 5 slots/host and
    // refresh would never run (deadlock caught in SessionManagerTest).
    private val authedClient = baseClient.newBuilder()
        .dispatcher(Dispatcher())
        .addInterceptor(AuthInterceptor(sessionManager))
        .authenticator(TokenAuthenticator(sessionManager))
        .build()
    private val authedRetrofit = NetworkModule.retrofit(normalizeGatewayBaseUrl(initialBaseUrl), authedClient)

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

    /** Cold-start reachability of the API Gateway (`GET /healthz`) on the active base URL. */
    suspend fun probeGateway(retries: Int = 5): Boolean = withContext(Dispatchers.IO) {
        val url = baseUrlHolder.get().trimEnd('/') + "/healthz"
        val probeClient = baseClient.newBuilder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .callTimeout(8, TimeUnit.SECONDS)
            .build()
        repeat(retries) { attempt ->
            try {
                val request = Request.Builder().url(url).get().header("Accept", "application/json").build()
                probeClient.newCall(request).execute().use { response ->
                    if (response.isSuccessful) return@withContext true
                }
            } catch (_: Exception) {
                // Gateway down, wrong IP, Wi‑Fi isolation, or stale URL — retry / discover.
            }
            if (attempt < retries - 1) delay(500L * (attempt + 1))
        }
        false
    }
}

private val Context.uiPreferencesStore: DataStore<Preferences> by preferencesDataStore(name = "pef_ui_preferences")

/** manual deps container (no di framework; app is small) */
class AppContainer(application: Application) {
    private val appContext = application.applicationContext
    private val endpointStore = GatewayEndpointStore(appContext)
    val baseUrlHolder = BaseUrlHolder(BuildConfig.API_BASE_URL)

    val network = PefNetwork(
        initialBaseUrl = BuildConfig.API_BASE_URL,
        tokenStore = SecureTokenStore(application),
        baseUrlHolder = baseUrlHolder,
        debugLogging = BuildConfig.DEBUG,
    )
    val sessionManager: SessionManager get() = network.sessionManager
    val sessionRepository = SessionRepository(network.sessionApi)
    val discrepancyStatsRepository = DiscrepancyStatsRepository(network.sessionApi, network.catalogApi)
    val evidenceRepository = EvidenceRepository(network.evidenceApi, network.imageClient)
    val notificationRepository = NotificationRepository(network.notificationsApi)
    val preferencesRepository = PreferencesRepository(application.uiPreferencesStore)

    /** app scope: survives Activity recreate (lang/theme change) */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val restoreStarted = AtomicBoolean(false)
    private val _gatewayReachable = MutableStateFlow<Boolean?>(null)
    /** null = probing/discovering; true/false = last result. */
    val gatewayReachable: StateFlow<Boolean?> = _gatewayReachable.asStateFlow()

    private val _activeGatewayUrl = MutableStateFlow(baseUrlHolder.get())
    /** effective Gateway URL (after discovery or BuildConfig) */
    val activeGatewayUrl: StateFlow<String> = _activeGatewayUrl.asStateFlow()

    init {
        appScope.launch {
            sessionManager.state.collect { state ->
                when (state) {
                    is AuthState.LoggedIn -> preferencesRepository.applyServerDefaults(state.user.uiPreferences)
                    is AuthState.LoggedOut -> notificationRepository.clear()
                    AuthState.Restoring -> Unit
                }
            }
        }
    }

    /** Probe / discover Gateway then restore encrypted session — once per process. */
    fun restoreSessionOnce() {
        if (restoreStarted.compareAndSet(false, true)) {
            appScope.launch {
                resolveGateway()
                sessionManager.restore()
            }
        }
    }

    fun retryGatewayProbe() {
        appScope.launch {
            _gatewayReachable.value = null
            resolveGateway(forceDiscover = true)
        }
    }

    private suspend fun resolveGateway(forceDiscover: Boolean = false) {
        if (!forceDiscover && network.probeGateway(retries = 2)) {
            _activeGatewayUrl.value = baseUrlHolder.get()
            _gatewayReachable.value = true
            endpointStore.save(baseUrlHolder.get())
            return
        }

        if (BuildConfig.DEBUG) {
            val found = GatewayLocator.discover(
                store = endpointStore,
                buildConfigUrl = BuildConfig.API_BASE_URL,
            )
            if (found != null) {
                baseUrlHolder.set(found)
                endpointStore.save(found)
                _activeGatewayUrl.value = found
                _gatewayReachable.value = true
                return
            }
        }

        _activeGatewayUrl.value = baseUrlHolder.get()
        _gatewayReachable.value = network.probeGateway(retries = 3)
        if (_gatewayReachable.value == true) {
            endpointStore.save(baseUrlHolder.get())
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
