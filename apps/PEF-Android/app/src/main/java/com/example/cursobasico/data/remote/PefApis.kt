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
import com.example.cursobasico.data.model.ResolveDiscrepancyRequest
import com.example.cursobasico.data.model.ResolveDiscrepancyResult
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
 * only routes PUBLISHED by API Gateway (infra/gateway/templates/pef/routes.conf.template).
 * relative to base URL (BuildConfig.API_BASE_URL), no leading "/".
 */

/** AuthService routes that do NOT take Bearer (HTTP client w/o authenticator) */
interface AuthPublicApi {
    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @POST("api/v1/auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): TokenResponse
}

/** AuthService routes with Bearer */
interface AuthApi {
    @GET("api/v1/auth/me")
    suspend fun me(): UserDto

    @GET("api/v1/auth/permissions")
    suspend fun permissions(): PermissionsDto

    @POST("api/v1/auth/logout")
    suspend fun logout(@Body body: LogoutRequest): Response<Unit>
}

/** SessionService (reads + discrepancy resolve for or_charge / SPD) */
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

    /** supervisor aggregate report (read-only). [from] = ISO date (YYYY-MM-DD); null = last 30 days */
    @GET("api/v1/sessions/reports/summary")
    suspend fun reportSummary(@Query("from") from: String? = null): ReportSummaryDto

    /** open resolve (spd_supervisor or or_charge). admin safe-close still on SPD portal */
    @POST("api/v1/sessions/{id}/discrepancies/{discrepancyId}/resolve")
    suspend fun resolveDiscrepancy(
        @Path("id") sessionId: String,
        @Path("discrepancyId") discrepancyId: String,
        @Body body: ResolveDiscrepancyRequest,
    ): ResolveDiscrepancyResult
}

/** CatalogService (catalog reads used to group discrepancies by category) */
interface CatalogApi {
    @GET("api/v1/catalog/families")
    suspend fun families(): List<CatalogFamilyDto>

    @GET("api/v1/catalog/categories")
    suspend fun categories(): List<CatalogCategoryDto>
}

/** EvidenceService (metadata + pre-signed URL; never Garage internal paths) */
interface EvidenceApi {
    @GET("api/v1/sessions/{id}/evidence")
    suspend fun listSessionEvidence(@Path("id") sessionId: String): List<EvidenceDto>

    @GET("api/v1/evidence/{id}")
    suspend fun getEvidence(@Path("id") evidenceId: String): EvidenceDto
}

/** NotificationsService (in-app notifs for the logged-in user) */
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
