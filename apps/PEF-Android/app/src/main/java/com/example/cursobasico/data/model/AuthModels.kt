package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Contratos de AuthService publicados por el Gateway (apps/BackendAuthService/api_v1.py):
 *   POST /api/v1/auth/login       {email, password}         -> LoginResponse
 *   POST /api/v1/auth/refresh     {refresh_token}           -> TokenResponse (rotación: el refresh anterior queda revocado)
 *   POST /api/v1/auth/logout      Bearer + {refresh_token}  -> 204 (revoca access y refresh)
 *   GET  /api/v1/auth/me          Bearer                    -> UserDto
 *   GET  /api/v1/auth/permissions Bearer                    -> PermissionsDto
 */

@Serializable
data class LoginRequest(val email: String, val password: String)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class LogoutRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "Bearer",
    @SerialName("expires_in") val expiresIn: Long,
)

@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "Bearer",
    @SerialName("expires_in") val expiresIn: Long,
    val user: UserDto,
)

@Serializable
data class UiPreferencesDto(
    val locale: String? = null,
    val theme: String? = null,
)

@Serializable
data class UserDto(
    val id: String,
    val name: String,
    val email: String,
    @SerialName("institution_id") val institutionId: String,
    @SerialName("ui_preferences") val uiPreferences: UiPreferencesDto? = null,
    val roles: List<String> = emptyList(),
)

@Serializable
data class PermissionsDto(
    @SerialName("user_id") val userId: String,
    @SerialName("institution_id") val institutionId: String,
    val roles: List<String> = emptyList(),
)

/** Sobre de error DS09 común a todos los servicios: {"error","message","correlation_id","details"}. */
@Serializable
data class ApiErrorBody(
    val error: String? = null,
    val message: String? = null,
    @SerialName("correlation_id") val correlationId: String? = null,
)

/** Roles reales del PEF (role.code). Solo los dos primeros tienen acceso clínico a sesiones y evidencias. */
object Roles {
    const val STATION_OPERATOR = "station_operator"
    const val SPD_SUPERVISOR = "spd_supervisor"
    const val IT_ADMIN = "it_admin"
}
