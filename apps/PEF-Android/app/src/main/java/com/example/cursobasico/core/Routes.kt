package com.example.cursobasico.core

import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.data.model.SessionStatus
import kotlinx.serialization.Serializable


@Serializable
data object Login : NavKey
@Serializable
data object Menu: NavKey

@Serializable
data object AlertsOnHold: NavKey

@Serializable
data object Sessions: NavKey

@Serializable
data object Personal: NavKey

/** ANDROID-01: detail screens get real session id (SessionService UUID) */
@Serializable
data class SessionInProgressDetail(val sessionId: String) : NavKey

@Serializable
data class OnHoldDetail(val sessionId: String) : NavKey

@Serializable
data class SessionScheduledDetail(val sessionId: String) : NavKey

/** operator derived from sessions user can see (no published personnel directory) */
@Serializable
data class PersonalDetail(val operatorId: String, val operatorName: String) : NavKey

/** ANDROID-01: prefs (lang, theme, notifs) + logout */
@Serializable
data object Settings : NavKey

/**
 * detail screen from REAL backend status:
 * in progress -> InProgress; scheduled -> Scheduled; SPD review / correction / closed / aborted -> OnHold (report).
 */
fun sessionRouteFor(sessionId: String, statusCode: String): NavKey = when (statusCode) {
    SessionStatus.IN_PROGRESS -> SessionInProgressDetail(sessionId)
    SessionStatus.SCHEDULED -> SessionScheduledDetail(sessionId)
    else -> OnHoldDetail(sessionId)
}
