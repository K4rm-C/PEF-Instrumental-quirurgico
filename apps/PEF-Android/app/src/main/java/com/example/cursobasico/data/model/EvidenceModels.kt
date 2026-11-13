package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * EvidenceService (apps/EvidenceService/services/evidence_service.py -> evidence_item):
 *   GET /api/v1/sessions/{id}/evidence -> List<EvidenceDto> (signed_url siempre null en el listado)
 *   GET /api/v1/evidence/{id}          -> EvidenceDto con signed_url prefirmada (TTL corto, auditada);
 *                                         null si la evidencia está bloqueada o purgada.
 * La app nunca construye URLs de Garage: solo usa la URL que el backend firma.
 */
@Serializable
data class EvidenceDto(
    val id: String,
    @SerialName("session_id") val sessionId: String,
    val kind: String,
    @SerialName("content_type") val contentType: String? = null,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
    val sha256: String? = null,
    @SerialName("storage_provider") val storageProvider: String? = null,
    @SerialName("retention_until") val retentionUntil: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    val blocked: Boolean = false,
    val purged: Boolean = false,
    @SerialName("signed_url") val signedUrl: String? = null,
    @SerialName("signed_url_expires_at") val signedUrlExpiresAt: String? = null,
)
