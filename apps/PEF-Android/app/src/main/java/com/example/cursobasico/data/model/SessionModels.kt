package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * Representación `expand=detail` de SessionService (apps/SessionService/services/session_snapshot.py):
 *   GET /api/v1/sessions?expand=detail[&status=a,b]   -> List<SessionSummaryDto>
 *   GET /api/v1/sessions/{id}?expand=detail            -> SessionDetailDto
 * Un supervisor (spd_supervisor) ve las sesiones de su institución; un operador solo las suyas.
 */

/** Códigos de estado definidos por el backend (STATUS_CODES). La app nunca crea transiciones. */
object SessionStatus {
    const val SCHEDULED = "scheduled"
    const val IN_PROGRESS = "in_progress"
    const val AWAITING_SPD_REVIEW = "awaiting_spd_review"
    const val CORRECTION_REQUIRED = "correction_required"
    const val CLOSED = "closed"
    const val ABORTED = "aborted"
    val ALL = listOf(SCHEDULED, IN_PROGRESS, AWAITING_SPD_REVIEW, CORRECTION_REQUIRED, CLOSED, ABORTED)

    /** Sesiones que requieren atención del Supervisor (revisión SPD o corrección). */
    val ON_HOLD = setOf(AWAITING_SPD_REVIEW, CORRECTION_REQUIRED)
}

@Serializable
data class CodeName(val code: String, val name: String? = null)

@Serializable
data class IdName(val id: String, val name: String? = null)

@Serializable
data class IdCodeName(val id: String, val code: String? = null, val name: String? = null)

@Serializable
data class OperationDto(
    val id: String,
    @SerialName("scheduled_at") val scheduledAt: String? = null,
    @SerialName("institution_id") val institutionId: String? = null,
    @SerialName("procedure_type") val procedureType: IdCodeName? = null,
    val room: IdCodeName? = null,
)

@Serializable
data class SessionSummaryDto(
    val id: String,
    val status: CodeName,
    @SerialName("capture_mode") val captureMode: String? = null,
    val operator: IdName? = null,
    val operation: OperationDto? = null,
    val kit: IdName? = null,
    val station: IdName? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("open_discrepancies") val openDiscrepancies: Int = 0,
    @SerialName("has_privacy_agreement") val hasPrivacyAgreement: Boolean = false,
)

@Serializable
data class ExpectedLineDto(
    @SerialName("family_id") val familyId: String,
    @SerialName("family_code") val familyCode: String? = null,
    @SerialName("family_name") val familyName: String? = null,
    val category: IdCodeName? = null,
    @SerialName("expected_quantity") val expectedQuantity: Int,
    val source: String? = null,
)

@Serializable
data class SessionEventDto(
    val id: String,
    @SerialName("occurred_at") val occurredAt: String? = null,
    @SerialName("event_code") val eventCode: String,
    @SerialName("event_name") val eventName: String? = null,
    @SerialName("user_id") val userId: String? = null,
    @SerialName("family_id") val familyId: String? = null,
    @SerialName("expected_quantity") val expectedQuantity: Int? = null,
    @SerialName("detected_quantity") val detectedQuantity: Int? = null,
    val payload: JsonObject? = null,
)

@Serializable
data class DiscrepancyDto(
    val id: String,
    @SerialName("session_id") val sessionId: String? = null,
    val description: String? = null,
    val resolved: Boolean = false,
    @SerialName("resolved_at") val resolvedAt: String? = null,
    @SerialName("expected_quantity") val expectedQuantity: Int? = null,
    @SerialName("detected_quantity") val detectedQuantity: Int? = null,
    val family: IdCodeName? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
    @SerialName("origin_event_code") val originEventCode: String? = null,
)

@Serializable
data class PrivacyDto(
    val version: String? = null,
    @SerialName("agreed_at") val agreedAt: String? = null,
    @SerialName("purpose_quality_ops") val purposeQualityOps: Boolean = false,
    @SerialName("purpose_model_improvement") val purposeModelImprovement: Boolean = false,
    @SerialName("agreed_by") val agreedBy: IdName? = null,
)

@Serializable
data class PatientDto(val id: String, @SerialName("display_name") val displayName: String? = null)

@Serializable
data class TeamMemberDto(
    @SerialName("physician_id") val physicianId: String,
    val name: String? = null,
    @SerialName("role_code") val roleCode: String? = null,
    @SerialName("role_name") val roleName: String? = null,
)

@Serializable
data class VisionModelDto(
    val id: String,
    @SerialName("version_tag") val versionTag: String? = null,
    val sha256: String? = null,
)

@Serializable
data class SessionDetailDto(
    val id: String,
    val status: CodeName,
    @SerialName("capture_mode") val captureMode: String? = null,
    val operator: IdName? = null,
    val operation: OperationDto? = null,
    val kit: IdName? = null,
    val station: IdName? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("open_discrepancies") val openDiscrepancies: Int = 0,
    @SerialName("has_privacy_agreement") val hasPrivacyAgreement: Boolean = false,
    val tray: JsonObject? = null,
    val phase: IdCodeName? = null,
    @SerialName("expected_inventory") val expectedInventory: List<ExpectedLineDto> = emptyList(),
    val events: List<SessionEventDto> = emptyList(),
    val discrepancies: List<DiscrepancyDto> = emptyList(),
    @SerialName("vision_model") val visionModel: VisionModelDto? = null,
    val privacy: PrivacyDto? = null,
    val patient: PatientDto? = null,
    @SerialName("surgical_team") val surgicalTeam: List<TeamMemberDto> = emptyList(),
    val contingency: JsonObject? = null,
    val escalation: JsonObject? = null,
)

/**
 * Etiqueta visible de una sesión. Es la MISMA regla que usa NotificationsService
 * (apps/NotificationsService/services/dispatcher.py -> session_label): "WS-" + 8 primeros hex del UUID.
 */
fun sessionLabel(sessionId: String): String =
    "WS-" + sessionId.replace("-", "").take(8).uppercase()
