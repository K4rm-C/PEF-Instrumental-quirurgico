package com.example.cursobasico.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Reporte de supervisor de SessionService (apps/SessionService/services/session_reports.py -> summary), solo los
 * campos que usa la app. Ruta publicada: GET /api/v1/sessions/reports/summary?from=YYYY-MM-DD (spd_supervisor; el
 * resto de roles recibe 403). Agregados de solo lectura calculados por el backend.
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

/** Discrepancias por familia de instrumento; family_id/name null = discrepancia sin familia asociada. */
@Serializable
data class FamilyDiscrepancyDto(
    @SerialName("family_id") val familyId: String? = null,
    val code: String? = null,
    val name: String? = null,
    val total: Int = 0,
    val open: Int = 0,
)

/*
 * CatalogService (apps/CatalogService): GET /api/v1/catalog/families y GET /api/v1/catalog/categories
 * (cualquier usuario autenticado). Se usan para agrupar las familias por su categoría.
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
