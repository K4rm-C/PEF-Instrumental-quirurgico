package com.example.cursobasico.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.R
import com.example.cursobasico.data.model.SessionStatus
import com.example.cursobasico.ui.state.ErrorKind
import com.example.cursobasico.ui.state.UiState
import com.example.cursobasico.ui.theme.LocalPefColors
import com.example.cursobasico.ui.theme.PefColors

val pef: PefColors
    @Composable @ReadOnlyComposable get() = LocalPefColors.current

/** Acciones de la barra superior compartida (campana y avatar), provistas por AppNavigation. */
data class TopBarState(
    val unreadCount: Int? = null,
    val showUnreadBadge: Boolean = true,
    val userInitials: String = "",
    val onBellClick: () -> Unit = {},
    val onAvatarClick: () -> Unit = {},
)

val LocalTopBarState = staticCompositionLocalOf { TopBarState() }

/** Pinta el contenido según el estado: carga, vacío, error (con reintento), sin permiso o datos. */
@Composable
fun <T> StateContent(
    state: UiState<T>,
    onRetry: () -> Unit,
    emptyText: String,
    content: @Composable (T) -> Unit,
) {
    when (state) {
        UiState.Loading -> LoadingBox()
        UiState.Empty -> MessageCard(Icons.Default.Inbox, emptyText)
        UiState.NotPermitted -> MessageCard(Icons.Default.Lock, stringResource(R.string.state_not_permitted))
        is UiState.Error -> ErrorCard(state.kind, state.error.serverMessage, state.error.correlationId, onRetry)
        is UiState.Success -> content(state.data)
    }
}

@Composable
fun LoadingBox(modifier: Modifier = Modifier) {
    val description = stringResource(R.string.state_loading)
    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 32.dp)
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center,
    ) {
        CircularProgressIndicator(color = pef.blue, strokeWidth = 3.dp, modifier = Modifier.size(32.dp))
    }
}

@Composable
fun ErrorCard(kind: ErrorKind, serverMessage: String?, correlationId: String?, onRetry: (() -> Unit)?) {
    val icon = when (kind) {
        ErrorKind.OFFLINE, ErrorKind.TIMEOUT -> Icons.Default.CloudOff
        ErrorKind.FORBIDDEN, ErrorKind.UNAUTHORIZED -> Icons.Default.Lock
        else -> Icons.Default.ErrorOutline
    }
    val detail = listOfNotNull(
        serverMessage?.takeIf { it.isNotBlank() },
        correlationId?.let { stringResource(R.string.error_reference, it) },
    ).joinToString("\n").ifBlank { null }
    MessageCard(
        icon = icon,
        title = stringResource(errorTitleRes(kind)),
        detail = detail,
        actionLabel = if (onRetry != null && kind != ErrorKind.FORBIDDEN) stringResource(R.string.action_retry) else null,
        onAction = onRetry,
        accent = pef.red,
    )
}

@Composable
fun MessageCard(
    icon: ImageVector,
    title: String,
    detail: String? = null,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
    accent: androidx.compose.ui.graphics.Color = pef.textSecondary,
) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
        shape = RoundedCornerShape(11.dp),
        colors = CardDefaults.cardColors(containerColor = pef.surface),
        border = BorderStroke(1.dp, pef.border),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(icon, contentDescription = null, tint = accent, modifier = Modifier.size(26.dp))
            Spacer(Modifier.height(8.dp))
            Text(title, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = pef.textPrimary, textAlign = TextAlign.Center)
            if (detail != null) {
                Spacer(Modifier.height(4.dp))
                Text(detail, fontSize = 10.sp, color = pef.textSecondary, textAlign = TextAlign.Center)
            }
            if (actionLabel != null && onAction != null) {
                Spacer(Modifier.height(12.dp))
                PrimaryButton(text = actionLabel, onClick = onAction)
            }
        }
    }
}

/** Aviso informativo (función sin endpoint publicado, datos derivados, etc.). */
@Composable
fun InfoBanner(text: String, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(9.dp))
            .background(pef.lightBlue)
            .padding(horizontal = 12.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Default.Info, contentDescription = null, tint = pef.blue, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(8.dp))
        Text(text, fontSize = 10.sp, lineHeight = 13.sp, color = pef.textPrimary)
    }
}

@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
) {
    Button(
        onClick = onClick,
        enabled = enabled && !loading,
        modifier = modifier.height(32.dp),
        shape = RoundedCornerShape(9.dp),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = pef.action,
            contentColor = pef.onAction,
            disabledContainerColor = pef.action.copy(alpha = 0.5f),
            disabledContentColor = pef.onAction.copy(alpha = 0.8f),
        ),
    ) {
        if (loading) {
            CircularProgressIndicator(color = pef.onAction, strokeWidth = 2.dp, modifier = Modifier.size(14.dp))
            Spacer(Modifier.width(6.dp))
        }
        Text(text, fontSize = 10.sp, fontWeight = FontWeight.Medium)
    }
}

/** Botón de actualización manual (sin polling permanente). */
@Composable
fun RefreshButton(onClick: () -> Unit, refreshing: Boolean) {
    IconButton(onClick = onClick, enabled = !refreshing, modifier = Modifier.size(32.dp)) {
        if (refreshing) {
            CircularProgressIndicator(color = pef.textSecondary, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
        } else {
            Icon(Icons.Default.Refresh, contentDescription = stringResource(R.string.action_refresh), tint = pef.textSecondary)
        }
    }
}

/** Insignia de estado de sesión con los colores del diseño original. */
@Composable
fun SessionStatusPill(code: String, modifier: Modifier = Modifier) {
    val (bg, fg, dot) = when (code) {
        SessionStatus.IN_PROGRESS -> Triple(pef.badgeActiveBg, pef.badgeActiveText, pef.badgeActiveDot)
        in SessionStatus.ON_HOLD -> Triple(pef.badgeHoldBg, pef.badgeHoldText, pef.badgeHoldDot)
        SessionStatus.CLOSED -> Triple(pef.successContainer, pef.success, pef.success)
        else -> Triple(pef.badgeNeutralBg, pef.badgeNeutralText, pef.badgeNeutralText)
    }
    Pill(statusLabel(code), bg, fg, dot, modifier)
}

@Composable
fun Pill(
    text: String,
    background: androidx.compose.ui.graphics.Color,
    textColor: androidx.compose.ui.graphics.Color,
    dotColor: androidx.compose.ui.graphics.Color,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .clip(CircleShape)
            .background(background)
            .padding(horizontal = 7.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Start,
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(dotColor))
        Spacer(Modifier.width(4.dp))
        Text(text, fontSize = 9.sp, color = textColor, maxLines = 1)
    }
}
