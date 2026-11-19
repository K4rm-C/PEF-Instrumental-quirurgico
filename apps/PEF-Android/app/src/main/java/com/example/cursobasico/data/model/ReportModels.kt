package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * SessionService supervisor report (apps/SessionService/services/session_reports.py -> summary),
 * only fields the app uses. published: GET /api/v1/sessions/reports/summary?from=YYYY-MM-DD
 * (or_charge / spd_supervisor; other roles get 403). read-only aggregates from backend.
 */
@Serializable
data class ReportSummaryDto(
    val filters: ReportFiltersDto? = null,
    val discrepancies: DiscrepancyStatsDto = DiscrepancyStatsDto(),
)

@Serializable
data class ReportFiltersDto(val from: String? = null, val to: String? = null)

@Serializable
data class DiscrepancyStatsDto(
    val total: Int = 0,
    val open: Int = 0,
    val resolved: Int = 0,
    @SerialName("by_family") val byFamily: List<FamilyDiscrepancyDto> = emptyList(),
)

/** discrepancies by instrument family; family_id/name null = no family attached */
@Serializable
data class FamilyDiscrepancyDto(
    @SerialName("family_id") val familyId: String? = null,
    val code: String? = null,
    val name: String? = null,
    val total: Int = 0,
    val open: Int = 0,
)

/*
 * CatalogService: GET /api/v1/catalog/families + /categories (any authed user).
 * used to group families by category on the charts.
 */
@Serializable
data class CatalogFamilyDto(
    val id: String,
    val code: String? = null,
    val name: String? = null,
    @SerialName("category_code") val categoryCode: String? = null,
)

@Serializable
data class CatalogCategoryDto(val id: String, val code: String, val name: String? = null)
