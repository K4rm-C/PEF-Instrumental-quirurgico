package com.example.cursobasico.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.R
import com.example.cursobasico.core.ChartRow

/**
 * Tarjeta de barras horizontales (diseño "Discrepancies by …"): nombre, total a la derecha y barra proporcional al
 * valor mayor de la tarjeta. Los colores siguen el orden de la paleta; "Other" usa su propio color.
 */
@Composable
fun BarChartCard(title: String, rows: List<ChartRow>, modifier: Modifier = Modifier) {
    val maxCount = rows.maxOfOrNull { it.count }?.coerceAtLeast(1) ?: 1
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(containerColor = pef.surface),
        border = BorderStroke(1.dp, pef.border),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 18.dp)) {
            Text(title, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = pef.textPrimary)
            Spacer(Modifier.height(14.dp))
            var colorIndex = 0
            rows.forEach { row ->
                val color = if (row.isOther) pef.chartOther else pef.chartPalette[colorIndex++ % pef.chartPalette.size]
                val label = when {
                    row.isOther -> stringResource(R.string.chart_other)
                    row.label == null -> stringResource(R.string.chart_not_specified)
                    else -> row.label
                }
                BarRow(label, row.count, row.count.toFloat() / maxCount, color)
                Spacer(Modifier.height(12.dp))
            }
        }
    }
}

@Composable
private fun BarRow(label: String, count: Int, fraction: Float, color: androidx.compose.ui.graphics.Color) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clearAndSetSemantics { contentDescription = "$label: $count" },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                label,
                fontSize = 11.sp,
                color = pef.textPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Text(count.toString(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = pef.textPrimary)
        }
        Spacer(Modifier.height(5.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp))
                .background(pef.chartTrack),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth(fraction.coerceIn(0.04f, 1f))
                    .fillMaxHeight()
                    .clip(RoundedCornerShape(3.dp))
                    .background(color),
            )
        }
    }
}
