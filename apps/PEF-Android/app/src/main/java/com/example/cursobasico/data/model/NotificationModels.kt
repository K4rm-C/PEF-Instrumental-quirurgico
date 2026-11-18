package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * NotificationsService (apps/NotificationsService/services/notification_service.py):
 *   GET   /api/v1/notifications?status=&type=&from=&to=&page=&per_page=&sort=  -> NotificationPageDto
 *   GET   /api/v1/notifications/unread-count                                   -> UnreadCountDto
 *   PATCH /api/v1/notifications/{id}/read                                      -> NotificationDto
 *   PATCH /api/v1/notifications/read-all  {type?, before?}                     -> ReadAllResultDto
 * Siempre limitadas al usuario del token y a su institución.
 */
@Serializable
data class NotificationDto(
    val id: String,
    val type: String,
    val status: String,
    val title: String? = null,
    val message: String? = null,
    @SerialName("session_id") val sessionId: String? = null,
    val data: JsonObject? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("read_at") val readAt: String? = null,
) {
    val isUnread: Boolean get() = status == "unread"
}

@Serializable
data class NotificationPageDto(
    val items: List<NotificationDto> = emptyList(),
    val page: Int = 1,
    @SerialName("per_page") val perPage: Int = 20,
    val total: Int = 0,
    @SerialName("total_pages") val totalPages: Int = 1,
    val unread: Int = 0,
    val sort: String? = null,
)

@Serializable
data class UnreadCountDto(val unread: Int)

@Serializable
data class ReadAllRequest(val type: String? = null, val before: String? = null)

@Serializable
data class ReadAllResultDto(val updated: Int, val unread: Int)

/** Tipos válidos de NotificationsService (TYPES en notification_service.py). */
object NotificationTypes {
    val ALL = listOf(
        "session_scheduled", "discrepancy_pending", "correction_required", "discrepancy_resolved", "session_closed",
        "review_pending", "session_unresolved", "station_alert", "final_count_escalated", "service_alert",
    )
}
