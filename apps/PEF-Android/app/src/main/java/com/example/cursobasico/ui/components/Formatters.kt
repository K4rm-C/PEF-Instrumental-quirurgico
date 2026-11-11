package com.example.cursobasico.ui.components

import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.example.cursobasico.R
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.ui.state.ErrorKind
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

private val DATE_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
private val TIME = DateTimeFormatter.ofPattern("HH:mm")

/** Las fechas del backend son ISO-8601 UTC ("...Z"); se muestran en la zona del dispositivo. */
fun parseInstant(iso: String?): Instant? = try {
    iso?.let { Instant.parse(it) }
} catch (_: DateTimeParseException) {
    null
}

fun formatDateTime(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String =
    parseInstant(iso)?.atZone(zone)?.format(DATE_TIME) ?: "—"

fun formatTime(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String =
    parseInstant(iso)?.atZone(zone)?.format(TIME) ?: "—"

fun isSameLocalDay(iso: String?, day: LocalDate, zone: ZoneId = ZoneId.systemDefault()): Boolean =
    parseInstant(iso)?.atZone(zone)?.toLocalDate() == day

fun formatBytes(bytes: Long?): String = when {
    bytes == null -> "—"
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "%.1f KB".format(bytes / 1024.0)
    else -> "%.1f MB".format(bytes / (1024.0 * 1024.0))
}

fun dash(value: Any?): String = value?.toString()?.takeIf { it.isNotBlank() } ?: "—"

@StringRes
fun statusLabelRes(code: String): Int = when (code) {
    SessionStatus.SCHEDULED -> R.string.status_scheduled
    SessionStatus.IN_PROGRESS -> R.string.status_in_progress
    SessionStatus.AWAITING_SPD_REVIEW -> R.string.status_awaiting_spd_review
    SessionStatus.CORRECTION_REQUIRED -> R.string.status_correction_required
    SessionStatus.CLOSED -> R.string.status_closed
    SessionStatus.ABORTED -> R.string.status_aborted
    else -> R.string.status_unknown
}

@Composable
fun statusLabel(code: String): String = stringResource(statusLabelRes(code))

@StringRes
fun notificationTypeLabelRes(type: String): Int = when (type) {
    "session_scheduled" -> R.string.ntype_session_scheduled
    "discrepancy_pending" -> R.string.ntype_discrepancy_pending
    "correction_required" -> R.string.ntype_correction_required
    "discrepancy_resolved" -> R.string.ntype_discrepancy_resolved
    "session_closed" -> R.string.ntype_session_closed
    "review_pending" -> R.string.ntype_review_pending
    "session_unresolved" -> R.string.ntype_session_unresolved
    "station_alert" -> R.string.ntype_station_alert
    "final_count_escalated" -> R.string.ntype_final_count_escalated
    "service_alert" -> R.string.ntype_service_alert
    else -> R.string.ntype_other
}

@StringRes
fun roleLabelRes(code: String): Int = when (code) {
    "station_operator" -> R.string.role_station_operator
    "spd_supervisor" -> R.string.role_spd_supervisor
    "it_admin" -> R.string.role_it_admin
    else -> R.string.role_other
}

@StringRes
fun errorTitleRes(kind: ErrorKind): Int = when (kind) {
    ErrorKind.OFFLINE -> R.string.error_offline
    ErrorKind.TIMEOUT -> R.string.error_timeout
    ErrorKind.UNAUTHORIZED -> R.string.error_unauthorized
    ErrorKind.FORBIDDEN -> R.string.error_forbidden
    ErrorKind.NOT_FOUND -> R.string.error_not_found
    ErrorKind.RATE_LIMITED -> R.string.error_rate_limited
    ErrorKind.SERVER -> R.string.error_server
    ErrorKind.INVALID_RESPONSE -> R.string.error_invalid_response
    ErrorKind.BAD_REQUEST -> R.string.error_bad_request
    ErrorKind.UNKNOWN -> R.string.error_unknown
}
