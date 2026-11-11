package com.example.cursobasico.auth

import com.example.cursobasico.data.model.LoginRequest
import com.example.cursobasico.data.model.LogoutRequest
import com.example.cursobasico.data.model.RefreshRequest
import com.example.cursobasico.data.model.Roles
import com.example.cursobasico.data.model.UserDto
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.remote.AuthApi
import com.example.cursobasico.data.remote.AuthPublicApi
import com.example.cursobasico.data.remote.safeApiCall
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

sealed interface AuthState {
    /** Leyendo la sesión cifrada y validándola contra GET /me. */
    data object Restoring : AuthState
    data class LoggedOut(val reason: LogoutReason? = null) : AuthState
    /** [offline] = sesión local restaurada, pero el Gateway no respondió al validarla. */
    data class LoggedIn(val user: UserDto, val offline: Boolean = false) : AuthState
}

sealed interface LogoutReason {
    /** [serverConfirmed] = false si AuthService no confirmó la revocación (p. ej. sin conexión). */
    data class UserInitiated(val serverConfirmed: Boolean) : LogoutReason
    /** El backend rechazó el refresh (caducado, inactividad, duración máxima, revocado o usuario inactivo). */
    data class SessionExpired(val serverMessage: String?) : LogoutReason
}

enum class LogoutOutcome {
    /** AuthService revocó access y refresh (204). */
    REVOKED,
    /** No se pudo confirmar la revocación en el servidor; las credenciales locales se borraron igualmente. */
    LOCAL_ONLY,
}

/** Permisos de interfaz derivados de los roles reales (el backend vuelve a validar cada petición). */
val UserDto.canReadSessions: Boolean
    get() = Roles.STATION_OPERATOR in roles || Roles.SPD_SUPERVISOR in roles
val UserDto.isSupervisor: Boolean get() = Roles.SPD_SUPERVISOR in roles

/**
 * Administrador de sesión JWT del cliente Android.
 *
 * - Login real contra AuthService; nunca guarda la contraseña.
 * - Tokens solo en [TokenStore] (cifrado) y en memoria para el interceptor.
 * - Refresh con ROTACIÓN (el backend revoca el refresh presentado y entrega un par nuevo): un único refresh a la
 *   vez ([refreshMutex]); quien llega tarde reutiliza el token ya renovado en lugar de presentar uno revocado.
 * - Un 401 en /refresh = sesión terminada por el backend -> credenciales locales borradas y estado LoggedOut.
 */
class SessionManager(
    private val store: TokenStore,
    private val publicApi: AuthPublicApi,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private lateinit var authApi: AuthApi

    /** Se conecta después de construir el cliente autenticado (que a su vez depende de este objeto). */
    fun attach(api: AuthApi) {
        authApi = api
    }

    private val _state = MutableStateFlow<AuthState>(AuthState.Restoring)
    val state: StateFlow<AuthState> = _state.asStateFlow()

    @Volatile
    private var session: StoredSession? = null
    private val refreshMutex = Mutex()

    fun currentAccessToken(): String? = session?.accessToken

    /** Restaura la sesión al abrir la app: solo si el backend la sigue aceptando (o si no hay conexión). */
    suspend fun restore() {
        val stored = store.read()
        if (stored == null) {
            _state.value = AuthState.LoggedOut()
            return
        }
        session = stored
        _state.value = AuthState.Restoring
        when (val result = safeApiCall { authApi.me() }) {
            is ApiResult.Success -> {
                updateUser(result.data)
            }
            is ApiResult.Failure -> when (result.error) {
                // El autenticador ya intentó el refresh; si llegó un 401 la sesión no es válida. Si el refresh
                // ya la expiró (session == null), se conserva su motivo.
                is AppError.Unauthorized, is AppError.Forbidden ->
                    if (session != null) expire(result.error.serverMessage)
                // Sin conexión / Gateway caído: se conserva la sesión local; cada pantalla mostrará el error.
                else -> if (session != null) _state.value = AuthState.LoggedIn(stored.user, offline = true)
            }
        }
    }

    suspend fun login(email: String, password: String): ApiResult<UserDto> {
        val result = safeApiCall { publicApi.login(LoginRequest(email.trim().lowercase(), password)) }
        return when (result) {
            is ApiResult.Success -> {
                val body = result.data
                val newSession = StoredSession(
                    accessToken = body.accessToken,
                    refreshToken = body.refreshToken,
                    accessExpiresAtMillis = clock() + body.expiresIn * 1000,
                    user = body.user,
                )
                store.save(newSession)
                session = newSession
                _state.value = AuthState.LoggedIn(body.user)
                ApiResult.Success(body.user)
            }
            is ApiResult.Failure -> result
        }
    }

    /**
     * Renueva el access token tras un 401. Devuelve el token a usar o null si la sesión no puede continuar.
     * [failedAccessToken] es el token con el que falló la petición: si ya no es el actual, otro hilo lo renovó.
     */
    suspend fun refreshTokens(failedAccessToken: String?): String? = refreshMutex.withLock {
        val current = session ?: return@withLock null
        if (failedAccessToken != null && current.accessToken != failedAccessToken) {
            return@withLock current.accessToken
        }
        when (val result = safeApiCall { publicApi.refresh(RefreshRequest(current.refreshToken)) }) {
            is ApiResult.Success -> {
                val renewed = current.copy(
                    accessToken = result.data.accessToken,
                    refreshToken = result.data.refreshToken,
                    accessExpiresAtMillis = clock() + result.data.expiresIn * 1000,
                )
                store.save(renewed)
                session = renewed
                renewed.accessToken
            }
            is ApiResult.Failure -> {
                if (result.error is AppError.Unauthorized) expire(result.error.serverMessage)
                null
            }
        }
    }

    /** Vuelve a consultar GET /me (p. ej. cambio de rol). */
    suspend fun refreshProfile(): ApiResult<UserDto> {
        val result = safeApiCall { authApi.me() }
        if (result is ApiResult.Success) updateUser(result.data)
        return result
    }

    /**
     * Logout real: POST /logout con Bearer + refresh_token (el backend revoca ambos). El autenticador no actúa
     * sobre esta ruta (ver [TokenAuthenticator]); si el access caducó (401) se renueva el par una vez y se repite
     * con el refresh NUEVO, porque la rotación revoca el anterior. Pase lo que pase, las credenciales locales se borran.
     */
    suspend fun logout(): LogoutOutcome {
        var outcome = LogoutOutcome.LOCAL_ONLY
        try {
            for (attempt in 0 until 2) {
                val current = session ?: break
                val result = safeApiCall { authApi.logout(LogoutRequest(current.refreshToken)) }
                if (result !is ApiResult.Success) break
                if (result.data.isSuccessful) {
                    outcome = LogoutOutcome.REVOKED
                    break
                }
                if (result.data.code() != 401 || attempt > 0) break
                refreshTokens(current.accessToken) ?: break
            }
        } finally {
            store.clear()
            session = null
            _state.value = AuthState.LoggedOut(LogoutReason.UserInitiated(outcome == LogoutOutcome.REVOKED))
        }
        return outcome
    }

    private suspend fun updateUser(user: UserDto) {
        val current = session ?: return
        val updated = current.copy(user = user)
        store.save(updated)
        session = updated
        _state.value = AuthState.LoggedIn(user)
    }

    private suspend fun expire(serverMessage: String?) {
        store.clear()
        session = null
        _state.value = AuthState.LoggedOut(LogoutReason.SessionExpired(serverMessage))
    }
}
