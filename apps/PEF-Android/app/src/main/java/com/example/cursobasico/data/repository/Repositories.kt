package com.example.cursobasico.data.repository

import com.example.cursobasico.data.model.EvidenceDto
import com.example.cursobasico.data.model.NotificationDto
import com.example.cursobasico.data.model.NotificationPageDto
import com.example.cursobasico.data.model.ReadAllResultDto
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError
import com.example.cursobasico.data.remote.EvidenceApi
import com.example.cursobasico.data.remote.NotificationsApi
import com.example.cursobasico.data.remote.SessionApi
import com.example.cursobasico.data.remote.httpStatusToError
import com.example.cursobasico.data.remote.safeApiCall
import com.example.cursobasico.data.remote.toAppError
import com.example.cursobasico.core.DiscrepancyCharts
import com.example.cursobasico.core.buildDiscrepancyCharts
import com.example.cursobasico.data.model.CatalogCategoryDto
import com.example.cursobasico.data.model.CatalogFamilyDto
import com.example.cursobasico.data.remote.CatalogApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/** Lecturas de SessionService. Ninguna escritura: la app no cambia estados ni cierra sesiones. */
class SessionRepository(private val api: SessionApi) {
    /** [statuses] vacío = todas; se envía como `status=a,b` (filtro del backend). */
    suspend fun listSessions(statuses: Collection<String> = emptyList()): ApiResult<List<SessionSummaryDto>> =
        safeApiCall { api.listSessions(status = statuses.takeIf { it.isNotEmpty() }?.joinToString(",")) }

    suspend fun getSession(sessionId: String): ApiResult<SessionDetailDto> =
        safeApiCall { api.getSession(sessionId) }
}

/**
 * EvidenceService. La imagen se descarga SOLO con la URL prefirmada que entrega el backend, con un cliente sin
 * Bearer (el JWT nunca viaja a Garage), sin caché en disco y con límite de tamaño; vive solo en memoria.
 */
class EvidenceRepository(
    private val api: EvidenceApi,
    private val imageClient: OkHttpClient,
    private val maxImageBytes: Long = 15L * 1024 * 1024,
) {
    suspend fun listForSession(sessionId: String): ApiResult<List<EvidenceDto>> =
        safeApiCall { api.listSessionEvidence(sessionId) }

    /** Metadatos + URL prefirmada (cada emisión queda auditada por el backend). */
    suspend fun getSigned(evidenceId: String): ApiResult<EvidenceDto> = safeApiCall { api.getEvidence(evidenceId) }

    suspend fun downloadImage(signedUrl: String): ApiResult<ByteArray> = withContext(Dispatchers.IO) {
        try {
            imageClient.newCall(Request.Builder().url(signedUrl).get().build()).execute().use { response ->
                val body = response.body
                when {
                    !response.isSuccessful || body == null ->
                        ApiResult.Failure(httpStatusToError(response.code, null, null))
                    body.contentLength() > maxImageBytes ->
                        ApiResult.Failure(AppError.BadRequest("Evidence image too large", null))
                    else -> {
                        val bytes = body.byteStream().use { it.readNBytesCompat(maxImageBytes + 1) }
                        if (bytes.size > maxImageBytes) ApiResult.Failure(AppError.BadRequest("Evidence image too large", null))
                        else ApiResult.Success(bytes)
                    }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            ApiResult.Failure(e.toAppError())
        }
    }
}

private fun java.io.InputStream.readNBytesCompat(limit: Long): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    var total = 0L
    while (total < limit) {
        val read = read(buffer, 0, minOf(buffer.size.toLong(), limit - total).toInt())
        if (read < 0) break
        out.write(buffer, 0, read)
        total += read
    }
    return out.toByteArray()
}

/** NotificationsService. Mantiene el contador de no leídas que muestra la campana superior. */
class NotificationRepository(private val api: NotificationsApi) {
    private val _unread = MutableStateFlow<Int?>(null)
    val unread: StateFlow<Int?> = _unread.asStateFlow()

    suspend fun list(status: String? = null, type: String? = null, from: String? = null, page: Int = 1):
        ApiResult<NotificationPageDto> {
        val result = safeApiCall { api.list(status = status, type = type, from = from, page = page) }
        if (result is ApiResult.Success) _unread.value = result.data.unread
        return result
    }

    suspend fun refreshUnreadCount(): ApiResult<Int> {
        val result = safeApiCall { api.unreadCount().unread }
        if (result is ApiResult.Success) _unread.value = result.data
        return result
    }

    suspend fun markRead(notificationId: String): ApiResult<NotificationDto> {
        val result = safeApiCall { api.markRead(notificationId) }
        if (result is ApiResult.Success) refreshUnreadCount()
        return result
    }

    suspend fun markAllRead(): ApiResult<ReadAllResultDto> {
        val result = safeApiCall { api.markAllRead() }
        if (result is ApiResult.Success) _unread.value = result.data.unread
        return result
    }

    fun clear() {
        _unread.value = null
    }
}

/**
 * Estadísticas de discrepancias del Dashboard: reporte del supervisor + catálogo (familias y categorías) en paralelo.
 * El catálogo cambia poco: se conserva en memoria tras la primera lectura correcta.
 */
class DiscrepancyStatsRepository(
    private val sessions: SessionApi,
    private val catalog: CatalogApi,
) {
    @Volatile
    private var cachedCatalog: Pair<List<CatalogFamilyDto>,
        List<CatalogCategoryDto>>? = null

    /** [from] = fecha ISO de inicio (YYYY-MM-DD), interpretada por el backend como día completo. */
    suspend fun load(from: String?): ApiResult<DiscrepancyCharts> = safeApiCall {
        coroutineScope {
            val summary = async { sessions.reportSummary(from) }
            val catalogData = cachedCatalog ?: run {
                val families = async { catalog.families() }
                val categories = async { catalog.categories() }
                (families.await() to categories.await()).also { cachedCatalog = it }
            }
            buildDiscrepancyCharts(summary.await(), catalogData.first, catalogData.second)
        }
    }

    fun clear() {
        cachedCatalog = null
    }
}
