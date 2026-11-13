package com.example.cursobasico.views

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.ActionColor
import com.example.cursobasico.Background
import com.example.cursobasico.BorderColor
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.composables.EvidenceContent
import com.example.cursobasico.composables.TimelineContent
import com.example.cursobasico.core.Sessions
import com.example.cursobasico.core.inventoryRows
import com.example.cursobasico.core.liveCount
import com.example.cursobasico.core.procedureRoomText
import com.example.cursobasico.data.model.SessionDetailDto
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.dash
import com.example.cursobasico.ui.components.formatDateTime
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.components.statusLabel
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.viewmodel.SessionDetailViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Marco común de las pantallas de detalle: carga GET /api/v1/sessions/{id}?expand=detail y muestra
 * carga / error / sin permiso. Siempre refleja el estado que devuelve el servidor.
 */
@Composable
fun SessionDetailFrame(
    sessionId: String,
    onBack: () -> Unit,
    onNavigate: (NavKey) -> Unit,
    titlePrefix: String? = null,
    floatingActionButton: @Composable () -> Unit = {},
    content: @Composable ColumnScope.(SessionDetailDto) -> Unit
) {
    val viewModel = pefViewModel(key = "session-detail-$sessionId") {
        SessionDetailViewModel(sessionId, it.sessionRepository)
    }
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(sessionId) { viewModel.load() }
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Background,
        topBar = {
            DashboardTopBar()
        },
        bottomBar = {
            DashboardBottomBarE(
                currentRoute = Sessions,
                onNavigate = onNavigate
            )
        },
        floatingActionButton = floatingActionButton
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
        ) {
            Spacer(modifier = Modifier.height(14.dp))
            val detail = (state as? UiState.Success)?.data
            SessionDetailHeader(
                onBack = onBack,
                title = sessionLabel(sessionId).let { label -> titlePrefix?.let { "$it ($label)" } ?: label },
                subtitle = detail?.procedureRoomText() ?: "",
                refreshing = state is UiState.Loading || (state as? UiState.Success)?.refreshing == true,
                onRefresh = viewModel::load
            )
            Spacer(modifier = Modifier.height(16.dp))
            StateContent(state = state, onRetry = viewModel::load, emptyText = "") { data ->
                content(data)
            }
            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

@Composable
fun InProgressScreen(sessionId: String, onBack: () -> Unit, onNavigate: (NavKey) -> Unit){
    var selectedSubmenu by rememberSaveable {
        mutableIntStateOf(1)
    }
    SessionDetailFrame(sessionId = sessionId, onBack = onBack, onNavigate = onNavigate) { detail ->
        SessionSubmenu(
            selectedSubmenu = selectedSubmenu,
            onSubmenuSelected = {
                selectedSubmenu = it
            }
        )
        Spacer(modifier = Modifier.height(18.dp))
        when (selectedSubmenu) {
            1 -> SessionInfoContent(detail)
            2 -> InventoryContent(detail)
            3 -> TimelineContent(detail)
            4 -> LiveCountContent(detail)
            5 -> EvidenceContent(sessionId)
        }
    }
}

@Composable
fun SessionDetailHeader(
    onBack: () -> Unit,
    title: String,
    subtitle: String,
    refreshing: Boolean = false,
    onRefresh: (() -> Unit)? = null
) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(
                onClick = onBack,
                modifier = Modifier.size(28.dp)
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = stringResource(R.string.cd_back),
                    tint = TextSecondary,
                    modifier = Modifier.size(18.dp)
                )
            }
            Spacer(modifier = Modifier.weight(1f))
            if (onRefresh != null) RefreshButton(onClick = onRefresh, refreshing = refreshing)
        }
        Spacer(modifier = Modifier.height(3.dp))
        Text(
            text = title,
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Text(
            text = subtitle,
            fontSize = 11.sp,
            color = TextSecondary
        )
    }
}

@Composable
fun SessionSubmenu(
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
                text = stringResource(R.string.tab_inventory),
                selected = selectedSubmenu == 2,
                onClick = { onSubmenuSelected(2) },
                modifier = Modifier.weight(1f)
            )
            SubmenuButton(
                text = stringResource(R.string.tab_timeline),
                selected = selectedSubmenu == 3,
                onClick = { onSubmenuSelected(3) },
                modifier = Modifier.weight(1f)
            )
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            SubmenuButton(
                text = stringResource(R.string.tab_live_count),
                selected = selectedSubmenu == 4,
                onClick = { onSubmenuSelected(4) },
                modifier = Modifier.width(100.dp)
            )
            SubmenuButton(
                text = stringResource(R.string.tab_evidence),
                selected = selectedSubmenu == 5,
                onClick = { onSubmenuSelected(5) },
                modifier = Modifier.width(100.dp)
            )
        }
    }
}

@Composable
fun SubmenuButton(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .heightIn(min = 25.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(
                if (selected) ActionColor else SurfaceColor
            )
            .border(
                1.dp,
                if (selected) ActionColor else BorderColor,
                RoundedCornerShape(9.dp)
            )
            .semantics { this.selected = selected }
            .clickable(role = Role.Tab) { onClick() }
            .padding(horizontal = 4.dp, vertical = 4.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            fontSize = 10.sp,
            textAlign = TextAlign.Center,
            fontWeight = if (selected)
                FontWeight.SemiBold
            else FontWeight.Normal,
            color = if (selected)
                OnActionColor
            else TextSecondary
        )
    }
}

@Composable
fun DetailField(
    label: String,
    value: String,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            fontSize = 10.sp,
            color = TextSecondary
        )
        Spacer(modifier = Modifier.width(12.dp))
        Text(
            text = value,
            fontSize = 10.sp,
            color = TextPrimary,
            fontWeight = FontWeight.Medium,
            textAlign = TextAlign.End
        )
    }
}

@Composable
fun DetailSectionCard(
    title: String,
    content: @Composable ColumnScope.() -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(9.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(
            1.dp,
            pef.strongBorder
        )
    ) {
        Column(
            modifier = Modifier.padding(11.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp)
        ) {
            Text(
                text = title,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(3.dp))
            content()
        }
    }
}

@Composable
fun DetailStatCard(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = TextPrimary
) {
    Card(
        modifier = modifier.heightIn(min = 78.dp),
        shape = RoundedCornerShape(9.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(1.dp, BorderColor),
        elevation = CardDefaults.cardElevation(
            defaultElevation = 1.dp
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 78.dp)
                .padding(11.dp),
            verticalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = title,
                fontSize = 8.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Text(
                text = value,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = valueColor
            )
        }
    }
}

@Composable
fun statusColor(code: String): Color = when (code) {
    SessionStatus.IN_PROGRESS -> pef.blue
    in SessionStatus.ON_HOLD -> pef.red
    SessionStatus.CLOSED -> pef.success
    else -> TextSecondary
}

@Composable
fun privacyText(detail: SessionDetailDto): String =
    if (detail.hasPrivacyAgreement) {
        detail.privacy?.version?.let { stringResource(R.string.privacy_agreed_version, it) }
            ?: stringResource(R.string.privacy_agreed)
    } else stringResource(R.string.privacy_pending)

@Composable
fun SessionInfoContent(detail: SessionDetailDto) {
    Column(
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            DetailStatCard(
                title = stringResource(R.string.detail_status),
                value = statusLabel(detail.status.code),
                modifier = Modifier.weight(1f),
                valueColor = statusColor(detail.status.code)
            )
            DetailStatCard(
                title = stringResource(R.string.detail_capture_mode),
                value = dash(detail.captureMode),
                modifier = Modifier.weight(1f)
            )
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            DetailStatCard(
                title = stringResource(R.string.detail_privacy),
                value = privacyText(detail),
                modifier = Modifier.weight(1f),
                valueColor = if (detail.hasPrivacyAgreement) pef.success else pef.orange
            )
            DetailStatCard(
                title = stringResource(R.string.stat_open_discrepancies),
                value = detail.openDiscrepancies.toString(),
                modifier = Modifier.weight(1f),
                valueColor = if (detail.openDiscrepancies > 0) pef.red else TextPrimary
            )
        }
        DetailSectionCard(title = stringResource(R.string.detail_assignment)) {
            DetailField(stringResource(R.string.label_operator), dash(detail.operator?.name))
            DetailField(stringResource(R.string.label_patient), dash(detail.patient?.displayName))
            DetailField(stringResource(R.string.label_kit), dash(detail.kit?.name))
            DetailField(stringResource(R.string.label_capture_station), dash(detail.station?.name))
            DetailField(
                stringResource(R.string.label_tray),
                dash(listOf("code", "name").firstNotNullOfOrNull { (detail.tray?.get(it) as? JsonPrimitive)?.contentOrNull })
            )
            DetailField(stringResource(R.string.label_phase), dash(detail.phase?.name ?: detail.phase?.code))
        }
        DetailSectionCard(title = stringResource(R.string.detail_timing)) {
            DetailField(stringResource(R.string.label_scheduled_start), formatDateTime(detail.operation?.scheduledAt))
            DetailField(stringResource(R.string.label_started), formatDateTime(detail.startedAt))
            DetailField(stringResource(R.string.label_ended), formatDateTime(detail.endedAt))
        }
        DetailSectionCard(title = stringResource(R.string.detail_surgical_team)) {
            if (detail.surgicalTeam.isEmpty()) {
                Text(stringResource(R.string.detail_team_empty), fontSize = 10.sp, color = TextSecondary)
            }
            detail.surgicalTeam.forEach { member ->
                DetailField(dash(member.roleName ?: member.roleCode), dash(member.name))
            }
        }
        detail.visionModel?.let { model ->
            DetailSectionCard(title = stringResource(R.string.detail_vision_model)) {
                DetailField(stringResource(R.string.label_model_version), dash(model.versionTag))
            }
        }
    }
}

@Composable
fun InventoryContent(detail: SessionDetailDto) {
    val instruments = detail.inventoryRows()
    if (instruments.isEmpty()) {
        InfoBanner(stringResource(R.string.inventory_empty))
        return
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(FieldBackground)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(34.dp)
                .padding(horizontal = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = stringResource(R.string.col_instrument),
                modifier = Modifier.weight(1f),
                fontSize = 9.sp,
                color = TextSecondary
            )
            Text(
                text = stringResource(R.string.col_expected),
                fontSize = 9.sp,
                color = TextSecondary
            )
        }
        HorizontalDivider(color = BorderColor)
        instruments.forEach { instrument ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 45.dp)
                    .padding(
                        horizontal = 10.dp,
                        vertical = 9.dp
                    ),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = instrument.name,
                    modifier = Modifier.weight(1f),
                    fontSize = 10.sp,
                    lineHeight = 13.sp,
                    color = TextPrimary
                )
                Text(
                    text = instrument.expected.toString(),
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )
            }
        }
    }
}

@Composable
fun LiveCountCell(
    text: String,
    modifier: Modifier
) {
    Text(
        text = text,
        modifier = modifier,
        fontSize = 8.sp,
        color = TextSecondary
    )
}

@Composable
fun LiveCountValue(
    text: String,
    modifier: Modifier,
    color: Color = TextPrimary
) {
    Text(
        text = text,
        modifier = modifier,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        color = color
    )
}

/** Último conteo YOLO registrado por el backend (auto_count). La app no recalcula ni valida conteos. */
@Composable
fun LiveCountContent(detail: SessionDetailDto) {
    val live = detail.liveCount()
    Column {
        Text(
            text = stringResource(R.string.live_title),
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = if (live.updatedAt != null) {
                stringResource(R.string.live_updated_at, formatDateTime(live.updatedAt)) +
                    (live.trigger?.let { " · $it" } ?: "")
            } else stringResource(R.string.live_no_counts),
            fontSize = 10.sp,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(10.dp))
        if (live.rows.isEmpty()) {
            InfoBanner(stringResource(R.string.inventory_empty))
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
            }
            HorizontalDivider(color = BorderColor)
            live.rows.forEach { item ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 49.dp)
                        .padding(
                            horizontal = 9.dp,
                            vertical = 8.dp
                        ),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = item.name,
                        modifier = Modifier.weight(1.7f),
                        fontSize = 9.sp,
                        lineHeight = 11.sp,
                        color = TextPrimary
                    )
                    LiveCountValue(
                        item.expected.toString(),
                        Modifier.weight(0.9f)
                    )
                    LiveCountValue(
                        item.detected?.toString() ?: "—",
                        Modifier.weight(0.9f)
                    )
                }
            }
        }
        Spacer(modifier = Modifier.height(8.dp))
        InfoBanner(stringResource(R.string.live_confidence_unavailable))
    }
}
