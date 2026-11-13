package com.example.cursobasico.core

import com.example.cursobasico.data.model.CatalogCategoryDto
import com.example.cursobasico.data.model.CatalogFamilyDto
import com.example.cursobasico.data.model.DiscrepancyStatsDto
import com.example.cursobasico.data.model.FamilyDiscrepancyDto
import com.example.cursobasico.data.model.ReportFiltersDto
import com.example.cursobasico.data.model.ReportSummaryDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DiscrepancyChartsTest {
    private val families = listOf(
        CatalogFamilyDto("f-mayo", "MAYO", "Mayo-Hegar Needle Holder", "suturing"),
        CatalogFamilyDto("f-allis", "ALLIS", "Allis Tissue Forceps", "grasping"),
        CatalogFamilyDto("f-foerster", "FOERSTER", "Foerster Sponge Forceps", "grasping"),
        CatalogFamilyDto("f-metz", "METZ", "Metzenbaum Scissors", "cutting"),
        CatalogFamilyDto("f-kelly", "KELLY", "Kelly Forceps", "hemostasis"),
    )
    private val categories = listOf(
        CatalogCategoryDto("c1", "suturing", "Needle Holders"),
        CatalogCategoryDto("c2", "grasping", "Forceps"),
        CatalogCategoryDto("c3", "cutting", "Scissors"),
        CatalogCategoryDto("c4", "hemostasis", "Hemostats"),
    )

    private fun summary(vararg rows: FamilyDiscrepancyDto) = ReportSummaryDto(
        filters = ReportFiltersDto("2026-08-13T00:00:00Z", "2026-11-11T00:00:00Z"),
        discrepancies = DiscrepancyStatsDto(total = rows.sumOf { it.total }, open = rows.sumOf { it.open }, byFamily = rows.toList()),
    )

    @Test
    fun `por instrumento ordena y agrupa el resto en Other`() {
        val charts = buildDiscrepancyCharts(
            summary(
                FamilyDiscrepancyDto("f-mayo", "MAYO", "Mayo-Hegar Needle Holder", 3, 2),
                FamilyDiscrepancyDto("f-allis", "ALLIS", "Allis Tissue Forceps", 2, 1),
                FamilyDiscrepancyDto("f-foerster", "FOERSTER", "Foerster Sponge Forceps", 1, 0),
                FamilyDiscrepancyDto("f-metz", "METZ", "Metzenbaum Scissors", 1, 1),
                FamilyDiscrepancyDto("f-kelly", "KELLY", "Kelly Forceps", 1, 0),
            ),
            families, categories,
        )
        assertEquals(8, charts.total)
        assertEquals(4, charts.open)
        assertEquals(listOf("Mayo-Hegar Needle Holder", "Allis Tissue Forceps", "Foerster Sponge Forceps", null),
            charts.byInstrument.map { it.label })
        assertEquals(listOf(3, 2, 1, 2), charts.byInstrument.map { it.count })
        assertTrue(charts.byInstrument.last().isOther)
    }

    @Test
    fun `por familia agrupa por la categoria del catalogo`() {
        val charts = buildDiscrepancyCharts(
            summary(
                FamilyDiscrepancyDto("f-mayo", "MAYO", "Mayo-Hegar Needle Holder", 4, 0),
                FamilyDiscrepancyDto("f-allis", "ALLIS", "Allis Tissue Forceps", 2, 0),
                FamilyDiscrepancyDto("f-foerster", "FOERSTER", "Foerster Sponge Forceps", 1, 0),
                FamilyDiscrepancyDto("f-metz", "METZ", "Metzenbaum Scissors", 1, 0),
            ),
            families, categories,
        )
        assertEquals(listOf("Needle Holders", "Forceps", "Scissors"), charts.byFamily.map { it.label })
        assertEquals(listOf(4, 3, 1), charts.byFamily.map { it.count })
        assertEquals(charts.byInstrument.sumOf { it.count }, charts.byFamily.sumOf { it.count })
    }

    @Test
    fun `discrepancias sin familia o fuera del catalogo quedan como no especificadas`() {
        val charts = buildDiscrepancyCharts(
            summary(
                FamilyDiscrepancyDto(null, null, null, 2, 2),
                FamilyDiscrepancyDto("f-desconocida", "X", "Unknown family", 1, 0),
            ),
            families, categories,
        )
        assertNull(charts.byInstrument.first { it.count == 2 }.label)
        assertEquals(listOf(ChartRow(null, 3)), charts.byFamily)
    }

    @Test
    fun `sin discrepancias no inventa filas`() {
        val charts = buildDiscrepancyCharts(summary(), families, categories)
        assertEquals(0, charts.total)
        assertTrue(charts.byInstrument.isEmpty() && charts.byFamily.isEmpty())
    }

    @Test
    fun `topWithOther no agrupa si caben todas`() {
        val rows = topWithOther(listOf("a" to 1, "b" to 3, "c" to 2, "d" to 1), maxRows = 4)
        assertEquals(listOf("b", "c", "a", "d"), rows.map { it.label })
        assertTrue(rows.none { it.isOther })
    }
}
