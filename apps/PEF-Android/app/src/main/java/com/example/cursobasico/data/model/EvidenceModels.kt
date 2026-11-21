package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * EvidenceService (evidence_item):
 *   GET /api/v1/sessions/{id}/evidence -> list (signed_url always null here)
 *   GET /api/v1/evidence/{id}          -> dto with short TTL signed_url (audited);
 *                                         null if locked/purged.
 * app never builds Garage URLs; only uses what backend signed.
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
