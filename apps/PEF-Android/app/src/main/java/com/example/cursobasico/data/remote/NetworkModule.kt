package com.example.cursobasico.data.remote

import android.util.Log
import com.example.cursobasico.auth.SessionManager
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.UUID
import java.util.concurrent.TimeUnit

object NetworkModule {
    /** Tolerante a campos nuevos del backend; un tipo incompatible sigue siendo error (InvalidResponse). */
    val json: Json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        coerceInputValues = true
    }

    fun baseClient(debugLogging: Boolean, readTimeoutMillis: Long = 30_000): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(readTimeoutMillis, TimeUnit.MILLISECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(readTimeoutMillis + 15_000, TimeUnit.MILLISECONDS)
        .addInterceptor(DefaultHeadersInterceptor(debugLogging))
        .build()

    fun retrofit(baseUrl: String, client: OkHttpClient): Retrofit = Retrofit.Builder()
        .baseUrl(baseUrl)
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
}

/**
 * Cabeceras comunes: JSON (DS09: Accept application/json), X-Correlation-ID propio por petición.
 * El registro de depuración solo incluye método, ruta, estado y correlation id: nunca cabeceras ni cuerpos
 * (los JWT y las contraseñas jamás llegan a Logcat).
 */
class DefaultHeadersInterceptor(private val debugLogging: Boolean) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val correlationId = UUID.randomUUID().toString()
        val request = chain.request().newBuilder()
            .header("Accept", "application/json")
            .header("X-Correlation-ID", correlationId)
            .build()
        val response = chain.proceed(request)
        if (debugLogging) {
            try {
                Log.d("PefHttp", "${request.method} ${request.url.encodedPath} -> ${response.code} cid=$correlationId")
            } catch (_: RuntimeException) {
                // android.util.Log no existe en pruebas JVM.
            }
        }
        return response
    }
}

/** Añade el Bearer vigente a las rutas protegidas. */
class AuthInterceptor(private val sessionManager: SessionManager) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val token = sessionManager.currentAccessToken()
        if (token == null || request.header("Authorization") != null) return chain.proceed(request)
        return chain.proceed(request.newBuilder().header("Authorization", "Bearer $token").build())
    }
}

/**
 * Ante un 401 renueva el access token UNA vez (con exclusión mutua en [SessionManager.refreshTokens]) y reintenta.
 * Si el refresh falla devuelve null: OkHttp entrega el 401 original y la app pasa a "sesión expirada".
 */
class TokenAuthenticator(private val sessionManager: SessionManager) : Authenticator {
    override fun authenticate(route: Route?, response: Response): Request? {
        if (priorResponseCount(response) >= 2) return null
        // El logout gestiona su propio 401 (SessionManager.logout): su cuerpo lleva el refresh token.
        if (response.request.url.encodedPath.endsWith("/auth/logout")) return null
        val failedToken = response.request.header("Authorization")?.removePrefix("Bearer ")?.trim() ?: return null
        val newToken = runBlocking { sessionManager.refreshTokens(failedToken) } ?: return null
        if (newToken == failedToken) return null
        return response.request.newBuilder().header("Authorization", "Bearer $newToken").build()
    }

    private fun priorResponseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) {
            count++
            prior = prior.priorResponse
        }
        return count
    }
}
