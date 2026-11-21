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
    /** reading encrypted session + validating via GET /me */
    data object Restoring : AuthState
    data class LoggedOut(val reason: LogoutReason? = null) : AuthState
    /** [offline] = local session restored but Gateway didnt answer /me */
    data class LoggedIn(val user: UserDto, val offline: Boolean = false) : AuthState
}

sealed interface LogoutReason {
    /** [serverConfirmed] = false if AuthService never confirmed revoke (eg offline) */
    data class UserInitiated(val serverConfirmed: Boolean) : LogoutReason
    /** backend rejected refresh (expired, idle, max life, revoked, or inactive user) */
    data class SessionExpired(val serverMessage: String?) : LogoutReason
}

enum class LogoutOutcome {
    /** AuthService revoked access+refresh (204) */
    REVOKED,
    /** couldnt confirm revoke on server; local creds cleared anyway */
    LOCAL_ONLY,
}

/** UI perms from real roles (backend rechecks every request) */
/** clinical read in App: operator (own sessions) or floor supervisor (institution). SPD = web portal, not App */
val UserDto.canReadSessions: Boolean
    get() = Roles.STATION_OPERATOR in roles || Roles.OR_CHARGE in roles
/** floor supervisor (App): institutional lists, discrepancy reports, alerts */
val UserDto.isSupervisor: Boolean get() = Roles.OR_CHARGE in roles
val UserDto.isFloorSupervisor: Boolean get() = Roles.OR_CHARGE in roles
/** App home: operator or floor supervisor */
val UserDto.canUseAppHome: Boolean
    get() = canReadSessions

/**
 * JWT session mgr for Android client.
 *
 * - real login vs AuthService; never stores pwd
 * - tokens only in [TokenStore] (encrypted) + memory for interceptor
 * - ROTATING refresh (backend revokes presented refresh, issues new pair): one refresh at a time
 *   ([refreshMutex]); late callers reuse renewed token instead of presenting a revoked one
 * - 401 on /refresh = session dead from backend -> wipe local creds + LoggedOut
 */
class SessionManager(
    private val store: TokenStore,
    private val publicApi: AuthPublicApi,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private lateinit var authApi: AuthApi

    /** wire after authed client is built (that client depends on this) */
    fun attach(api: AuthApi) {
        authApi = api
    }

    private val _state = MutableStateFlow<AuthState>(AuthState.Restoring)
    val state: StateFlow<AuthState> = _state.asStateFlow()

    @Volatile
    private var session: StoredSession? = null
    private val refreshMutex = Mutex()

    fun currentAccessToken(): String? = session?.accessToken

    /** restore on app open: only if backend still accepts it (or offline) */
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
                // authenticator already tried refresh; 401 here = session dead. if refresh
                // already expired it (session == null), keep that reason
                is AppError.Unauthorized, is AppError.Forbidden ->
                    if (session != null) expire(result.error.serverMessage)
                // offline / Gateway down: keep local session; screens show their own errors
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
     * renew access after 401. returns token to use, or null if session cant continue.
     * [failedAccessToken] = token that failed the call; if its not current, another thread already renewed.
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

    /** re-hit GET /me (eg role change) */
    suspend fun refreshProfile(): ApiResult<UserDto> {
        val result = safeApiCall { authApi.me() }
        if (result is ApiResult.Success) updateUser(result.data)
        return result
    }

    /**
     * real logout: POST /logout with Bearer + refresh_token (backend revokes both). authenticator skips
     * this path (see [TokenAuthenticator]); if access expired (401) renew once and retry with the NEW
     * refresh, cos rotation kills the old one. either way local creds get wiped.
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
