package com.example.cursobasico.views

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.Background
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.R
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.core.Personal
import com.example.cursobasico.core.procedureRoomText
import com.example.cursobasico.core.sessionRouteFor
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.data.model.SessionSummaryDto
import com.example.cursobasico.data.model.sessionLabel
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.formatDateTime
import com.example.cursobasico.ui.components.parseInstant
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.viewmodel.PersonalViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel

@Composable
fun PersonalDetailView(
    operatorId: String,
    operatorName: String,
    onBack: () -> Unit,
    onNavigate: (NavKey) -> Unit,
    onSessionClick: (NavKey) -> Unit
){
    val viewModel = pefViewModel { PersonalViewModel(it.sessionRepository, it.sessionManager) }
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { if (state !is UiState.Success) viewModel.load() }
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Background,
        topBar = {
            DashboardTopBar()
        },
        bottomBar = {
            DashboardBottomBarE(
                currentRoute = Personal,
                onNavigate = onNavigate
            )
        }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
        ) {
            Spacer(modifier = Modifier.height(14.dp))
            PersonalDetailHeader(
                name = operatorName,
                onBack = onBack,
                refreshing = state is UiState.Loading,
                onRefresh = viewModel::load
            )
            Spacer(modifier = Modifier.height(16.dp))
            StateContent(state = state, onRetry = viewModel::load, emptyText = stringResource(R.string.personal_empty)) { operators ->
                val sessions = operators.firstOrNull { it.id == operatorId }?.sessions.orEmpty()
                    .sortedByDescending { parseInstant(it.startedAt ?: it.operation?.scheduledAt) }
                val active = sessions.filter { it.status.code == SessionStatus.IN_PROGRESS || it.status.code in SessionStatus.ON_HOLD }
                val scheduled = sessions.filter { it.status.code == SessionStatus.SCHEDULED }
                val history = sessions.filter { it.status.code == SessionStatus.CLOSED || it.status.code == SessionStatus.ABORTED }
                PersonalSessionSection(stringResource(R.string.personal_active_sessions), active, onSessionClick)
                PersonalSessionSection(stringResource(R.string.personal_scheduled_sessions), scheduled, onSessionClick)
                PersonalSessionSection(stringResource(R.string.personal_session_history), history, onSessionClick)
            }
            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

@Composable
private fun PersonalSessionSection(title: String, sessions: List<SessionSummaryDto>, onSessionClick: (NavKey) -> Unit) {
    Text(
        text = title,
        fontSize = 14.sp,
        fontWeight = FontWeight.Bold,
        color = TextPrimary
    )
    Spacer(modifier = Modifier.height(16.dp))
    if (sessions.isEmpty()) {
        InfoBanner(stringResource(R.string.personal_section_empty))
    }
    sessions.forEach { session ->
        SessionCardE(
            code = sessionLabel(session.id),
            description = session.procedureRoomText(),
            statusCode = session.status.code,
            operator = formatDateTime(session.startedAt ?: session.operation?.scheduledAt),
            onClick = { onSessionClick(sessionRouteFor(session.id, session.status.code)) }
        )
        Spacer(modifier = Modifier.height(13.dp))
    }
    Spacer(modifier = Modifier.height(16.dp))
}

@Composable
fun PersonalDetailHeader(
    name: String,
    onBack: () -> Unit,
    refreshing: Boolean = false,
    onRefresh: () -> Unit = {}
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
            RefreshButton(onClick = onRefresh, refreshing = refreshing)
        }
        Spacer(modifier = Modifier.height(3.dp))
        Text(
            text = name,
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Text(
            text = stringResource(R.string.personal_session_operator),
            fontSize = 11.sp,
            color = TextSecondary
        )
    }
}
