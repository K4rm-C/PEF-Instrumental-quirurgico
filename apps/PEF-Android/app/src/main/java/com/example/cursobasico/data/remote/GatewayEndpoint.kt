package com.example.cursobasico.data.remote

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.TimeUnit

private val Context.gatewayEndpointStore: DataStore<Preferences> by preferencesDataStore(name = "pef_gateway_endpoint")

/** URL activa del API Gateway; OkHttp la aplica en cada petición (sin recrear Retrofit). */
class BaseUrlHolder(initial: String) {
    private val ref = AtomicReference(normalizeGatewayBaseUrl(initial))

    fun get(): String = ref.get()

    fun httpUrl(): HttpUrl = requireNotNull(ref.get().toHttpUrlOrNull()) { "Invalid gateway base URL" }

    fun set(url: String) {
        ref.set(normalizeGatewayBaseUrl(url))
    }
}

/** Reescribe scheme/host/port de cada request al Gateway activo. */
class BaseUrlInterceptor(private val holder: BaseUrlHolder) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val target = holder.httpUrl()
        val rewritten = original.url.newBuilder()
            .scheme(target.scheme)
            .host(target.host)
            .port(target.port)
            .build()
        return chain.proceed(original.newBuilder().url(rewritten).build())
    }
}

class GatewayEndpointStore(context: Context) {
    private val store = context.applicationContext.gatewayEndpointStore
    private val key = stringPreferencesKey("gateway_base_url")

    suspend fun read(): String? = store.data.map { it[key] }.first()

    suspend fun save(url: String) {
        val normalized = normalizeGatewayBaseUrl(url)
        store.edit { it[key] = normalized }
    }
}

/**
 * Localiza el API Gateway sin rebuild: última URL buena → BuildConfig → emulador → barrido /24.
 * Solo se usa en debug; release sigue en la URL HTTPS fija de BuildConfig.
 */
object GatewayLocator {
    private const val GATEWAY_MARK = "pef-gateway"
    private const val HEALTH = "/healthz"

    suspend fun discover(
        store: GatewayEndpointStore,
        buildConfigUrl: String,
        probeClient: OkHttpClient? = null,
    ): String? = withContext(Dispatchers.IO) {
        val client = probeClient ?: OkHttpClient.Builder()
            .connectTimeout(400, TimeUnit.MILLISECONDS)
            .readTimeout(600, TimeUnit.MILLISECONDS)
            .callTimeout(900, TimeUnit.MILLISECONDS)
            .build()

        val candidates = linkedSetOf<String>()
        store.read()?.let { candidates += normalizeGatewayBaseUrl(it) }
        candidates += normalizeGatewayBaseUrl(buildConfigUrl)
        candidates += "http://10.0.2.2:8080/"
        candidates += "http://127.0.0.1:8080/"

        for (url in candidates) {
            if (probeHealth(client, url)) return@withContext url
        }

        for (prefix in localIpv4Prefixes()) {
            val hit = scanSubnet(client, prefix)
            if (hit != null) return@withContext hit
        }
        null
    }

    private fun localIpv4Prefixes(): List<String> {
        val prefixes = linkedSetOf<String>()
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return emptyList()
            for (nic in interfaces) {
                if (!nic.isUp || nic.isLoopback) continue
                for (addr in nic.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        val parts = host.split('.')
                        if (parts.size == 4) prefixes += "${parts[0]}.${parts[1]}.${parts[2]}"
                    }
                }
            }
        } catch (_: Exception) {
            // Sin interfaces: solo quedan candidatos fijos.
        }
        // Emulator host-side LAN is invisible as 10.0.2.2; keep empty ok.
        return prefixes.toList()
    }

    private suspend fun scanSubnet(client: OkHttpClient, prefix: String): String? = coroutineScope {
        val gate = Semaphore(48)
        val jobs = (1..254).map { last ->
            async {
                gate.withPermit {
                    val url = "http://$prefix.$last:8080/"
                    if (probeHealth(client, url)) url else null
                }
            }
        }
        jobs.awaitAll().firstOrNull { it != null }
    }

    fun probeHealth(client: OkHttpClient, baseUrl: String): Boolean {
        val url = normalizeGatewayBaseUrl(baseUrl).trimEnd('/') + HEALTH
        return try {
            val request = Request.Builder().url(url).get().header("Accept", "application/json").build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return false
                val body = response.body?.string().orEmpty()
                body.contains(GATEWAY_MARK)
            }
        } catch (_: Exception) {
            false
        }
    }
}

fun normalizeGatewayBaseUrl(raw: String): String {
    var s = raw.trim()
    if (s.isEmpty()) s = "http://127.0.0.1:8080/"
    if (!s.endsWith("/")) s += "/"
    requireNotNull(s.toHttpUrlOrNull()) { "Invalid gateway URL: $raw" }
    return s
}
