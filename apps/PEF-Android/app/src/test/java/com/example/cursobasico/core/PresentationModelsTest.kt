package com.example.cursobasico.core

import com.example.cursobasico.Fixtures
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.data.remote.NetworkModule
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneOffset

class PresentationModelsTest {
    private val json = NetworkModule.json

    private fun summaries(vararg items: String): List<SessionSummaryDto> =
        json.decodeFromString(ListSerializer(SessionSummaryDto.serializer()), "[${items.joinToString(",")}]")

    private fun detail(status: String = "in_progress"): SessionDetailDto =
        json.decodeFromString(SessionDetailDto.serializer(), Fixtures.detailJson(status))

    @Test
    fun `etiqueta de sesion coincide con la regla de NotificationsService`() {
        // dispatcher.py: "WS-" + str(session_id).replace("-", "")[:8].upper()
        assertEquals("WS-0A1B2C3D", sessionLabel("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"))
    }

    @Test
    fun `dashboard cuenta por estado y suma discrepancias abiertas`() {
        val list = summaries(
            Fixtures.summaryJson(id = "a", status = "in_progress", scheduledAt = "2026-11-10T15:00:00Z"),
            Fixtures.summaryJson(id = "b", status = "awaiting_spd_review", openDiscrepancies = 2, scheduledAt = "2026-11-09T15:00:00Z", startedAt = "2026-11-09T15:30:00Z"),
            Fixtures.summaryJson(id = "c", status = "correction_required", openDiscrepancies = 1, scheduledAt = "2026-11-09T10:00:00Z", startedAt = null),
            Fixtures.summaryJson(id = "d", status = "scheduled", scheduledAt = "2026-11-10T20:00:00Z", startedAt = null),
            Fixtures.summaryJson(id = "e", status = "closed", scheduledAt = "2026-11-01T10:00:00Z", startedAt = "2026-11-01T10:30:00Z"),
        )
        val data = computeDashboard(list, LocalDate.of(2026, 11, 10), ZoneOffset.UTC)
        assertEquals(2, data.sessionsToday)
        assertEquals(1, data.active)
        assertEquals(1, data.pendingReview)
        assertEquals(1, data.correctionRequired)
        assertEquals(1, data.scheduled)
        assertEquals(3, data.openDiscrepancies)
        assertEquals(setOf("a", "b", "c"), data.attention.map { it.id }.toSet())
    }

    @Test
    fun `dashboard sin sesiones no inventa valores`() {
        val data = computeDashboard(emptyList(), LocalDate.of(2026, 11, 10), ZoneOffset.UTC)
        assertEquals(0, data.sessionsToday + data.active + data.pendingReview + data.openDiscrepancies)
        assertTrue(data.attention.isEmpty())
    }

    @Test
    fun `busqueda y filtros locales de sesiones`() {
        val list = summaries(
            Fixtures.summaryJson(id = "a1", room = "OR-1", procedure = "Appendectomy", operatorName = "Ana"),
            Fixtures.summaryJson(id = "b2", room = "OR-2", procedure = "Cholecystectomy", operatorName = "Beto"),
        )
        assertEquals(listOf("b2"), filterSessions(list, "beto", null, null, ::sessionLabel).map { it.id })
        assertEquals(listOf("a1"), filterSessions(list, "", "OR-1", null, ::sessionLabel).map { it.id })
        assertEquals(listOf("b2"), filterSessions(list, "", null, "Cholecystectomy", ::sessionLabel).map { it.id })
        assertEquals(listOf("a1"), filterSessions(list, sessionLabel("a1"), null, null, ::sessionLabel).map { it.id })
        assertEquals(2, filterSessions(list, "", null, null, ::sessionLabel).size)
    }

    @Test
    fun `personal derivado agrupa operadores reales`() {
        val list = summaries(
            Fixtures.summaryJson(id = "a", operatorId = "op1", operatorName = "Zoe"),
            Fixtures.summaryJson(id = "b", operatorId = "op1", operatorName = "Zoe"),
            Fixtures.summaryJson(id = "c", operatorId = "op2", operatorName = "Ana"),
        )
        val operators = groupOperators(list)
        assertEquals(listOf("Ana", "Zoe"), operators.map { it.name })
        assertEquals(2, operators.first { it.id == "op1" }.sessions.size)
    }

    @Test
    fun `conteo en vivo toma el ultimo tablero auto_count`() {
        val live = detail().liveCount()
        assertEquals(listOf(6, 1), live.rows.map { it.detected })
        assertEquals(listOf(6, 2), live.rows.map { it.expected })
        assertEquals("periodic", live.trigger)
        assertEquals("2026-11-10T16:06:00Z", live.updatedAt)
    }

    @Test
    fun `conteo en vivo sin eventos deja detectado vacio`() {
        val noEvents = detail().copy(events = emptyList())
        val live = noEvents.liveCount()
        assertTrue(live.rows.all { it.detected == null })
        assertNull(live.updatedAt)
    }

    @Test
    fun `timeline en orden cronologico`() {
        val rows = detail().timelineRows()
        assertEquals(listOf("Session opened", "Automatic count"), rows.map { it.event })
    }

    @Test
    fun `discrepancias muestran lo registrado por el backend`() {
        val row = detail().discrepancyRows().single()
        assertEquals("Mayo-Hegar needle holder", row.name)
        assertEquals(-1, row.difference)
        assertEquals("count_mismatch", row.reason)
    }

    @Test
    fun `ruta de detalle segun estado real`() {
        assertTrue(sessionRouteFor("x", "in_progress") is SessionInProgressDetail)
        assertTrue(sessionRouteFor("x", "scheduled") is SessionScheduledDetail)
        assertTrue(sessionRouteFor("x", "awaiting_spd_review") is OnHoldDetail)
        assertTrue(sessionRouteFor("x", "correction_required") is OnHoldDetail)
        assertTrue(sessionRouteFor("x", "closed") is OnHoldDetail)
    }

    @Test
    fun `nota de escalacion usa las claves reales del backend`() {
        val escalated = json.decodeFromString(
            SessionDetailDto.serializer(),
            Fixtures.detailJson("correction_required").replace("\"escalation\":null", "\"escalation\":{\"kind\":\"missing\",\"notes\":\"Tray rechecked\"}")
        )
        assertEquals("missing: Tray rechecked", escalated.resolutionNote())
        assertNull(detail().resolutionNote())
    }
}
