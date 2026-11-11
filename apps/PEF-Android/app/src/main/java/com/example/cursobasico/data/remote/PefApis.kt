package com.example.cursobasico.data.remote

import com.example.cursobasico.data.model.CatalogCategoryDto
import com.example.cursobasico.data.model.CatalogFamilyDto
import com.example.cursobasico.data.model.EvidenceDto
import com.example.cursobasico.data.model.LoginRequest
import com.example.cursobasico.data.model.LoginResponse
import com.example.cursobasico.data.model.LogoutRequest
import com.example.cursobasico.data.model.NotificationDto
import com.example.cursobasico.data.model.NotificationPageDto
import com.example.cursobasico.data.model.PermissionsDto
import com.example.cursobasico.data.model.ReadAllRequest
import com.example.cursobasico.data.model.ReadAllResultDto
import com.example.cursobasico.data.model.RefreshRequest
import com.example.cursobasico.data.model.ReportSummaryDto
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.model.TokenResponse
import com.example.cursobasico.data.model.UnreadCountDto
import com.example.cursobasico.data.model.UserDto
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/*
 * Únicamente rutas PUBLICADAS por el API Gateway (infra/gateway/templates/pef/routes.conf.template).
 * Rutas relativas a la URL base (BuildConfig.API_BASE_URL), sin "/" inicial.
 */

/** Rutas de AuthService que NO llevan Bearer (cliente HTTP sin autenticador). */
interface AuthPublicApi {
    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @POST("api/v1/auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): TokenResponse
}

/** Rutas de AuthService con Bearer. */
interface AuthApi {
    @GET("api/v1/auth/me")
    suspend fun me(): UserDto

    @GET("api/v1/auth/permissions")
    suspend fun permissions(): PermissionsDto

    @POST("api/v1/auth/logout")
    suspend fun logout(@Body body: LogoutRequest): Response<Unit>
}

/** SessionService (lecturas; la app no ejecuta transiciones de estado). */
interface SessionApi {
    @GET("api/v1/sessions")
    suspend fun listSessions(
        @Query("expand") expand: String = "detail",
        @Query("status") status: String? = null,
    ): List<SessionSummaryDto>

    @GET("api/v1/sessions/{id}")
    suspend fun getSession(
        @Path("id") sessionId: String,
        @Query("expand") expand: String = "detail",
    ): SessionDetailDto

    /** Reporte agregado del supervisor (solo lectura). [from] = fecha ISO (YYYY-MM-DD); null = últimos 30 días. */
    @GET("api/v1/sessions/reports/summary")
    suspend fun reportSummary(@Query("from") from: String? = null): ReportSummaryDto
}

/** CatalogService (lecturas del catálogo usadas para agrupar discrepancias por categoría). */
interface CatalogApi {
    @GET("api/v1/catalog/families")
    suspend fun families(): List<CatalogFamilyDto>

    @GET("api/v1/catalog/categories")
    suspend fun categories(): List<CatalogCategoryDto>
}

/** EvidenceService (metadatos y URL prefirmada; nunca rutas internas de Garage). */
interface EvidenceApi {
    @GET("api/v1/sessions/{id}/evidence")
    suspend fun listSessionEvidence(@Path("id") sessionId: String): List<EvidenceDto>

    @GET("api/v1/evidence/{id}")
    suspend fun getEvidence(@Path("id") evidenceId: String): EvidenceDto
}

/** NotificationsService (notificaciones internas del usuario autenticado). */
interface NotificationsApi {
    @GET("api/v1/notifications")
    suspend fun list(
        @Query("status") status: String? = null,
        @Query("type") type: String? = null,
        @Query("from") from: String? = null,
        @Query("page") page: Int = 1,
        @Query("per_page") perPage: Int = 50,
        @Query("sort") sort: String = "-created_at",
    ): NotificationPageDto

    @GET("api/v1/notifications/unread-count")
    suspend fun unreadCount(): UnreadCountDto

    @PATCH("api/v1/notifications/{id}/read")
    suspend fun markRead(@Path("id") notificationId: String): NotificationDto

    @PATCH("api/v1/notifications/read-all")
    suspend fun markAllRead(@Body body: ReadAllRequest = ReadAllRequest()): ReadAllResultDto
}
