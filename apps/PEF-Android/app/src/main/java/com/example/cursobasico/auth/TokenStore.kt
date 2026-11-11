package com.example.cursobasico.auth

import com.example.cursobasico.data.model.UserDto
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Sesión persistida. NUNCA incluye la contraseña. Solo se guarda cifrada ([SecureTokenStore]) y
 * fuera de las preferencias no sensibles (DataStore de preferencias de interfaz).
 */
@Serializable
data class StoredSession(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    /** Instante (epoch ms) en que caduca el access token, calculado con expires_in. */
    @SerialName("access_expires_at") val accessExpiresAtMillis: Long,
    val user: UserDto,
) {
    /** Evita imprimir los JWT por accidente (toString de data class). */
    override fun toString(): String = "StoredSession(user=${user.id}, accessExpiresAt=$accessExpiresAtMillis)"
}

/** Almacenamiento de credenciales sensibles (implementación cifrada en producción, en memoria en pruebas). */
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
