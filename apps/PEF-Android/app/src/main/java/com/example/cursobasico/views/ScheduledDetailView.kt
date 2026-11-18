package com.example.cursobasico.views

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.R
import com.example.cursobasico.data.model.SessionDetailDto


@Composable
fun ScheduledView(sessionId: String, onBack: () -> Unit, onNavigate: (NavKey) -> Unit){
    var selectedSubmenu by rememberSaveable {
        mutableIntStateOf(1)
    }
    SessionDetailFrame(sessionId = sessionId, onBack = onBack, onNavigate = onNavigate) { detail ->
        SessionSubmenuS(
            selectedSubmenu = selectedSubmenu,
            onSubmenuSelected = {
                selectedSubmenu = it
            }
        )
        Spacer(modifier = Modifier.height(18.dp))
        when (selectedSubmenu) {
            1 -> SessionInfoContentS(detail)
            2 -> InventoryContentS(detail)
        }
    }
}

@Composable
fun SessionSubmenuS(
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
        }
    }
}

/** Misma información que una sesión en curso (los campos aún no ocurridos se muestran como "—"). */
@Composable
fun SessionInfoContentS(detail: SessionDetailDto) = SessionInfoContent(detail)

@Composable
fun InventoryContentS(detail: SessionDetailDto) = InventoryContent(detail)
