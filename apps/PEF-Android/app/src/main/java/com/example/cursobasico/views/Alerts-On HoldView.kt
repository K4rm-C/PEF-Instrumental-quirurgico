package com.example.cursobasico.views

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.Background
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.background
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.ActionColor
import com.example.cursobasico.BorderColor
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.core.AlertsOnHold
import com.example.cursobasico.core.procedureRoomText
import com.example.cursobasico.core.sessionRouteFor
import com.example.cursobasico.data.model.NotificationDto
import com.example.cursobasico.data.model.NotificationTypes
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.Pill
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.SessionStatusPill
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.errorTitleRes
import com.example.cursobasico.ui.components.formatDateTime
import com.example.cursobasico.ui.components.notificationTypeLabelRes
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.state.kind
import com.example.cursobasico.ui.viewmodel.AlertFilters
import com.example.cursobasico.ui.viewmodel.AlertsViewModel
import com.example.cursobasico.ui.viewmodel.DateRange
import com.example.cursobasico.ui.viewmodel.pefViewModel
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull


@Composable
fun Alerts_OnHoldView(onNavigate: (NavKey) -> Unit, onSessionClick: (NavKey) -> Unit){
    val viewModel = pefViewModel { AlertsViewModel(it.notificationRepository, it.sessionRepository, it.sessionManager) }
    val notificationsState by viewModel.notificationsState.collectAsStateWithLifecycle()
    val onHoldState by viewModel.onHoldState.collectAsStateWithLifecycle()
    val filters by viewModel.filters.collectAsStateWithLifecycle()
    val busy by viewModel.busy.collectAsStateWithLifecycle()
    val unread by viewModel.unread.collectAsStateWithLifecycle()
    val actionError by viewModel.actionError.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.loadAll() }

    val snackbar = remember { SnackbarHostState() }
    val resources = LocalResources.current
    LaunchedEffect(actionError) {
        actionError?.let {
            snackbar.showSnackbar(resources.getString(errorTitleRes(it.kind())))
            viewModel.consumeActionError()
        }
    }

    var selectedTab by rememberSaveable { mutableStateOf(1) }
    var searchText by rememberSaveable {
        mutableStateOf("")
    }
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Background,
        topBar = {
            DashboardTopBar()
        },
        bottomBar = {
            DashboardBottomBarE(
                currentRoute = AlertsOnHold,
                onNavigate = onNavigate
            )
        },
        snackbarHost = { SnackbarHost(snackbar) }
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
                SessionsHeader(
                    refreshing = notificationsState is UiState.Loading || onHoldState is UiState.Loading,
                    onRefresh = viewModel::loadAll
                )
                Spacer(modifier = Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
                    SubmenuButton(
                        text = if (unread != null && unread!! > 0) {
                            stringResource(R.string.alerts_tab_notifications_count, unread!!)
                        } else stringResource(R.string.alerts_tab_notifications),
                        selected = selectedTab == 1,
                        onClick = { selectedTab = 1 },
                        modifier = Modifier.weight(1f)
                    )
                    SubmenuButton(
                        text = stringResource(R.string.alerts_tab_on_hold),
                        selected = selectedTab == 2,
                        onClick = { selectedTab = 2 },
                        modifier = Modifier.weight(1f)
                    )
                }
                Spacer(modifier = Modifier.height(13.dp))
                SearchBarSessions (searchText = searchText,
                    onSearchChange = {
                        searchText = it
                    })
                Spacer(modifier = Modifier.height(13.dp))
                if (selectedTab == 1) {
                    SessionFilters(filters = filters, onChange = viewModel::setFilters)
                    Spacer(modifier = Modifier.height(10.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        OutlinedButton(
                            onClick = viewModel::markAllRead,
                            enabled = AlertsViewModel.MARK_ALL !in busy && (unread ?: 0) > 0,
                            modifier = Modifier.height(30.dp),
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 0.dp),
                            shape = RoundedCornerShape(11.dp),
                            border = BorderStroke(1.dp, BorderColor)
                        ) {
                            Text(stringResource(R.string.alerts_mark_all_read), fontSize = 10.sp, color = TextPrimary)
                        }
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    StateContent(
                        state = notificationsState,
                        onRetry = viewModel::loadNotifications,
                        emptyText = stringResource(R.string.alerts_empty)
                    ) { page ->
                        val items = page.items.filter { it.matches(searchText) }
                        if (items.isEmpty()) {
                            MessageCard(Icons.Default.Search, stringResource(R.string.alerts_no_match))
                        }
                        items.forEach { notification ->
                            NotificationCard(
                                notification = notification,
                                busy = notification.id in busy || (notification.sessionId != null && notification.sessionId in busy),
                                onMarkRead = { viewModel.markRead(notification.id) },
                                onOpenSession = notification.sessionId?.let { id ->
                                    {
                                        if (notification.isUnread) viewModel.markRead(notification.id)
                                        viewModel.openSession(id, onSessionClick)
                                    }
                                }
                            )
                            Spacer(modifier = Modifier.height(15.dp))
                        }
                        if (page.totalPages > 1) {
                            Text(
                                text = pluralStringResource(R.plurals.alerts_showing, page.total, page.items.size, page.total),
                                fontSize = 10.sp,
                                color = TextSecondary
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    InfoBanner(stringResource(R.string.alerts_push_not_available))
                } else {
                    StateContent(
                        state = onHoldState,
                        onRetry = viewModel::loadOnHold,
                        emptyText = stringResource(R.string.alerts_on_hold_empty)
                    ) { sessions ->
                        val filteredSessions = sessions.filter { it.matches(searchText) }
                        filteredSessions.forEach { session ->
                            AlertSessionCard(session = session,
                                onClick = { onSessionClick(sessionRouteFor(session.id, session.status.code)) })
                            Spacer(modifier = Modifier.height(15.dp))
                        }
                        if (filteredSessions.isEmpty()) {
                            Text(
                                text = stringResource(R.string.alerts_no_sessions_found),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 25.dp),
                                fontSize = 12.sp,
                                color = TextSecondary
                            )
                        }
                    }
                }
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

private fun NotificationDto.sessionLabelText(): String? =
    (data?.get("session_label") as? JsonPrimitive)?.contentOrNull ?: sessionId?.let(::sessionLabel)

private fun NotificationDto.matches(query: String): Boolean {
    val q = query.trim()
    return q.isEmpty() || listOfNotNull(title, message, sessionLabelText(), type).any { it.contains(q, ignoreCase = true) }
}

private fun SessionSummaryDto.matches(query: String): Boolean {
    val q = query.trim()
    return q.isEmpty() || listOfNotNull(sessionLabel(id), operator?.name, operation?.procedureType?.name,
        operation?.room?.code).any { it.contains(q, ignoreCase = true) }
}

@Composable
fun SessionsHeader(refreshing: Boolean = false, onRefresh: () -> Unit = {}){
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.alerts_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = stringResource(R.string.alerts_subtitle),
                fontSize = 11.sp,
                lineHeight = 14.sp,
                color = TextSecondary
            )
        }
        RefreshButton(onClick = onRefresh, refreshing = refreshing)
    }
}


@Composable
fun SearchBarSessions(
    searchText: String,
    onSearchChange: (String) -> Unit
) {
    SearchBarSessionsE(
        searchText = searchText,
        onSearchChange = onSearchChange,
        placeholder = stringResource(R.string.alerts_search_placeholder)
    )
}


@Composable
fun SessionFilter(
    selectedOption: String,
    options: List<String>,
    onOptionSelected: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    SessionFilterE(
        selectedOption = selectedOption,
        options = options,
        onOptionSelected = onOptionSelected,
        modifier = modifier
    )
}


/** Filtros que aplica NotificationsService: status, type y from (rango de fechas). */
@Composable
fun SessionFilters(
    filters: AlertFilters,
    onChange: (AlertFilters) -> Unit
) {
    val statusOptions = listOf(
        FilterOption(null, stringResource(R.string.filter_all_status)),
        FilterOption("unread", stringResource(R.string.notification_unread)),
        FilterOption("read", stringResource(R.string.notification_read))
    )
    val typeOptions = listOf(FilterOption(null, stringResource(R.string.filter_all_types))) +
        NotificationTypes.ALL.map { FilterOption(it, stringResource(notificationTypeLabelRes(it))) }
    val rangeOptions = listOf(
        FilterOption(DateRange.ALL.name, stringResource(R.string.filter_any_date)),
        FilterOption(DateRange.TODAY.name, stringResource(R.string.filter_today)),
        FilterOption(DateRange.LAST_7.name, stringResource(R.string.filter_last_7)),
        FilterOption(DateRange.LAST_30.name, stringResource(R.string.filter_last_30))
    )
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(13.dp)
    ) {
        OptionFilter(
            selected = filters.status,
            options = statusOptions,
            onSelected = { onChange(filters.copy(status = it)) },
            modifier = Modifier.weight(1f)
        )
        OptionFilter(
            selected = filters.range.name,
            options = rangeOptions,
            onSelected = { value -> onChange(filters.copy(range = DateRange.entries.first { it.name == value })) },
            modifier = Modifier.weight(1f)
        )
        OptionFilter(
            selected = filters.type,
            options = typeOptions,
            onSelected = { onChange(filters.copy(type = it)) },
            modifier = Modifier.weight(1.1f)
        )
    }
}

/** Tarjeta de notificación con la estructura visual de AlertSessionCard. */
@Composable
fun NotificationCard(
    notification: NotificationDto,
    busy: Boolean,
    onMarkRead: () -> Unit,
    onOpenSession: (() -> Unit)?
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(
            width = 1.dp,
            color = if (notification.isUnread) pef.blue else BorderColor
        ),
        elevation = CardDefaults.cardElevation(
            defaultElevation = 0.dp
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 18.dp, end = 7.dp, top = 17.dp, bottom = 9.dp)
        ) {
            Row(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.weight(1f)) {
                    SessionLabel(stringResource(R.string.label_session))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(notification.sessionLabelText() ?: "—")
                }
                Column(modifier = Modifier.weight(1f)) {
                    SessionLabel(stringResource(R.string.label_status))
                    Spacer(modifier = Modifier.height(2.dp))
                    if (notification.isUnread) {
                        Pill(stringResource(R.string.notification_unread), pef.badgeActiveBg, pef.badgeActiveText, pef.badgeActiveDot)
                    } else {
                        Pill(stringResource(R.string.notification_read), pef.badgeNeutralBg, pef.badgeNeutralText, pef.badgeNeutralText)
                    }
                }
            }
            Spacer(modifier = Modifier.height(13.dp))
            Row(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.weight(1f)) {
                    SessionLabel(stringResource(R.string.label_type))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(stringResource(notificationTypeLabelRes(notification.type)))
                }
                Column(modifier = Modifier.weight(1f)) {
                    SessionLabel(stringResource(R.string.label_date))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(formatDateTime(notification.createdAt))
                }
            }
            Spacer(modifier = Modifier.height(8.dp))
            Column {
                SessionLabel(stringResource(R.string.label_message))
                Spacer(modifier = Modifier.height(3.dp))
                // Texto del servidor tal cual (no se traducen mensajes clínicos sin una regla definida).
                SessionValue(notification.message ?: "—")
            }
            Spacer(modifier = Modifier.height(10.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                verticalAlignment = Alignment.CenterVertically
            ) {
                if (busy) {
                    CircularProgressIndicator(color = pef.textSecondary, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
                }
                if (notification.isUnread) {
                    OutlinedButton(
                        onClick = onMarkRead,
                        enabled = !busy,
                        modifier = Modifier.height(30.dp),
                        shape = RoundedCornerShape(11.dp),
                        border = BorderStroke(1.dp, BorderColor),
                        contentPadding = PaddingValues(horizontal = 13.dp, vertical = 0.dp)
                    ) {
                        Text(stringResource(R.string.alerts_mark_read), fontSize = 10.sp, color = TextPrimary)
                    }
                }
                if (onOpenSession != null) {
                    AlertActionButton(stringResource(R.string.alerts_view_session), enabled = !busy, onClick = onOpenSession)
                }
            }
        }
    }
}

@Composable
private fun AlertActionButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.height(30.dp),
        shape = RoundedCornerShape(11.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = ActionColor,
            contentColor = OnActionColor
        ),
        contentPadding = PaddingValues(
            horizontal = 13.dp,
            vertical = 0.dp
        )
    ) {
        Text(
            text = text,
            fontSize = 10.sp,
            fontWeight = FontWeight.Medium
        )
    }
}


@Composable
fun AlertSessionCard(
    session: SessionSummaryDto,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(
            width = 1.dp,
            color = TextSecondary
        ),
        elevation = CardDefaults.cardElevation(
            defaultElevation = 0.dp
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(
                    start = 18.dp,
                    end = 7.dp,
                    top = 17.dp,
                    bottom = 9.dp
                )
        ) {
            // FILA 1: SESSION Y STATUS
            Row(
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.weight(1f)
                ) {
                    SessionLabel(stringResource(R.string.label_session))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(sessionLabel(session.id))
                }
                Column(
                    modifier = Modifier.weight(1f)
                ) {
                    SessionLabel(stringResource(R.string.label_status))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionStatusPill(session.status.code)
                }
            }

            Spacer(modifier = Modifier.height(13.dp))

            // FILA 2: OPERATOR Y PROCEDURE

            Row(
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.weight(1f)
                ) {
                    SessionLabel(stringResource(R.string.label_operator))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(session.operator?.name ?: "—")
                }

                Column(
                    modifier = Modifier.weight(1f)
                ) {
                    SessionLabel(stringResource(R.string.label_procedure))
                    Spacer(modifier = Modifier.height(2.dp))
                    SessionValue(session.procedureRoomText())
                }
            }
            Spacer(modifier = Modifier.height(8.dp))

            // FILA 3: DISCREPANCIAS ABIERTAS (dato real del backend)

            Column {
                SessionLabel(stringResource(R.string.label_open_discrepancies))
                Spacer(modifier = Modifier.height(3.dp))
                SessionValue(session.openDiscrepancies.toString())
            }
            Spacer(modifier = Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End
            ) {
                AlertActionButton(stringResource(R.string.action_view_details), onClick = onClick)
            }
        }
    }
}

@Composable
fun SessionLabel(text: String) {
    Text(
        text = text,
        fontSize = 11.sp,
        color = TextSecondary
    )
}

@Composable
fun SessionValue(text: String) {
    Text(
        text = text,
        fontSize = 11.sp,
        lineHeight = 14.sp,
        fontWeight = FontWeight.Medium,
        color = TextPrimary
    )
}

@Composable
fun StatusBadge(statusCode: String) {
    SessionStatusPill(statusCode)
}
