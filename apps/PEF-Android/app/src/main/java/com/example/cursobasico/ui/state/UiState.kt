package com.example.cursobasico.ui.state

import com.example.cursobasico.data.remote.ApiResult
import com.example.cursobasico.data.remote.AppError

/**
 * screen state fed by the backend.
 * Unauthorized / Offline come from [AppError] (see [UiState.Error.kind]).
 */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Success<T>(val data: T, val refreshing: Boolean = false) : UiState<T>
    data object Empty : UiState<Nothing>
    data class Error(val error: AppError) : UiState<Nothing> {
        val kind: ErrorKind get() = error.kind()
    }
    /** user role cant see this (decided before hitting backend) */
    data object NotPermitted : UiState<Nothing>
}

enum class ErrorKind { OFFLINE, TIMEOUT, UNAUTHORIZED, FORBIDDEN, NOT_FOUND, RATE_LIMITED, SERVER, INVALID_RESPONSE, BAD_REQUEST, UNKNOWN }

fun AppError.kind(): ErrorKind = when (this) {
    AppError.Network -> ErrorKind.OFFLINE
    AppError.Timeout -> ErrorKind.TIMEOUT
    is AppError.Unauthorized -> ErrorKind.UNAUTHORIZED
    is AppError.Forbidden -> ErrorKind.FORBIDDEN
    is AppError.NotFound -> ErrorKind.NOT_FOUND
    is AppError.RateLimited -> ErrorKind.RATE_LIMITED
    is AppError.Server -> ErrorKind.SERVER
    AppError.InvalidResponse -> ErrorKind.INVALID_RESPONSE
    is AppError.BadRequest -> ErrorKind.BAD_REQUEST
    is AppError.Unexpected -> ErrorKind.UNKNOWN
}

/** remote result -> UiState; [isEmpty] picks Empty */
fun <T> ApiResult<T>.toUiState(isEmpty: (T) -> Boolean = { false }): UiState<T> = when (this) {
    is ApiResult.Success -> if (isEmpty(data)) UiState.Empty else UiState.Success(data)
    is ApiResult.Failure -> UiState.Error(error)
}

fun <T> UiState<T>.dataOrNull(): T? = (this as? UiState.Success)?.data
