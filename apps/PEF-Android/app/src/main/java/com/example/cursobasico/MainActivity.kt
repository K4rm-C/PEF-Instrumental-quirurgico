package com.example.cursobasico

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Assignment
import androidx.compose.material.icons.automirrored.filled.FactCheck
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.core.AlertsOnHold
import com.example.cursobasico.core.AppNavigation
import com.example.cursobasico.core.Menu
import com.example.cursobasico.core.Personal
import com.example.cursobasico.core.Sessions
import com.example.cursobasico.preferences.LocaleApplier
import com.example.cursobasico.ui.components.LocalTopBarState
import com.example.cursobasico.ui.theme.LocalPefColors
import com.example.cursobasico.ui.theme.PefTheme
import kotlinx.coroutines.runBlocking

// -----------------------------------------
// COLORES
// -----------------------------------------
// ANDROID-01: los nombres originales se conservan, pero ahora leen la paleta semántica del tema activo
// (ui/theme/Color.kt) para que claro/oscuro/automático afecte a todas las pantallas.

val Navy: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.navy
val Background: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.background
val Blue: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.blue
val LightBlue: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.lightBlue
val Orange: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.orange
val LightOrange: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.lightOrange
val Red: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.red
val LightRed: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.lightRed
val TextPrimary: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.textPrimary
val TextSecondary: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.textSecondary
val BorderColor: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.border
/** Superficie de tarjetas (antes Color.White fijo). */
val SurfaceColor: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.surface
/** Fondo de campos y tablas (antes Color(0xFFF7F9FC) fijo). */
val FieldBackground: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.fieldBackground
/** Botones primarios / pestaña activa (antes Navy). */
val ActionColor: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.action
val OnActionColor: Color @Composable @ReadOnlyComposable get() = LocalPefColors.current.onAction

// -----------------------------------------
// MAIN ACTIVITY
// -----------------------------------------

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as PefApplication).container
        container.restoreSessionOnce()
        // Primera lectura síncrona (archivo local pequeño) para no parpadear con el tema equivocado.
        val initialPreferences = runBlocking { container.preferencesRepository.current() }
        setContent {
            val preferences by container.preferencesRepository.preferences
                .collectAsStateWithLifecycle(initialValue = initialPreferences)
            LaunchedEffect(preferences.language) { LocaleApplier.apply(preferences.language) }
            PefTheme(mode = preferences.theme) {
                AppNavigation(container = container, preferences = preferences)
            }
        }
    }
}


// -----------------------------------------
// ENCABEZADO SUPERIOR
// -----------------------------------------
@Composable
fun DashboardTopBar() {
    val topBar = LocalTopBarState.current
    val unread = topBar.unreadCount?.takeIf { topBar.showUnreadBadge && it > 0 }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Navy)
            .statusBarsPadding()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(57.dp)
                .padding(horizontal = 18.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f)
            ) {
                Text(
                    text = stringResource(R.string.topbar_title),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = LocalPefColors.current.brandAccent
                )
                Spacer(
                    modifier = Modifier.height(3.dp)
                )

                Text(
                    text = stringResource(R.string.topbar_subtitle),
                    fontSize = 11.sp,
                    color = LocalPefColors.current.onNavy
                )
            }
            // Botón de notificaciones (NotificationsService: contador real de no leídas)
            val bellDescription = if (unread != null) {
                pluralStringResource(R.plurals.cd_notifications_unread, unread, unread)
            } else stringResource(R.string.cd_notifications)
            BadgedBox(
                badge = {
                    if (unread != null) {
                        Badge(containerColor = LocalPefColors.current.red, contentColor = Color.White) {
                            Text(if (unread > 99) "99+" else unread.toString(), fontSize = 8.sp)
                        }
                    }
                }
            ) {
                Box(
                    modifier = Modifier
                        .size(27.dp)
                        .clip(CircleShape)
                        .background(LocalPefColors.current.onNavy)
                        .clickable(role = Role.Button, onClickLabel = bellDescription) { topBar.onBellClick() },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Notifications,
                        contentDescription = bellDescription,
                        tint = Navy,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
            Spacer(
                modifier = Modifier.width(9.dp)
            )
            // Avatar del usuario autenticado (iniciales reales) -> Ajustes
            val settingsDescription = stringResource(R.string.cd_open_settings)
            Box(
                modifier = Modifier
                    .size(27.dp)
                    .clip(CircleShape)
                    .background(LocalPefColors.current.navIndicator)
                    .border(
                        width = 1.dp,
                        color = LocalPefColors.current.onNavy.copy(alpha = 0.4f),
                        shape = CircleShape
                    )
                    .clickable(role = Role.Button, onClickLabel = settingsDescription) { topBar.onAvatarClick() },
                contentAlignment = Alignment.Center
            ) {
                if (topBar.userInitials.isNotBlank()) {
                    Text(
                        text = topBar.userInitials,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        color = LocalPefColors.current.onNavy
                    )
                } else {
                    Icon(
                        imageVector = Icons.Default.Person,
                        contentDescription = settingsDescription,
                        tint = LocalPefColors.current.onNavy,
                        modifier = Modifier.size(16.dp)
                    )
                }
            }
        }
    }
}

// -----------------------------------------
// BARRA INFERIOR
// -----------------------------------------
@Composable
fun DashboardBottomBarE(
    currentRoute: NavKey,
    onNavigate: (NavKey) -> Unit
) {
    val destinations = listOf(
        Menu,
        AlertsOnHold,
        Sessions,
        Personal
    )
    val icons = listOf(
        Icons.Default.Home,
        Icons.Default.Warning,
        Icons.AutoMirrored.Filled.Assignment,
        Icons.AutoMirrored.Filled.FactCheck
    )
    val labels = listOf(
        stringResource(R.string.nav_dashboard),
        stringResource(R.string.nav_alerts),
        stringResource(R.string.nav_sessions),
        stringResource(R.string.nav_personal)
    )
    NavigationBar(
        modifier = Modifier.height(49.dp),
        containerColor = Navy,
        contentColor = LocalPefColors.current.onNavy,
        tonalElevation = 0.dp
    ) {
        destinations.forEachIndexed { index, destination ->
            NavigationBarItem(
                selected = currentRoute == destination,
                onClick = {
                    onNavigate(destination)
                },
                icon = {
                    Icon(
                        imageVector = icons[index],
                        contentDescription = labels[index],
                        modifier = Modifier.size(22.dp)
                    )
                },
                alwaysShowLabel = false,
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = LocalPefColors.current.onNavy,
                    unselectedIconColor = LocalPefColors.current.onNavy,
                    indicatorColor = LocalPefColors.current.navIndicator
                )
            )
        }
    }
}
