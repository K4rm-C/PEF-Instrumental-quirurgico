package com.example.cursobasico

import com.example.cursobasico.auth.InMemoryTokenStore
import com.example.cursobasico.auth.StoredSession
import com.example.cursobasico.data.model.UserDto
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.util.concurrent.CopyOnWriteArrayList

/*
 * Fixtures SINTÉTICOS con la forma exacta de los contratos del backend (no son datos hospitalarios reales).
 */
object Fixtures {
    const val SESSION_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
    const val OPERATOR_ID = "11111111-2222-4333-8444-555555555555"

    fun user(roles: List<String> = listOf("spd_supervisor"), name: String = "Test Supervisor") = UserDto(
        id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        name = name,
        email = "supervisor@example.test",
        institutionId = "99999999-8888-4777-8666-555555555555",
        roles = roles,
    )

    fun userJson(roles: List<String> = listOf("spd_supervisor"), locale: String = "es-MX", theme: String = "dark") = """
        {"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","name":"Test Supervisor","email":"supervisor@example.test",
         "institution_id":"99999999-8888-4777-8666-555555555555",
         "ui_preferences":{"locale":"$locale","theme":"$theme"},
         "roles":[${roles.joinToString(",") { "\"$it\"" }}]}
    """.trimIndent()

    fun loginJson(access: String = "access-1", refresh: String = "refresh-1") = """
        {"access_token":"$access","refresh_token":"$refresh","token_type":"Bearer","expires_in":900,"user":${userJson()}}
    """.trimIndent()

    fun tokenJson(access: String, refresh: String) =
        """{"access_token":"$access","refresh_token":"$refresh","token_type":"Bearer","expires_in":900}"""

    fun error(error: String, message: String, correlationId: String = "corr-test-1") =
        """{"error":"$error","message":"$message","correlation_id":"$correlationId","details":{}}"""

    fun storedSession(access: String = "access-1", refresh: String = "refresh-1", roles: List<String> = listOf("spd_supervisor")) =
        StoredSession(access, refresh, Long.MAX_VALUE, user(roles))

    fun summaryJson(
        id: String = SESSION_ID,
        status: String = "in_progress",
        scheduledAt: String = "2026-11-10T15:30:00Z",
        startedAt: String? = "2026-11-10T16:02:00Z",
        openDiscrepancies: Int = 0,
        operatorId: String = OPERATOR_ID,
        operatorName: String = "Operator One",
        room: String = "OR-3",
        procedure: String = "Appendectomy",
        updatedAt: String = "2026-11-10T16:10:00Z",
    ) = """
        {"id":"$id","status":{"code":"$status","name":"$status"},"capture_mode":"vision",
         "operator":{"id":"$operatorId","name":"$operatorName"},
         "operation":{"id":"op-$id","scheduled_at":"$scheduledAt","institution_id":"inst",
           "procedure_type":{"id":"pt1","code":"APP","name":"$procedure"},
           "room":{"id":"r1","code":"$room","name":"Room"}},
         "kit":{"id":"k1","name":"Basic Kit"},"station":{"id":"s1","name":"OR-01"},
         "started_at":${startedAt?.let { "\"$it\"" } ?: "null"},"ended_at":null,"updated_at":"$updatedAt",
         "closed_by_user_id":null,"open_discrepancies":$openDiscrepancies,"has_privacy_agreement":true}
    """.trimIndent()

    fun detailJson(status: String = "in_progress") = """
        {"id":"$SESSION_ID","status":{"code":"$status","name":"$status"},"capture_mode":"vision",
         "operator":{"id":"$OPERATOR_ID","name":"Operator One"},
         "operation":{"id":"op1","scheduled_at":"2026-11-10T15:30:00Z","institution_id":"inst",
           "procedure_type":{"id":"pt1","code":"APP","name":"Appendectomy"},"room":{"id":"r1","code":"OR-3","name":"Room"}},
         "kit":{"id":"k1","name":"Basic Kit"},"station":{"id":"s1","name":"OR-01"},
         "started_at":"2026-11-10T16:02:00Z","ended_at":null,"updated_at":"2026-11-10T16:10:00Z",
         "closed_by_user_id":null,"open_discrepancies":1,"has_privacy_agreement":true,
         "tray":null,"phase":{"id":"ph1","code":"intraop","name":"Intraoperative"},
         "expected_inventory":[
           {"family_id":"f1","family_code":"KELLY","family_name":"Kelly forceps","category":null,"expected_quantity":6,"source":"kit"},
           {"family_id":"f2","family_code":"MAYO","family_name":"Mayo-Hegar needle holder","category":null,"expected_quantity":2,"source":"kit"}],
         "events":[
           {"id":"e2","occurred_at":"2026-11-10T16:06:00Z","event_code":"auto_count","event_name":"Automatic count",
            "user_id":null,"family_id":null,"expected_quantity":null,"detected_quantity":null,
            "payload":{"trigger":"periodic","board":{"totals":{"family_count":2},
              "families":[{"family_id":"f1","detected_quantity":6},{"family_id":"f2","detected_quantity":1}]}}},
           {"id":"e1","occurred_at":"2026-11-10T16:02:00Z","event_code":"session_open","event_name":"Session opened",
            "user_id":"u1","family_id":null,"expected_quantity":null,"detected_quantity":null,"payload":{}}],
         "discrepancies":[{"id":"d1","session_id":"$SESSION_ID","description":"Missing","resolved":false,"resolved_at":null,
            "expected_quantity":2,"detected_quantity":1,"family":{"id":"f2","code":"MAYO","name":"Mayo-Hegar needle holder"},
            "reason_code":"count_mismatch","origin_event_code":"auto_count"}],
         "vision_model":{"id":"m1","version_tag":"yolo-v1","sha256":null},
         "privacy":{"version":"1.0","document_uri":"x","agreed_at":"2026-11-10T15:00:00Z","purpose_quality_ops":true,
            "purpose_model_improvement":false,"agreed_by":null},
         "patient":{"id":"p1","display_name":"Synthetic Patient"},
         "surgical_team":[{"physician_id":"ph1","name":"Synthetic Surgeon","role_code":"surgeon","role_name":"Surgeon"}],
         "contingency":null,"escalation":null}
    """.trimIndent()
}

/** Despachador por ruta que registra cada petición; las respuestas se configuran por "MÉTODO ruta". */
class RoutingDispatcher : Dispatcher() {
    val handlers = mutableMapOf<String, (RecordedRequest) -> MockResponse>()
    val requests = CopyOnWriteArrayList<RecordedRequest>()

    fun on(method: String, path: String, handler: (RecordedRequest) -> MockResponse) {
        handlers["$method $path"] = handler
    }

    fun count(method: String, path: String) = requests.count { it.method == method && it.requestUrl?.encodedPath == path }

    override fun dispatch(request: RecordedRequest): MockResponse {
        requests += request
        val key = "${request.method} ${request.requestUrl?.encodedPath}"
        return handlers[key]?.invoke(request) ?: MockResponse().setResponseCode(404)
            .setBody(Fixtures.error("not_found", "Route not published by the API Gateway"))
    }
}

fun json(body: String, code: Int = 200): MockResponse =
    MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

fun MockWebServer.network(store: InMemoryTokenStore = InMemoryTokenStore(), readTimeoutMillis: Long = 30_000) =
    PefNetwork(url("/").toString(), store, debugLogging = false, readTimeoutMillis = readTimeoutMillis)

fun RecordedRequest.bearer(): String? = getHeader("Authorization")?.removePrefix("Bearer ")
