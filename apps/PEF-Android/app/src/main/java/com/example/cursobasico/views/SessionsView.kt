package com.example.cursobasico.views

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.Background
import com.example.cursobasico.BorderColor
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.core.Sessions
import com.example.cursobasico.core.filterSessions
import com.example.cursobasico.core.procedureRoomText
import com.example.cursobasico.core.sessionRouteFor
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.SessionStatusPill
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.formatDateTime
import com.example.cursobasico.ui.components.statusLabel
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.state.dataOrNull
import com.example.cursobasico.ui.viewmodel.SessionsViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel

/** Opción de filtro: [value] null = "todos". */
data class FilterOption(val value: String?, val label: String)

@Composable
fun SessionsView(onNavigate: (NavKey) -> Unit,onSessionClick: (NavKey) -> Unit) {
    val viewModel = pefViewModel { SessionsViewModel(it.sessionRepository, it.sessionManager) }
    val state by viewModel.state.collectAsStateWithLifecycle()
    val statusFilter by viewModel.statusFilter.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.load() }

    var searchText by rememberSaveable {
        mutableStateOf("")
    }
    var selectedRoom by rememberSaveable {
        mutableStateOf<String?>(null)
    }
    var selectedProcedure by rememberSaveable {
        mutableStateOf<String?>(null)
    }
    val loaded = state.dataOrNull().orEmpty()
    // Opciones de sala y procedimiento a partir de las sesiones reales (no listas fijas).
    val rooms = loaded.mapNotNull { it.operation?.room?.code }.distinct().sorted()
    val procedures = loaded.mapNotNull { it.operation?.procedureType?.name }.distinct().sorted()
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
                ViewHeader(
                    refreshing = state is UiState.Loading || (state as? UiState.Success)?.refreshing == true,
                    onRefresh = { viewModel.load(force = true) }
                )
                Spacer(modifier = Modifier.height(14.dp))
                SearchBarSessionsE (searchText = searchText,
                    onSearchChange = {
                        searchText = it
                    })
                Spacer(modifier = Modifier.height(13.dp))
                SessionFiltersE(
                    selectedStatus = statusFilter,
                    selectedRoom = selectedRoom,
                    selectedProcedure = selectedProcedure,
                    rooms = rooms,
                    procedures = procedures,
                    onStatusChange = viewModel::setStatusFilter,
                    onRoomChange = {
                        selectedRoom = it
                    },
                    onProcedureChange = {
                        selectedProcedure = it
                    }
                )
                Spacer(modifier = Modifier.height(16.dp))
                StateContent(
                    state = state,
                    onRetry = { viewModel.load(force = true) },
                    emptyText = stringResource(R.string.sessions_empty)
                ) { sessions ->
                    val filteredSessions = filterSessions(sessions, searchText, selectedRoom, selectedProcedure, ::sessionLabel)
                    if (filteredSessions.isEmpty()) {
                        MessageCard(
                            icon = Icons.Default.Search,
                            title = stringResource(R.string.sessions_no_match)
                        )
                    }
                    filteredSessions.forEach { session ->
                        SessionCardE(
                            code = sessionLabel(session.id),
                            description = session.procedureRoomText(),
                            statusCode = session.status.code,
                            operator = listOfNotNull(
                                session.operator?.name,
                                formatDateTime(session.startedAt ?: session.operation?.scheduledAt).takeIf { it != "—" }
                            ).joinToString(" · ").ifBlank { "—" },
                            onClick = { onSessionClick(sessionRouteFor(session.id, session.status.code)) }
                        )
                        Spacer(modifier = Modifier.height(13.dp))
                    }
                }
                Spacer(modifier = Modifier.height(27.dp))
            }
        }
    }
}


@Composable
fun ViewHeader(refreshing: Boolean = false, onRefresh: () -> Unit = {}){
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.sessions_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = stringResource(R.string.sessions_subtitle),
                fontSize = 11.sp,
                lineHeight = 14.sp,
                color = TextSecondary
            )
        }
        RefreshButton(onClick = onRefresh, refreshing = refreshing)
    }
}

@Composable
fun SessionCardE(
    code: String,
    description: String,
    statusCode: String,
    operator: String,
    onClick: () -> Unit
) {
    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 76.dp),
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
                .heightIn(min = 76.dp)
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f)
            ) {
                Spacer(modifier = Modifier.height(1.dp))
                Text(
                    text = code,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )
                Text(
                    text = description,
                    fontSize = 10.sp,
                    color = TextSecondary
                )
                Text(
                    text = operator,
                    fontSize = 10.sp,
                    color = TextSecondary
                )
            }
            SessionStatusPill(statusCode)
        }
    }
}

@Composable
fun SearchBarSessionsE(
    searchText: String,
    onSearchChange: (String) -> Unit,
    placeholder: String = stringResource(R.string.sessions_search_placeholder)
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(37.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(FieldBackground)
            .border(
                width = 1.dp,
                color = BorderColor,
                shape = RoundedCornerShape(6.dp)
            )
            .padding(horizontal = 9.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = Icons.Default.Search,
            contentDescription = stringResource(R.string.cd_search),
            tint = TextSecondary,
            modifier = Modifier.size(17.dp)
        )
        Spacer(modifier = Modifier.width(7.dp))
        BasicTextField(
            value = searchText,
            onValueChange = onSearchChange,
            singleLine = true,
            textStyle = TextStyle(
                fontSize = 10.sp,
                color = TextPrimary
            ),
            cursorBrush = SolidColor(TextPrimary),
            modifier = Modifier.fillMaxWidth(),
            decorationBox = { innerTextField ->
                Box {
                    if (searchText.isEmpty()) {
                        Text(
                            text = placeholder,
                            fontSize = 10.sp,
                            color = TextSecondary
                        )
                    }
                    innerTextField()
                }
            }
        )
    }
}

@Composable
fun SessionFilterE(
    selectedOption: String,
    options: List<String>,
    onOptionSelected: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    var expanded by remember {
        mutableStateOf(false)
    }
    Box(
        modifier = modifier
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(30.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(FieldBackground)
                .border(
                    width = 1.dp,
                    color = BorderColor,
                    shape = RoundedCornerShape(6.dp)
                )
                .clickable(role = Role.DropdownList) {
                    expanded = true
                }
                .padding(horizontal = 7.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                imageVector = Icons.Default.KeyboardArrowDown,
                contentDescription = null,
                tint = TextSecondary,
                modifier = Modifier.size(14.dp)
            )

            Spacer(modifier = Modifier.width(3.dp))

            Text(
                text = selectedOption,
                fontSize = 10.sp,
                color = TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = {
                expanded = false
            }
        ) {
            options.forEach { option ->

                DropdownMenuItem(
                    text = {
                        Text(
                            text = option,
                            fontSize = 12.sp
                        )
                    },
                    onClick = {
                        onOptionSelected(option)
                        expanded = false
                    }
                )
            }
        }
    }
}

/** Desplegable de [FilterOption] reutilizando el diseño de SessionFilter. */
@Composable
fun OptionFilter(
    selected: String?,
    options: List<FilterOption>,
    onSelected: (String?) -> Unit,
    modifier: Modifier = Modifier
) {
    SessionFilter(
        selectedOption = options.firstOrNull { it.value == selected }?.label ?: options.first().label,
        options = options.map { it.label },
        onOptionSelected = { label -> onSelected(options.firstOrNull { it.label == label }?.value) },
        modifier = modifier
    )
}


@Composable
fun SessionFiltersE(
    selectedStatus: String?,
    selectedRoom: String?,
    selectedProcedure: String?,
    rooms: List<String>,
    procedures: List<String>,
    onRoomChange: (String?) -> Unit,
    onStatusChange: (String?) -> Unit,
    onProcedureChange: (String?) -> Unit
) {
    val statusOptions = listOf(FilterOption(null, stringResource(R.string.filter_all_status))) +
        SessionStatus.ALL.map { FilterOption(it, statusLabel(it)) }
    val roomOptions = listOf(FilterOption(null, stringResource(R.string.filter_all_rooms))) +
        rooms.map { FilterOption(it, it) }
    val procedureOptions = listOf(FilterOption(null, stringResource(R.string.filter_all_procedures))) +
        procedures.map { FilterOption(it, it) }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(13.dp)
    ) {
        OptionFilter(
            selected = selectedStatus,
            options = statusOptions,
            onSelected = onStatusChange,
            modifier = Modifier.weight(1f)
        )
        OptionFilter(
            selected = selectedRoom,
            options = roomOptions,
            onSelected = onRoomChange,
            modifier = Modifier.weight(1.1f)
        )
        OptionFilter(
            selected = selectedProcedure,
            options = procedureOptions,
            onSelected = onProcedureChange,
            modifier = Modifier.weight(1f)
        )
    }
}
