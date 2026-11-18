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
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.ActionColor
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.composables.EvidenceContent
import com.example.cursobasico.composables.ResolveDiscrepancySheet
import com.example.cursobasico.composables.TimelineContent
import com.example.cursobasico.core.discrepancyRows
import com.example.cursobasico.core.resolutionNote
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.dash
import com.example.cursobasico.ui.components.errorTitleRes
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.state.kind
import com.example.cursobasico.ui.viewmodel.SessionDetailViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel

@Composable
fun OnHoldDetailView(sessionId: String, onBack: () -> Unit, onNavigate: (NavKey) -> Unit) {
    var selectedSubmenu by rememberSaveable { mutableIntStateOf(1) }
    var showResolve by rememberSaveable { mutableStateOf(false) }
    val viewModel = pefViewModel(key = "session-detail-$sessionId") {
        SessionDetailViewModel(sessionId, it.sessionRepository)
    }
    val detailState by viewModel.state.collectAsStateWithLifecycle()
    val resolveBusy by viewModel.resolveBusy.collectAsStateWithLifecycle()
    val resolveError by viewModel.resolveError.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val resources = LocalResources.current

    LaunchedEffect(resolveError) {
        resolveError?.let {
            snackbar.showSnackbar(resources.getString(errorTitleRes(it.kind())))
            viewModel.consumeResolveError()
        }
    }

    Box {
        SessionDetailFrame(
            sessionId = sessionId,
            onBack = onBack,
            onNavigate = onNavigate,
            titlePrefix = stringResource(R.string.hold_detail_title),
            floatingActionButton = {
                FloatingActionButton(
                    onClick = { showResolve = true },
                    containerColor = ActionColor
                ) {
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
                onSubmenuSelected = { selectedSubmenu = it }
            )
            Spacer(modifier = Modifier.height(18.dp))
            when (selectedSubmenu) {
                1 -> CountReportInfo(detail)
                2 -> TimelineContent(detail)
                3 -> EvidenceContent(sessionId)
            }
        }
        SnackbarHost(hostState = snackbar, modifier = Modifier.align(Alignment.BottomCenter))
    }

    if (showResolve) {
        val detail = (detailState as? UiState.Success)?.data
        val open = detail?.discrepancies?.filter { !it.resolved }.orEmpty()
        ResolveDiscrepancySheet(
            openDiscrepancies = open,
            busy = resolveBusy,
            errorDetail = resolveError?.serverMessage,
            onDismiss = { showResolve = false },
            onSubmit = { discrepancyId, notes, outcome ->
                viewModel.resolveDiscrepancy(discrepancyId, notes, outcome) { ok ->
                    if (ok) showResolve = false
                }
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
        Row(
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

@Composable
fun CountReportInfo(detail: SessionDetailDto) {
    Column(
        verticalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier.fillMaxWidth()
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
        InfoBanner(stringResource(R.string.hold_review_in_portal))
    }
}

@Composable
fun ReportCount(detail: SessionDetailDto) {
    val rows = detail.discrepancyRows()
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(11.dp))
            .border(1.dp, BorderColor, RoundedCornerShape(11.dp))
            .background(FieldBackground)
            .padding(14.dp)
    ) {
        Text(
            text = stringResource(R.string.hold_discrepancies_title),
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(10.dp))
        if (rows.isEmpty()) {
            Text(
                text = stringResource(R.string.hold_no_discrepancies),
                fontSize = 12.sp,
                color = pef.textSecondary
            )
        } else {
            rows.forEachIndexed { index, item ->
                if (index > 0) {
                    HorizontalDivider(
                        modifier = Modifier.padding(vertical = 8.dp),
                        color = BorderColor
                    )
                }
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(
                        text = item.name,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        color = if ((item.difference ?: 0) != 0 && !item.resolved) pef.red else TextPrimary,
                        modifier = Modifier.weight(1f)
                    )
                    Text(
                        text = stringResource(
                            if (item.resolved) R.string.discrepancy_resolved else R.string.discrepancy_open
                        ),
                        fontSize = 11.sp,
                        color = if (item.resolved) pef.success else pef.textSecondary
                    )
                }
                Text(
                    text = "${item.expected ?: "—"} → ${item.detected ?: "—"}",
                    fontSize = 11.sp,
                    color = pef.textSecondary
                )
            }
        }
    }
}

@Composable
fun InfoTextBox(text: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 72.dp)
            .clip(RoundedCornerShape(8.dp))
            .border(1.dp, BorderColor, RoundedCornerShape(8.dp))
            .background(FieldBackground)
            .padding(12.dp)
    ) {
        Text(text = text, fontSize = 12.sp, color = TextPrimary)
    }
}
