package com.example.cursobasico.ui.viewmodel

import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.AppContainer
import com.example.cursobasico.PefApplication
import com.example.cursobasico.R
import com.example.cursobasico.auth.AuthState
import com.example.cursobasico.auth.LogoutOutcome
import com.example.cursobasico.auth.SessionManager
import com.example.cursobasico.auth.canReadSessions
import com.example.cursobasico.auth.isSupervisor
import com.example.cursobasico.core.DashboardData
import com.example.cursobasico.core.DiscrepancyCharts
import com.example.cursobasico.core.OperatorSummary
import com.example.cursobasico.core.computeDashboard
import com.example.cursobasico.core.groupOperators
import com.example.cursobasico.core.sessionRouteFor
import com.example.cursobasico.data.model.EvidenceDto
import com.example.cursobasico.data.model.NotificationPageDto
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.repository.DiscrepancyStatsRepository
import com.example.cursobasico.data.repository.EvidenceRepository
import com.example.cursobasico.data.repository.NotificationRepository
import com.example.cursobasico.data.repository.SessionRepository
import com.example.cursobasico.preferences.AppLanguage
import com.example.cursobasico.preferences.PreferencesRepository
import com.example.cursobasico.preferences.ThemeMode
import com.example.cursobasico.preferences.UserPreferences
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.state.toUiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZoneId

/** Crea un ViewModel con las dependencias del [AppContainer] (sin framework de inyección). */
@Composable
inline fun <reified VM : ViewModel> pefViewModel(key: String? = null, crossinline create: (AppContainer) -> VM): VM {
    val container = (LocalContext.current.applicationContext as PefApplication).container
    return viewModel(key = key, factory = viewModelFactory { initializer { create(container) } })
}

private fun SessionManager.loggedInUser() = (state.value as? AuthState.LoggedIn)?.user

/* ------------------------------------------------------------------------------------------- Login */

data class LoginUiState(
    val email: String = "",
    val password: String = "",
    val loading: Boolean = false,
    @param:StringRes val errorRes: Int? = null,
    val errorDetail: String? = null,
)

class LoginViewModel(
    private val sessionManager: SessionManager,
) : ViewModel() {
    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun onEmailChange(value: String) = _state.update { it.copy(email = value, errorRes = null, errorDetail = null) }
    fun onPasswordChange(value: String) = _state.update { it.copy(password = value, errorRes = null, errorDetail = null) }

    /** La navegación al Dashboard la hace AppNavigation SOLO cuando AuthState pasa a LoggedIn. */
    fun submit() {
        val current = _state.value
        if (current.loading) return // protección contra pulsaciones repetidas
        if (current.email.isBlank() || current.password.isEmpty()) {
            _state.update { it.copy(errorRes = R.string.login_error_missing_fields) }
            return
        }
        _state.update { it.copy(loading = true, errorRes = null, errorDetail = null) }
        viewModelScope.launch {
            when (val result = sessionManager.login(current.email, current.password)) {
                is ApiResult.Success -> {
                    // ui_preferences del servidor las aplica AppContainer al pasar a LoggedIn.
                    // La contraseña no se conserva en memoria más de lo necesario.
                    _state.update { LoginUiState(email = it.email) }
                }
                is ApiResult.Failure -> _state.update {
                    it.copy(
                        loading = false,
                        password = "",
                        errorRes = loginErrorRes(result.error),
                        errorDetail = result.error.correlationId,
                    )
                }
            }
        }
    }

    companion object {
        @StringRes
        fun loginErrorRes(error: AppError): Int = when (error) {
            is AppError.Unauthorized -> R.string.login_error_invalid_credentials
            is AppError.RateLimited -> R.string.login_error_rate_limited
            AppError.Network -> R.string.error_offline
            AppError.Timeout -> R.string.error_timeout
            is AppError.Server -> R.string.error_server
            AppError.InvalidResponse -> R.string.error_invalid_response
            else -> R.string.login_error_generic
        }
    }
}

/* --------------------------------------------------------------------------------------- Dashboard */

/** Periodo de la pestaña Discrepancies (el reporte del backend admite hasta 366 días). */
enum class DiscrepancyPeriod(val days: Long) { LAST_7(7), LAST_30(30), LAST_90(90), LAST_365(365) }

class DashboardViewModel(
    private val sessions: SessionRepository,
    private val notifications: NotificationRepository,
    private val sessionManager: SessionManager,
    private val preferences: PreferencesRepository,
    private val discrepancyStats: DiscrepancyStatsRepository? = null,
) : ViewModel() {
    private val _discrepancies = MutableStateFlow<UiState<DiscrepancyCharts>>(UiState.Loading)
    val discrepancies: StateFlow<UiState<DiscrepancyCharts>> = _discrepancies.asStateFlow()
    private val _period = MutableStateFlow(DiscrepancyPeriod.LAST_90)
    val period: StateFlow<DiscrepancyPeriod> = _period.asStateFlow()
    private var loadingDiscrepancies = false

    fun setPeriod(period: DiscrepancyPeriod) {
        if (period == _period.value) return
        _period.value = period
        loadDiscrepancies(force = true)
    }

    /** Reporte del supervisor: otros roles no lo consultan (el backend respondería 403). */
    fun loadDiscrepancies(force: Boolean = false) {
        val repository = discrepancyStats ?: return
        if (loadingDiscrepancies && !force) return
        val user = sessionManager.loggedInUser()
        if (user == null || !user.isSupervisor) {
            _discrepancies.value = UiState.NotPermitted
            return
        }
        loadingDiscrepancies = true
        val previous = _discrepancies.value
        _discrepancies.value = if (previous is UiState.Success && !force) previous.copy(refreshing = true) else UiState.Loading
        viewModelScope.launch {
            val requested = _period.value
            val from = LocalDate.now().minusDays(requested.days).toString()
            val result = repository.load(from)
            if (requested == _period.value) _discrepancies.value = result.toUiState { it.total == 0 }
            loadingDiscrepancies = false
        }
    }

    private val _state = MutableStateFlow<UiState<DashboardData>>(UiState.Loading)
    val state: StateFlow<UiState<DashboardData>> = _state.asStateFlow()
    private var loading = false

    fun load() {
        if (loading) return
        val user = sessionManager.loggedInUser()
        if (user == null || !user.canReadSessions) {
            _state.value = UiState.NotPermitted
            return
        }
        loading = true
        val previous = _state.value
        if (previous is UiState.Success) _state.value = previous.copy(refreshing = true)
        viewModelScope.launch {
            val result = sessions.listSessions()
            _state.value = when (result) {
                is ApiResult.Success -> UiState.Success(computeDashboard(result.data, LocalDate.now(), ZoneId.systemDefault()))
                is ApiResult.Failure -> UiState.Error(result.error)
            }
            if (preferences.current().inAppNotifications) notifications.refreshUnreadCount()
            loading = false
        }
    }
}

/* ---------------------------------------------------------------------------------------- Sessions */

class SessionsViewModel(
    private val sessions: SessionRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {
    private val _state = MutableStateFlow<UiState<List<SessionSummaryDto>>>(UiState.Loading)
    val state: StateFlow<UiState<List<SessionSummaryDto>>> = _state.asStateFlow()

    /** Filtro de estado aplicado por el backend (`?status=`); null = todos. */
    private val _statusFilter = MutableStateFlow<String?>(null)
    val statusFilter: StateFlow<String?> = _statusFilter.asStateFlow()
    private var loading = false

    fun setStatusFilter(code: String?) {
        if (_statusFilter.value == code) return
        _statusFilter.value = code
        load(force = true)
    }

    fun load(force: Boolean = false) {
        if (loading && !force) return
        val user = sessionManager.loggedInUser()
        if (user == null || !user.canReadSessions) {
            _state.value = UiState.NotPermitted
            return
        }
        loading = true
        val previous = _state.value
        _state.value = if (previous is UiState.Success && !force) previous.copy(refreshing = true) else UiState.Loading
        viewModelScope.launch {
            val requested = _statusFilter.value
            val result = sessions.listSessions(listOfNotNull(requested))
            if (requested == _statusFilter.value) _state.value = result.toUiState { it.isEmpty() }
            loading = false
        }
    }
}

/* ---------------------------------------------------------------------------------- Session detail */

class SessionDetailViewModel(
    private val sessionId: String,
    private val sessions: SessionRepository,
) : ViewModel() {
    private val _state = MutableStateFlow<UiState<SessionDetailDto>>(UiState.Loading)
    val state: StateFlow<UiState<SessionDetailDto>> = _state.asStateFlow()
    private var loading = false

    fun load() {
        if (loading) return
        loading = true
        val previous = _state.value
        if (previous is UiState.Success) _state.value = previous.copy(refreshing = true)
        viewModelScope.launch {
            _state.value = sessions.getSession(sessionId).toUiState()
            loading = false
        }
    }
}

/* ---------------------------------------------------------------------------------------- Evidence */

sealed interface EvidenceViewerState {
    data object Idle : EvidenceViewerState
    data class Loading(val evidence: EvidenceDto) : EvidenceViewerState
    /** Bytes solo en memoria: nunca se guardan en almacenamiento del teléfono. */
    data class Image(val evidence: EvidenceDto, val bytes: ByteArray) : EvidenceViewerState {
        override fun equals(other: Any?) = other is Image && other.evidence == evidence && other.bytes === bytes
        override fun hashCode() = evidence.hashCode()
    }
    /** Bloqueada o purgada por retención: el backend no firma URL. */
    data class Unavailable(val evidence: EvidenceDto) : EvidenceViewerState
    data class Failed(val evidence: EvidenceDto, val error: AppError) : EvidenceViewerState
}

class EvidenceViewModel(
    private val sessionId: String,
    private val evidence: EvidenceRepository,
) : ViewModel() {
    private val _list = MutableStateFlow<UiState<List<EvidenceDto>>>(UiState.Loading)
    val list: StateFlow<UiState<List<EvidenceDto>>> = _list.asStateFlow()
    private val _viewer = MutableStateFlow<EvidenceViewerState>(EvidenceViewerState.Idle)
    val viewer: StateFlow<EvidenceViewerState> = _viewer.asStateFlow()
    private var loading = false

    fun load() {
        if (loading) return
        loading = true
        _list.value = UiState.Loading
        viewModelScope.launch {
            _list.value = evidence.listForSession(sessionId).toUiState { it.isEmpty() }
            loading = false
        }
    }

    /** Pide la URL prefirmada (auditada por el backend) SOLO al abrir una evidencia, y descarga la imagen. */
    fun open(item: EvidenceDto) {
        if (_viewer.value is EvidenceViewerState.Loading) return
        _viewer.value = EvidenceViewerState.Loading(item)
        viewModelScope.launch {
            _viewer.value = when (val signed = evidence.getSigned(item.id)) {
                is ApiResult.Failure -> EvidenceViewerState.Failed(item, signed.error)
                is ApiResult.Success -> {
                    val url = signed.data.signedUrl
                    if (url == null || signed.data.blocked || signed.data.purged) {
                        EvidenceViewerState.Unavailable(signed.data)
                    } else when (val image = evidence.downloadImage(url)) {
                        is ApiResult.Success -> EvidenceViewerState.Image(signed.data, image.data)
                        is ApiResult.Failure -> EvidenceViewerState.Failed(signed.data, image.error)
                    }
                }
            }
        }
    }

    fun close() {
        _viewer.value = EvidenceViewerState.Idle
    }
}

/* ------------------------------------------------------------------------------------------ Alerts */

enum class DateRange(val days: Long?) { ALL(null), TODAY(0), LAST_7(7), LAST_30(30) }

data class AlertFilters(val status: String? = null, val type: String? = null, val range: DateRange = DateRange.ALL)

class AlertsViewModel(
    private val notifications: NotificationRepository,
    private val sessions: SessionRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {
    private val _notifications = MutableStateFlow<UiState<NotificationPageDto>>(UiState.Loading)
    val notificationsState: StateFlow<UiState<NotificationPageDto>> = _notifications.asStateFlow()
    private val _onHold = MutableStateFlow<UiState<List<SessionSummaryDto>>>(UiState.Loading)
    val onHoldState: StateFlow<UiState<List<SessionSummaryDto>>> = _onHold.asStateFlow()
    private val _filters = MutableStateFlow(AlertFilters())
    val filters: StateFlow<AlertFilters> = _filters.asStateFlow()
    /** Ids con una operación en curso (marcar leída / abrir sesión): evita pulsaciones repetidas. */
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy: StateFlow<Set<String>> = _busy.asStateFlow()
    private val _actionError = MutableStateFlow<AppError?>(null)
    val actionError: StateFlow<AppError?> = _actionError.asStateFlow()
    val unread: StateFlow<Int?> = notifications.unread

    fun setFilters(filters: AlertFilters) {
        if (filters == _filters.value) return
        _filters.value = filters
        loadNotifications()
    }

    fun loadAll() {
        loadNotifications()
        loadOnHold()
    }

    fun loadNotifications() {
        val f = _filters.value
        _notifications.value = UiState.Loading
        viewModelScope.launch {
            val from = f.range.days?.let { LocalDate.now().minusDays(it).toString() }
            val result = notifications.list(status = f.status, type = f.type, from = from)
            if (f == _filters.value) _notifications.value = result.toUiState { it.items.isEmpty() }
        }
    }

    fun loadOnHold() {
        val user = sessionManager.loggedInUser()
        if (user == null || !user.canReadSessions) {
            _onHold.value = UiState.NotPermitted
            return
        }
        _onHold.value = UiState.Loading
        viewModelScope.launch {
            _onHold.value = sessions.listSessions(SessionStatus.ON_HOLD).toUiState { it.isEmpty() }
        }
    }

    fun markRead(id: String) = guarded(id) {
        when (val result = notifications.markRead(id)) {
            is ApiResult.Success -> _notifications.update { state ->
                if (state is UiState.Success) {
                    state.copy(data = state.data.copy(items = state.data.items.map { if (it.id == id) result.data else it }))
                } else state
            }
            is ApiResult.Failure -> _actionError.value = result.error
        }
    }

    fun markAllRead() = guarded(MARK_ALL) {
        when (val result = notifications.markAllRead()) {
            is ApiResult.Success -> loadNotifications()
            is ApiResult.Failure -> _actionError.value = result.error
        }
    }

    /** Consulta el estado real de la sesión antes de navegar (la notificación no lo incluye). */
    fun openSession(sessionId: String, navigate: (NavKey) -> Unit) = guarded(sessionId) {
        when (val result = sessions.getSession(sessionId)) {
            is ApiResult.Success -> navigate(sessionRouteFor(sessionId, result.data.status.code))
            is ApiResult.Failure -> _actionError.value = result.error
        }
    }

    fun consumeActionError() {
        _actionError.value = null
    }

    private fun guarded(id: String, block: suspend () -> Unit) {
        if (id in _busy.value) return
        _busy.update { it + id }
        viewModelScope.launch {
            try {
                block()
            } finally {
                _busy.update { it - id }
            }
        }
    }

    companion object {
        const val MARK_ALL = "__mark_all__"
    }
}

/* ---------------------------------------------------------------------------------------- Personal */

class PersonalViewModel(
    private val sessions: SessionRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {
    private val _state = MutableStateFlow<UiState<List<OperatorSummary>>>(UiState.Loading)
    val state: StateFlow<UiState<List<OperatorSummary>>> = _state.asStateFlow()
    private var loading = false

    fun load() {
        if (loading) return
        val user = sessionManager.loggedInUser()
        if (user == null || !user.canReadSessions) {
            _state.value = UiState.NotPermitted
            return
        }
        loading = true
        _state.value = UiState.Loading
        viewModelScope.launch {
            _state.value = when (val result = sessions.listSessions()) {
                is ApiResult.Success -> groupOperators(result.data).let { if (it.isEmpty()) UiState.Empty else UiState.Success(it) }
                is ApiResult.Failure -> UiState.Error(result.error)
            }
            loading = false
        }
    }
}

/* ---------------------------------------------------------------------------------------- Settings */

class SettingsViewModel(
    private val preferences: PreferencesRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {
    val prefs: StateFlow<UserPreferences> =
        preferences.preferences.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), UserPreferences())
    val auth: StateFlow<AuthState> = sessionManager.state
    private val _loggingOut = MutableStateFlow(false)
    val loggingOut: StateFlow<Boolean> = _loggingOut.asStateFlow()

    fun setLanguage(language: AppLanguage) = viewModelScope.launch { preferences.setLanguage(language) }
    fun setTheme(theme: ThemeMode) = viewModelScope.launch { preferences.setTheme(theme) }
    fun setInAppNotifications(enabled: Boolean) = viewModelScope.launch { preferences.setInAppNotifications(enabled) }

    fun logout(onDone: (LogoutOutcome) -> Unit) {
        if (_loggingOut.value) return
        _loggingOut.value = true
        viewModelScope.launch {
            val outcome = sessionManager.logout()
            _loggingOut.value = false
            onDone(outcome)
        }
    }
}
