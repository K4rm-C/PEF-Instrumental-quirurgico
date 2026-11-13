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

/** ANDROID-01: las pantallas de detalle reciben el id real de la sesión (UUID de SessionService). */
@Serializable
data class SessionInProgressDetail(val sessionId: String) : NavKey

@Serializable
data class OnHoldDetail(val sessionId: String) : NavKey

@Serializable
data class SessionScheduledDetail(val sessionId: String) : NavKey

/** Operador derivado de las sesiones visibles para el usuario (no existe un directorio de personal publicado). */
@Serializable
data class PersonalDetail(val operatorId: String, val operatorName: String) : NavKey

/** ANDROID-01: preferencias (idioma, tema, notificaciones) y cierre de sesión. */
@Serializable
data object Settings : NavKey

/**
 * Pantalla de detalle según el estado REAL devuelto por el backend:
 * en curso -> InProgress; programada -> Scheduled; revisión SPD / corrección / cerrada / abortada -> OnHold (reporte).
 */
fun sessionRouteFor(sessionId: String, statusCode: String): NavKey = when (statusCode) {
    SessionStatus.IN_PROGRESS -> SessionInProgressDetail(sessionId)
    SessionStatus.SCHEDULED -> SessionScheduledDetail(sessionId)
    else -> OnHoldDetail(sessionId)
}
