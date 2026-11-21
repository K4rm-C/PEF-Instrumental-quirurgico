package com.example.cursobasico.auth

import com.example.cursobasico.data.model.UserDto
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * persisted session. NEVER includes pwd. only stored encrypted ([SecureTokenStore]) and
 * outside non-sensitive prefs (UI DataStore).
 */
@Serializable
data class StoredSession(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    /** epoch ms when access expires, from expires_in */
    @SerialName("access_expires_at") val accessExpiresAtMillis: Long,
    val user: UserDto,
) {
    /** dont accidentally dump JWTs (data class toString) */
    override fun toString(): String = "StoredSession(user=${user.id}, accessExpiresAt=$accessExpiresAtMillis)"
}

/** sensitive creds store (encrypted in prod, in-memory in tests) */
interface TokenStore {
    suspend fun read(): StoredSession?
    suspend fun save(session: StoredSession)
    suspend fun clear()
}

class InMemoryTokenStore(initial: StoredSession? = null) : TokenStore {
    @Volatile
    private var value: StoredSession? = initial
    override suspend fun read(): StoredSession? = value
    override suspend fun save(session: StoredSession) {
        value = session
    }
    override suspend fun clear() {
        value = null
    }
}
