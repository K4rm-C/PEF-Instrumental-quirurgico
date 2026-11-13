package com.example.cursobasico.views

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.ActionColor
import com.example.cursobasico.Background
import com.example.cursobasico.BorderColor
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.core.Personal
import com.example.cursobasico.core.PersonalDetail
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.RefreshButton
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.viewmodel.PersonalViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel

@Composable
fun PersonalView( onNavigate: (NavKey) -> Unit,onSessionClick: (NavKey) -> Unit){
    val viewModel = pefViewModel { PersonalViewModel(it.sessionRepository, it.sessionManager) }
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.load() }
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
                currentRoute = Personal,
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
                PersonalHeader(refreshing = state is UiState.Loading, onRefresh = viewModel::load)
                Spacer(modifier = Modifier.height(14.dp))
                SearchBarPersonalE (searchText = searchText,
                    onSearchChange = {
                        searchText = it
                    })
                Spacer(modifier = Modifier.height(13.dp))
                InfoBanner(stringResource(R.string.personal_scope_note))
                Spacer(modifier = Modifier.height(13.dp))
                StateContent(
                    state = state,
                    onRetry = viewModel::load,
                    emptyText = stringResource(R.string.personal_empty)
                ) { operators ->
                    val filtered = operators.filter { searchText.isBlank() || it.name.contains(searchText.trim(), ignoreCase = true) }
                    if (filtered.isEmpty()) {
                        MessageCard(Icons.Default.Search, stringResource(R.string.personal_no_match))
                    }
                    filtered.forEach { operator ->
                        val open = { onSessionClick(PersonalDetail(operator.id, operator.name)) }
                        PersonalCardE(
                            name = operator.name,
                            role = pluralStringResource(R.plurals.personal_role_operator_sessions, operator.sessions.size, operator.sessions.size),
                            onClick = open
                        )
                        Spacer(modifier = Modifier.height(13.dp))
                    }
                }
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
fun PersonalHeader(refreshing: Boolean = false, onRefresh: () -> Unit = {}){
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.personal_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = stringResource(R.string.personal_subtitle),
                fontSize = 11.sp,
                lineHeight = 14.sp,
                color = TextSecondary
            )
        }
        RefreshButton(onClick = onRefresh, refreshing = refreshing)
    }
}

@Composable
fun PersonalCardE(
    name: String,
    role: String,
    onClick: () -> Unit
) {
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
                    text = name,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )

                Spacer(modifier = Modifier.height(1.dp))

                Text(
                    text = role,
                    fontSize = 10.sp,
                    color = TextSecondary
                )
            }
            Button(
                onClick = onClick,
                colors = ButtonDefaults.buttonColors(
                    containerColor = ActionColor,
                    contentColor = OnActionColor
                ),
                contentPadding = PaddingValues(
                    horizontal = 9.dp,
                    vertical = 4.dp
                ),
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.height(28.dp)
            ) {
                Text(
                    text = stringResource(R.string.personal_view_sessions),
                    fontSize = 9.sp,
                    color = OnActionColor,
                    maxLines = 1
                )
            }
        }
    }
}

@Composable
fun SearchBarPersonalE(
    searchText: String,
    onSearchChange: (String) -> Unit
) {
    SearchBarSessionsE(
        searchText = searchText,
        onSearchChange = onSearchChange,
        placeholder = stringResource(R.string.personal_search_placeholder)
    )
}
