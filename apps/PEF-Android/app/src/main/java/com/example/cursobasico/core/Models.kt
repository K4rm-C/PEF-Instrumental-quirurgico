package com.example.cursobasico.core

import com.example.cursobasico.data.model.CatalogCategoryDto
import com.example.cursobasico.data.model.CatalogFamilyDto
import com.example.cursobasico.data.model.ReportSummaryDto
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.ui.components.isSameLocalDay
import com.example.cursobasico.ui.components.parseInstant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import java.time.LocalDate
import java.time.ZoneId

/*
 * ANDROID-01: presentation models from REAL backend responses only.
 * demo junk that lived here (WS-026, "Operator Demo", etc) is gone.
 * no fn messes with count rules: just read what SessionService already computed.
 */

data class DashboardData(
    val sessionsToday: Int,
    val active: Int,
    val pendingReview: Int,
    val correctionRequired: Int,
    val scheduled: Int,
    val openDiscrepancies: Int,
    /** in progress or waiting review/correction, newest first */
    val attention: List<SessionSummaryDto>,
)

fun computeDashboard(
    sessions: List<SessionSummaryDto>,
    today: LocalDate = LocalDate.now(),
    zone: ZoneId = ZoneId.systemDefault(),
): DashboardData = DashboardData(
    sessionsToday = sessions.count {
        isSameLocalDay(it.operation?.scheduledAt, today, zone) || isSameLocalDay(it.startedAt, today, zone)
    },
    active = sessions.count { it.status.code == SessionStatus.IN_PROGRESS },
    pendingReview = sessions.count { it.status.code == SessionStatus.AWAITING_SPD_REVIEW },
    correctionRequired = sessions.count { it.status.code == SessionStatus.CORRECTION_REQUIRED },
    scheduled = sessions.count { it.status.code == SessionStatus.SCHEDULED },
    openDiscrepancies = sessions.sumOf { it.openDiscrepancies },
    attention = sessions
        .filter { it.status.code == SessionStatus.IN_PROGRESS || it.status.code in SessionStatus.ON_HOLD }
        .sortedByDescending { parseInstant(it.updatedAt ?: it.startedAt ?: it.operation?.scheduledAt) },
)

/** procedure + OR text like original design ("Appendectomy - OR-3") */
fun SessionSummaryDto.procedureRoomText(): String = listOfNotNull(
    operation?.procedureType?.name ?: operation?.procedureType?.code,
    operation?.room?.code ?: operation?.room?.name,
).joinToString(" - ").ifBlank { "—" }

fun SessionDetailDto.procedureRoomText(): String = listOfNotNull(
    operation?.procedureType?.name ?: operation?.procedureType?.code,
    operation?.room?.code ?: operation?.room?.name,
).joinToString(" - ").ifBlank { "—" }

/** local search/filters on session list (status filter is backend's job) */
fun filterSessions(
    sessions: List<SessionSummaryDto>,
    query: String,
    room: String?,
    procedure: String?,
    label: (String) -> String,
): List<SessionSummaryDto> = sessions.filter { s ->
    val q = query.trim()
    val matchesQuery = q.isEmpty() || listOfNotNull(
        label(s.id), s.operator?.name, s.operation?.procedureType?.name, s.operation?.room?.code, s.kit?.name,
    ).any { it.contains(q, ignoreCase = true) }
    val matchesRoom = room == null || s.operation?.room?.code == room
    val matchesProcedure = procedure == null || s.operation?.procedureType?.name == procedure
    matchesQuery && matchesRoom && matchesProcedure
}

data class OperatorSummary(val id: String, val name: String, val sessions: List<SessionSummaryDto>)

/** derived personnel: operators on sessions the user can ALREADY see */
fun groupOperators(sessions: List<SessionSummaryDto>): List<OperatorSummary> = sessions
    .filter { it.operator != null }
    .groupBy { it.operator!!.id }
    .map { (id, items) -> OperatorSummary(id, items.first().operator?.name ?: "—", items) }
    .sortedBy { it.name.lowercase() }

data class InventoryRow(val familyId: String, val name: String, val expected: Int)

fun SessionDetailDto.inventoryRows(): List<InventoryRow> = expectedInventory.map {
    InventoryRow(it.familyId, it.familyName ?: it.familyCode ?: it.familyId, it.expectedQuantity)
}

data class LiveCountRow(val name: String, val expected: Int, val detected: Int?)
data class LiveCount(val rows: List<LiveCountRow>, val updatedAt: String?, val trigger: String?)

/**
 * latest YOLO count on record (auto_count event). prefers board (payload.board.families) from newest
 * event that has it; else last auto_count per family. Detected = null if nothing recorded.
 */
fun SessionDetailDto.liveCount(): LiveCount {
    val autoCounts = events.filter { it.eventCode == "auto_count" }.sortedBy { parseInstant(it.occurredAt) }
    val board = autoCounts.lastOrNull { (it.payload?.get("board") as? JsonObject)?.get("families") is JsonArray }
    val detectedByFamily: Map<String, Int?> = if (board != null) {
        ((board.payload!!["board"] as JsonObject)["families"] as JsonArray).mapNotNull { element ->
            val item = element as? JsonObject ?: return@mapNotNull null
            val familyId = (item["family_id"] as? JsonPrimitive)?.contentOrNull ?: return@mapNotNull null
            familyId to (item["detected_quantity"] as? JsonPrimitive)?.intOrNull
        }.toMap()
    } else {
        autoCounts.filter { it.familyId != null }.associate { it.familyId!! to it.detectedQuantity }
    }
    val latest = board ?: autoCounts.lastOrNull()
    return LiveCount(
        rows = inventoryRows().map { LiveCountRow(it.name, it.expected, detectedByFamily[it.familyId]) },
        updatedAt = latest?.occurredAt,
        trigger = (latest?.payload?.get("trigger") as? JsonPrimitive)?.contentOrNull,
    )
}

data class TimelineRow(val occurredAt: String?, val event: String, val scope: String, val expected: String, val detected: String)

fun SessionDetailDto.timelineRows(): List<TimelineRow> {
    val familyNames = expectedInventory.associate { it.familyId to (it.familyName ?: it.familyCode ?: it.familyId) }
    return events.sortedBy { parseInstant(it.occurredAt) }.map { e ->
        val scope = (e.payload?.get("scope") as? JsonPrimitive)?.contentOrNull
            ?: e.familyId?.let { familyNames[it] ?: it }
        TimelineRow(
            occurredAt = e.occurredAt,
            event = e.eventName ?: e.eventCode,
            scope = scope ?: "—",
            expected = e.expectedQuantity?.toString() ?: "—",
            detected = e.detectedQuantity?.toString() ?: "—",
        )
    }
}

data class DiscrepancyRow(
    val name: String,
    val expected: Int?,
    val detected: Int?,
    val reason: String?,
    val resolved: Boolean,
) {
    /** informative detected - expected as backend stored it (not a recount) */
    val difference: Int? get() = if (expected != null && detected != null) detected - expected else null
}

fun SessionDetailDto.discrepancyRows(): List<DiscrepancyRow> = discrepancies.map {
    DiscrepancyRow(
        name = it.family?.name ?: it.family?.code ?: it.description ?: "—",
        expected = it.expectedQuantity,
        detected = it.detectedQuantity,
        reason = it.reasonCode,
        resolved = it.resolved,
    )
}

/**
 * Operator written report (A1): prefer `discrepancies[].description` (free note),
 * skip auto Shortfall/Extra templates. escalation / contingency as fallback.
 */
fun SessionDetailDto.resolutionNote(): String? {
    fun JsonObject.text(key: String) = (this[key] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
    fun isAutoTemplate(text: String) =
        text.startsWith("Shortfall on ") || text.startsWith("Extra on ") || text.startsWith("SPD resolution (")

    val freeText = discrepancies.mapNotNull { it.description?.trim()?.takeIf { d -> d.isNotEmpty() && !isAutoTemplate(d) } }
    if (freeText.isNotEmpty()) return freeText.joinToString("\n")

    escalation?.let { e -> return listOfNotNull(e.text("kind"), e.text("notes")).joinToString(": ").ifBlank { null } }
    contingency?.let { c -> return listOfNotNull(c.text("reason_code"), c.text("notes")).joinToString(": ").ifBlank { null } }

    val autoDesc = discrepancies.mapNotNull { it.description?.trim()?.takeIf { d -> d.isNotEmpty() } }
    if (autoDesc.isNotEmpty()) return autoDesc.joinToString("\n")
    return null
}

/* ------------------------------------------------------------------ Discrepancies (Dashboard tab) */

/** bar chart row. [label] null = no instrument/category; [isOther] = leftover bucket */
data class ChartRow(val label: String?, val count: Int, val isOther: Boolean = false)

data class DiscrepancyCharts(
    val total: Int,
    val open: Int,
    val byInstrument: List<ChartRow>,
    val byFamily: List<ChartRow>,
    val from: String?,
    val to: String?,
)

/**
 * group totals SessionService ALREADY computed (reports/summary -> discrepancies.by_family):
 * - "by instrument": each catalog instrument family (eg "Mayo-Hegar needle holder")
 * - "by family": that family's category in CatalogService (eg "Grasping")
 * just sum+sort; no recount / reinterpret. if more than [maxRows] groups, show top
 * (maxRows - 1) and dump the rest into "Other".
 */
fun buildDiscrepancyCharts(
    summary: ReportSummaryDto,
    families: List<CatalogFamilyDto>,
    categories: List<CatalogCategoryDto>,
    maxRows: Int = 4,
): DiscrepancyCharts {
    val stats = summary.discrepancies
    val familyCategory = families.associate { it.id to it.categoryCode }
    val categoryName = categories.associate { it.code to (it.name ?: it.code) }
    val byInstrument = stats.byFamily
        .filter { it.total > 0 }
        .groupBy { it.name ?: it.code }
        .map { (label, rows) -> label to rows.sumOf { it.total } }
    val byCategory = stats.byFamily
        .filter { it.total > 0 }
        .groupBy { row -> row.familyId?.let { familyCategory[it] }?.let { code -> categoryName[code] ?: code } }
        .map { (label, rows) -> label to rows.sumOf { it.total } }
    return DiscrepancyCharts(
        total = stats.total,
        open = stats.open,
        byInstrument = topWithOther(byInstrument, maxRows),
        byFamily = topWithOther(byCategory, maxRows),
        from = summary.filters?.from,
        to = summary.filters?.to,
    )
}

fun topWithOther(rows: List<Pair<String?, Int>>, maxRows: Int): List<ChartRow> {
    val sorted = rows.sortedWith(compareByDescending<Pair<String?, Int>> { it.second }.thenBy { it.first == null }
        .thenBy { it.first?.lowercase() })
    if (sorted.size <= maxRows) return sorted.map { ChartRow(it.first, it.second) }
    val head = sorted.take(maxRows - 1).map { ChartRow(it.first, it.second) }
    return head + ChartRow(null, sorted.drop(maxRows - 1).sumOf { it.second }, isOther = true)
}
