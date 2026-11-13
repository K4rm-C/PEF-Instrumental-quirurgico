package com.example.cursobasico.views

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Message
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.ActionColor
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.composables.CommentsSheet
import com.example.cursobasico.composables.EvidenceContent
import com.example.cursobasico.composables.TimelineContent
import com.example.cursobasico.core.discrepancyRows
import com.example.cursobasico.core.resolutionNote
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.dash
import com.example.cursobasico.ui.components.pef

@Composable
fun OnHoldDetailView(sessionId: String, onBack: () -> Unit, onNavigate: (NavKey) -> Unit){
    var selectedSubmenu by rememberSaveable {
        mutableIntStateOf(1)
    }
    var showComments by rememberSaveable {
        mutableStateOf(false)
    }
    SessionDetailFrame(
        sessionId = sessionId,
        onBack = onBack,
        onNavigate = onNavigate,
        titlePrefix = stringResource(R.string.hold_detail_title),
        floatingActionButton = {
            FloatingActionButton(onClick = { showComments = true },
                containerColor = ActionColor) {
                Icon(
                    Icons.AutoMirrored.Filled.Message,
                    contentDescription = stringResource(R.string.cd_comments),
                    tint = OnActionColor
                )
            }
        }
    ) { detail ->
        SessionSubmenuOH(
            selectedSubmenu = selectedSubmenu,
            onSubmenuSelected = {
                selectedSubmenu = it
            }
        )
        Spacer(modifier = Modifier.height(18.dp))
        when (selectedSubmenu) {
            1 -> CountReportInfo(detail)
            2 -> TimelineContent(detail)
            3 -> EvidenceContent(sessionId)
        }
    }
    if (showComments) {
        CommentsSheet(
            onDismiss = {
                showComments = false
            }
        )
    }
}

@Composable
fun SessionSubmenuOH(
    selectedSubmenu: Int,
    onSubmenuSelected: (Int) -> Unit
) {
    Column(
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        Row (
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            SubmenuButton(
                text = stringResource(R.string.tab_session),
                selected = selectedSubmenu == 1,
                onClick = { onSubmenuSelected(1) },
                modifier = Modifier.weight(1f)
            )
            SubmenuButton(
                text = stringResource(R.string.tab_timeline),
                selected = selectedSubmenu == 2,
                onClick = { onSubmenuSelected(2) },
                modifier = Modifier.weight(1f)
            )
            SubmenuButton(
                text = stringResource(R.string.tab_evidence),
                selected = selectedSubmenu == 3,
                onClick = { onSubmenuSelected(3) },
                modifier = Modifier.weight(1f)
            )
        }
    }
}

/** Discrepancias registradas por SessionService (esperado vs detectado y motivo). Solo lectura. */
@Composable
fun ReportCount(detail: SessionDetailDto) {
    val instruments = detail.discrepancyRows()
    Column {
        Text(
            text = stringResource(R.string.hold_discrepancies_title),
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(10.dp))
        if (instruments.isEmpty()) {
            InfoBanner(stringResource(R.string.hold_no_discrepancies))
            return@Column
        }
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(FieldBackground)
                .border(1.dp, BorderColor)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(9.dp)
            ) {
                LiveCountCell(stringResource(R.string.col_instrument), Modifier.weight(1.7f))
                LiveCountCell(stringResource(R.string.col_expected), Modifier.weight(0.9f))
                LiveCountCell(stringResource(R.string.col_detected), Modifier.weight(0.9f))
                LiveCountCell(stringResource(R.string.col_difference), Modifier.weight(1f))
            }
            HorizontalDivider(color = BorderColor)
            instruments.forEach { item ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 49.dp)
                        .padding(
                            horizontal = 9.dp,
                            vertical = 8.dp
                        )
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = item.name,
                            modifier = Modifier.weight(1.7f),
                            fontSize = 9.sp,
                            lineHeight = 11.sp,
                            color = TextPrimary
                        )
                        LiveCountValue(
                            dash(item.expected),
                            Modifier.weight(0.9f)
                        )
                        LiveCountValue(
                            dash(item.detected),
                            Modifier.weight(0.9f)
                        )
                        LiveCountValue(
                            text = item.difference?.let { if (it > 0) "+$it" else it.toString() } ?: "—",
                            Modifier.weight(1f),
                            color = if ((item.difference ?: 0) != 0 && !item.resolved) pef.red else TextPrimary
                        )
                    }
                    Text(
                        text = listOfNotNull(
                            item.reason,
                            stringResource(if (item.resolved) R.string.discrepancy_resolved else R.string.discrepancy_open)
                        ).joinToString(" · "),
                        fontSize = 8.sp,
                        color = if (item.resolved) pef.success else pef.textSecondary
                    )
                }
            }
        }
    }
}

@Composable
fun InfoTextBox(
    text: String,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 60.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(FieldBackground)
            .border(
                width = 1.dp,
                color = BorderColor,
                shape = RoundedCornerShape(18.dp)
            )
            .padding(horizontal = 22.dp, vertical = 16.dp),
        contentAlignment = Alignment.CenterStart
    ) {
        Text(
            text = text,
            fontSize = 12.sp,
            fontWeight = FontWeight.Normal,
            color = TextPrimary
        )
    }
}

@Composable
fun CountReportInfo(detail: SessionDetailDto){
    Text(
        text = stringResource(R.string.label_status),
        fontSize = 12.sp,
        fontWeight = FontWeight.Bold,
        color = TextPrimary
    )
    Spacer(modifier = Modifier.height(10.dp))
    Column(
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            DetailStatCard(
                title = stringResource(R.string.detail_status),
                value = com.example.cursobasico.ui.components.statusLabel(detail.status.code),
                modifier = Modifier.weight(1f),
                valueColor = statusColor(detail.status.code)
            )
            DetailStatCard(
                title = stringResource(R.string.stat_open_discrepancies),
                value = detail.openDiscrepancies.toString(),
                modifier = Modifier.weight(1f),
                valueColor = if (detail.openDiscrepancies > 0) pef.red else TextPrimary
            )
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            DetailStatCard(
                title = stringResource(R.string.detail_capture_mode),
                value = dash(detail.captureMode),
                modifier = Modifier.weight(1f)
            )
            DetailStatCard(
                title = stringResource(R.string.detail_privacy),
                value = privacyText(detail),
                modifier = Modifier.weight(1f),
                valueColor = if (detail.hasPrivacyAgreement) pef.success else pef.orange
            )
        }
    }
    Spacer(modifier = Modifier.height(10.dp))
    ReportCount(detail)
    Spacer(modifier = Modifier.height(10.dp))
    Text(
        text = stringResource(R.string.hold_operator_note_title),
        fontSize = 12.sp,
        fontWeight = FontWeight.Bold,
        color = TextPrimary
    )
    Spacer(modifier = Modifier.height(10.dp))
    InfoTextBox(detail.resolutionNote() ?: stringResource(R.string.not_provided))
    if (detail.status.code in SessionStatus.ON_HOLD) {
        Spacer(modifier = Modifier.height(10.dp))
        // La resolución y el cierre seguro son acciones del Supervisor en el portal; la app no cierra sesiones.
        InfoBanner(stringResource(R.string.hold_review_in_portal))
    }
}
