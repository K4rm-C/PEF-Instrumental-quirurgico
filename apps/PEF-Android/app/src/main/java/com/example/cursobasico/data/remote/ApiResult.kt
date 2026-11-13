package com.example.cursobasico.data.remote

import com.example.cursobasico.data.model.ApiErrorBody
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import retrofit2.HttpException
import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/** Resultado de una llamada remota: nunca lanza excepciones a la interfaz. */
sealed interface ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>
    data class Failure(val error: AppError) : ApiResult<Nothing>
}

/**
 * Errores normalizados. `serverMessage` es el texto del sobre DS09 del backend (en inglés): se muestra solo
 * como detalle; el mensaje principal sale de los recursos de cadenas de la app (ui/state/ErrorMessages.kt).
 */
sealed class AppError(open val serverMessage: String? = null, open val correlationId: String? = null) {
    /** Sin conexión o Gateway inalcanzable. */
    data object Network : AppError()
    data object Timeout : AppError()
    data class BadRequest(override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    data class Unauthorized(override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    data class Forbidden(override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    data class NotFound(override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    data class RateLimited(override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    data class Server(val status: Int, override val serverMessage: String?, override val correlationId: String?) :
        AppError(serverMessage, correlationId)
    /** Respuesta con un JSON que no cumple el contrato esperado. */
    data object InvalidResponse : AppError()
    data class Unexpected(val status: Int?) : AppError()
}

private val errorJson = Json { ignoreUnknownKeys = true; isLenient = true; explicitNulls = false }

fun HttpException.toAppError(): AppError {
    val raw = try {
        response()?.errorBody()?.string()
    } catch (_: IOException) {
        null
    }
    val correlationId = response()?.headers()?.get("X-Correlation-ID")
    val body = raw?.let {
        try {
            errorJson.decodeFromString(ApiErrorBody.serializer(), it)
        } catch (_: Exception) {
            null
        }
    }
    return httpStatusToError(code(), body?.message, body?.correlationId ?: correlationId)
}

fun httpStatusToError(status: Int, message: String?, correlationId: String?): AppError = when (status) {
    400, 409, 410, 413, 422 -> AppError.BadRequest(message, correlationId)
    401 -> AppError.Unauthorized(message, correlationId)
    403 -> AppError.Forbidden(message, correlationId)
    404 -> AppError.NotFound(message, correlationId)
    429 -> AppError.RateLimited(message, correlationId)
    in 500..599 -> AppError.Server(status, message, correlationId)
    else -> AppError.Unexpected(status)
}

fun Throwable.toAppError(): AppError = when (this) {
    is HttpException -> toAppError()
    is SocketTimeoutException -> AppError.Timeout
    is InterruptedIOException -> AppError.Timeout
    is UnknownHostException, is ConnectException, is NoRouteToHostException -> AppError.Network
    is IOException -> AppError.Network
    is SerializationException, is IllegalArgumentException -> AppError.InvalidResponse
    else -> AppError.Unexpected(null)
}

/** Ejecuta una llamada Retrofit y convierte cualquier fallo en [ApiResult.Failure]. */
suspend fun <T> safeApiCall(block: suspend () -> T): ApiResult<T> = try {
    ApiResult.Success(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Throwable) {
    ApiResult.Failure(e.toAppError())
}

inline fun <T, R> ApiResult<T>.map(transform: (T) -> R): ApiResult<R> = when (this) {
    is ApiResult.Success -> ApiResult.Success(transform(data))
    is ApiResult.Failure -> this
}
