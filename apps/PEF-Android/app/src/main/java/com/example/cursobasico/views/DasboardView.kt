package com.example.cursobasico.views

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.Background
import com.example.cursobasico.Blue
import com.example.cursobasico.BorderColor
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.LightBlue
import com.example.cursobasico.LightOrange
import com.example.cursobasico.LightRed
import com.example.cursobasico.Orange
import com.example.cursobasico.R
import com.example.cursobasico.Red
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.core.DashboardData
import com.example.cursobasico.core.Menu
import com.example.cursobasico.core.procedureRoomText
import com.example.cursobasico.core.sessionRouteFor
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.components.statusLabel
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.viewmodel.DashboardViewModel
import com.example.cursobasico.ui.viewmodel.DiscrepancyPeriod
import com.example.cursobasico.ui.components.BarChartCard
import com.example.cursobasico.core.DiscrepancyCharts
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import com.example.cursobasico.ui.viewmodel.pefViewModel


@Composable
fun SurgicalDashboard(onNavigate: (NavKey) -> Unit,onSessionClick: (NavKey) -> Unit) {
    val viewModel = pefViewModel {
        DashboardViewModel(
            it.sessionRepository, it.notificationRepository, it.sessionManager, it.preferencesRepository,
            it.discrepancyStatsRepository
        )
    }
    val state by viewModel.state.collectAsStateWithLifecycle()
    val discrepancies by viewModel.discrepancies.collectAsStateWithLifecycle()
    val period by viewModel.period.collectAsStateWithLifecycle()
    // 0 = Session (tarjetas), 1 = Discrepancies (gráficas). Las sesiones activas/en espera se muestran en ambas.
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    LaunchedEffect(Unit) { viewModel.load() }
    LaunchedEffect(selectedTab) { if (selectedTab == 1) viewModel.loadDiscrepancies() }
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Background,
        topBar = {
            DashboardTopBar()
        },
        bottomBar = {
            DashboardBottomBarE(
                currentRoute = Menu,
                onNavigate = onNavigate
            )
        }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 27.dp)
            ) {

                Spacer(modifier = Modifier.height(38.dp))
                DashboardHeader(
                    refreshing = state is UiState.Loading || (state as? UiState.Success)?.refreshing == true ||
                        (selectedTab == 1 && discrepancies is UiState.Loading),
                    onRefresh = {
                        viewModel.load()
                        if (selectedTab == 1) viewModel.loadDiscrepancies(force = true)
                    }
                )
                Spacer(modifier = Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    SubmenuButton(
                        text = stringResource(R.string.dashboard_tab_session),
                        selected = selectedTab == 0,
                        onClick = { selectedTab = 0 },
                        modifier = Modifier.width(112.dp)
                    )
                    SubmenuButton(
                        text = stringResource(R.string.dashboard_tab_discrepancies),
                        selected = selectedTab == 1,
                        onClick = { selectedTab = 1 },
                        modifier = Modifier.width(112.dp)
                    )
                }
                Spacer(modifier = Modifier.height(12.dp))
                if (selectedTab == 0) {
                    // Con datos -> valores reales; sin datos (error, sin permiso) -> "—", nunca cifras inventadas.
                    StatisticsGrid((state as? UiState.Success)?.data)
                } else {
                    DiscrepanciesSection(
                        state = discrepancies,
                        period = period,
                        onPeriodChange = viewModel::setPeriod,
                        onRetry = { viewModel.loadDiscrepancies(force = true) }
                    )
                }
                Spacer(modifier = Modifier.height(18.dp))

                Text(
                    text = stringResource(R.string.dashboard_attention_title),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )
                Spacer(modifier = Modifier.height(16.dp))
                StateContent(
                    state = state,
                    onRetry = viewModel::load,
                    emptyText = stringResource(R.string.dashboard_attention_empty)
                ) { data ->
                    if (data.attention.isEmpty()) {
                        InfoBanner(stringResource(R.string.dashboard_attention_empty))
                    }
                    data.attention.take(MAX_ATTENTION).forEach { session ->
                        SessionCard(
                            code = sessionLabel(session.id),
                            description = session.procedureRoomText(),
                            status = statusLabel(session.status.code),
                            isActive = session.status.code == SessionStatus.IN_PROGRESS,
                            onClick = { onSessionClick(sessionRouteFor(session.id, session.status.code)) }
                        )
                        Spacer(modifier = Modifier.height(13.dp))
                    }
                    if (data.attention.size > MAX_ATTENTION) {
                        Text(
                            text = pluralStringResource(R.plurals.dashboard_more_in_sessions, data.attention.size - MAX_ATTENTION, data.attention.size - MAX_ATTENTION),
                            fontSize = 10.sp,
                            color = TextSecondary
                        )
                    }
                }
                Spacer(modifier = Modifier.height(27.dp))
            }
        }
    }
}

private const val MAX_ATTENTION = 10

/**
 * Pestaña Discrepancies: totales del reporte del supervisor (SessionService reports/summary) agrupados por
 * instrumento (familia del catálogo) y por familia de instrumentos (categoría del catálogo).
 */
@Composable
fun DiscrepanciesSection(
    state: UiState<DiscrepancyCharts>,
    period: DiscrepancyPeriod,
    onPeriodChange: (DiscrepancyPeriod) -> Unit,
    onRetry: () -> Unit
) {
    val periodOptions = listOf(
        FilterOption(DiscrepancyPeriod.LAST_7.name, stringResource(R.string.filter_last_7)),
        FilterOption(DiscrepancyPeriod.LAST_30.name, stringResource(R.string.filter_last_30)),
        FilterOption(DiscrepancyPeriod.LAST_90.name, stringResource(R.string.filter_last_90)),
        FilterOption(DiscrepancyPeriod.LAST_365.name, stringResource(R.string.filter_last_365))
    )
    if (state !is UiState.NotPermitted) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val data = (state as? UiState.Success)?.data
            Text(
                text = data?.let { stringResource(R.string.discrepancies_summary, it.total, it.open) } ?: "",
                fontSize = 10.sp,
                color = TextSecondary,
                modifier = Modifier.weight(1f)
            )
            OptionFilter(
                selected = period.name,
                options = periodOptions,
                onSelected = { value -> value?.let { onPeriodChange(DiscrepancyPeriod.valueOf(it)) } },
                modifier = Modifier.width(130.dp)
            )
        }
        Spacer(modifier = Modifier.height(10.dp))
    }
    StateContent(
        state = state,
        onRetry = onRetry,
        emptyText = stringResource(R.string.discrepancies_empty)
    ) { charts ->
        BarChartCard(title = stringResource(R.string.discrepancies_by_instrument), rows = charts.byInstrument)
        Spacer(modifier = Modifier.height(14.dp))
        BarChartCard(title = stringResource(R.string.discrepancies_by_family), rows = charts.byFamily)
    }
}

@Composable
fun DashboardHeader(refreshing: Boolean = false, onRefresh: () -> Unit = {}) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.dashboard_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = stringResource(R.string.dashboard_subtitle),
                fontSize = 11.sp,
                lineHeight = 14.sp,
                color = TextSecondary
            )
        }
        RefreshButton(onClick = onRefresh, refreshing = refreshing)
    }
}

@Composable
fun StatisticsGrid(data: DashboardData?) {
    fun value(selector: (DashboardData) -> Int): String = data?.let { selector(it).toString() } ?: "—"
    Column {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(13.dp)
        ) {
            StatisticCard(
                title = stringResource(R.string.stat_sessions_today),
                value = value { it.sessionsToday },
                modifier = Modifier.weight(1f),
                background = SurfaceColor,
                accent = TextPrimary,
                borderColor = BorderColor
            )
            StatisticCard(
                title = stringResource(R.string.stat_active_sessions),
                value = value { it.active },
                modifier = Modifier.weight(1f),
                background = LightBlue,
                accent = Blue,
                borderColor = pef.blueBorder
            )
        }
        Spacer(modifier = Modifier.height(17.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(13.dp)
        ) {
            StatisticCard(
                title = stringResource(R.string.stat_pending_reviews),
                value = value { it.pendingReview },
                modifier = Modifier.weight(1f),
                background = LightOrange,
                accent = Orange,
                borderColor = pef.orangeBorder
            )
            StatisticCard(
                title = stringResource(R.string.stat_open_discrepancies),
                value = value { it.openDiscrepancies },
                modifier = Modifier.weight(1f),
                background = LightRed,
                accent = Red,
                borderColor = pef.redBorder
            )
        }
        Spacer(modifier = Modifier.height(17.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(13.dp)
        ) {
            StatisticCard(
                title = stringResource(R.string.stat_scheduled),
                value = value { it.scheduled },
                modifier = Modifier.weight(1f),
                background = SurfaceColor,
                accent = TextPrimary,
                borderColor = BorderColor
            )
            StatisticCard(
                title = stringResource(R.string.stat_correction_required),
                value = value { it.correctionRequired },
                modifier = Modifier.weight(1f),
                background = LightRed,
                accent = Red,
                borderColor = pef.redBorder
            )
        }
    }
}

// -----------------------------------------
// TARJETA INDIVIDUAL DE ESTADISTICA
// -----------------------------------------

@Composable
fun StatisticCard(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    background: Color,
    accent: Color,
    borderColor: Color
) {
    val unavailable = stringResource(R.string.state_value_unavailable)
    Card(
        modifier = modifier
            .heightIn(min = 78.dp)
            .border(
                width = 1.dp,
                color = borderColor,
                shape = RoundedCornerShape(11.dp)
            )
            .semantics(mergeDescendants = true) {
                contentDescription = "$title: ${if (value == "—") unavailable else value}"
            },
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(
            containerColor = background
        ),
        elevation = CardDefaults.cardElevation(
            defaultElevation = 1.dp
        )
    ) {

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(
                    start = 14.dp,
                    top = 14.dp,
                    end = 8.dp,
                    bottom = 8.dp
                )
        ) {

            Text(
                text = title,
                fontSize = 8.sp,
                fontWeight = FontWeight.Medium,
                color = accent
            )

            Spacer(modifier = Modifier.height(7.dp))

            Text(
                text = value,
                fontSize = 23.sp,
                fontWeight = FontWeight.Bold,
                color = accent
            )
        }
    }
}

// -----------------------------------------
// MODELO VISUAL DE UNA SESION
// -----------------------------------------

@Composable
fun SessionCard(
    code: String,
    description: String,
    status: String,
    isActive: Boolean,
    onClick: () -> Unit
) {

    val statusBackground =
        if (isActive) pef.badgeActiveBg
        else pef.badgeHoldBg

    val statusTextColor =
        if (isActive) pef.badgeActiveText
        else pef.badgeHoldText

    val statusDotColor =
        if (isActive) pef.badgeActiveDot
        else pef.badgeHoldDot

    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 66.dp),
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        elevation = CardDefaults.cardElevation(
            defaultElevation = 1.dp
        ),
        border = androidx.compose.foundation.BorderStroke(
            width = 1.dp,
            color = BorderColor
        )
    ) {

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 66.dp)
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {

            Column(
                modifier = Modifier.weight(1f)
            ) {

                Text(
                    text = code,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )

                Spacer(modifier = Modifier.height(1.dp))

                Text(
                    text = description,
                    fontSize = 10.sp,
                    color = TextSecondary
                )
            }

            Row(
                modifier = Modifier
                    .clip(CircleShape)
                    .background(statusBackground)
                    .padding(
                        horizontal = 7.dp,
                        vertical = 4.dp
                    ),
                verticalAlignment = Alignment.CenterVertically
            ) {

                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .clip(CircleShape)
                        .background(statusDotColor)
                )

                Spacer(modifier = Modifier.width(4.dp))

                Text(
                    text = status,
                    fontSize = 9.sp,
                    color = statusTextColor,
                    maxLines = 1
                )
            }
        }
    }
}
