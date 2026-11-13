package com.example.cursobasico.composables

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.BorderColor
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.core.TimelineRow
import com.example.cursobasico.core.timelineRows
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.formatDateTime

@Composable
fun TimelineEventCard(
    event: TimelineRow
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 120.dp),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(1.dp, BorderColor)
    ) {
        Column {
            Text(
                text = formatDateTime(event.occurredAt),
                fontSize = 10.sp,
                color = TextSecondary,
                modifier = Modifier.padding(
                    horizontal = 11.dp,
                    vertical = 7.dp
                )
            )
            HorizontalDivider(color = BorderColor)
            Column(
                modifier = Modifier.padding(11.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Row {
                    TimelineField(
                        stringResource(R.string.timeline_event),
                        event.event,
                        Modifier.weight(1f)
                    )
                    TimelineField(
                        stringResource(R.string.timeline_scope),
                        event.scope,
                        Modifier.weight(1f)
                    )
                }
                Row {
                    TimelineField(
                        stringResource(R.string.col_expected),
                        event.expected,
                        Modifier.weight(1f)
                    )
                    TimelineField(
                        stringResource(R.string.col_detected),
                        event.detected,
                        Modifier.weight(1f)
                    )
                }
            }
        }
    }
}
@Composable
fun TimelineField(
    label: String,
    value: String,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier) {
        Text(
            text = label,
            fontSize = 10.sp,
            color = TextSecondary
        )
        Spacer(modifier = Modifier.height(3.dp))
        Text(
            text = value,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = TextPrimary
        )
    }
}

/** Historial de eventos de conteo de la sesión (count_event registrado por el backend), en orden cronológico. */
@Composable
fun TimelineContent(detail: SessionDetailDto) {
    val events = detail.timelineRows()
    if (events.isEmpty()) {
        InfoBanner(stringResource(R.string.timeline_empty))
        return
    }
    Column(
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        events.forEach { event ->
            TimelineEventCard(event)
        }
    }
}
