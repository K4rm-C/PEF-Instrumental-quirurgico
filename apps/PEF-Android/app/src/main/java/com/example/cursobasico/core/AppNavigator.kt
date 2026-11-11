package com.example.cursobasico.core

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import android.app.Activity
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import androidx.navigation3.runtime.entryProvider
import androidx.navigation3.runtime.rememberNavBackStack
import androidx.navigation3.ui.NavDisplay
import com.example.cursobasico.AppContainer
import com.example.cursobasico.R
import com.example.cursobasico.auth.AuthState
import com.example.cursobasico.preferences.UserPreferences
import com.example.cursobasico.ui.components.LocalTopBarState
import com.example.cursobasico.ui.components.TopBarState
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.screens.SettingsView
import com.example.cursobasico.views.Alerts_OnHoldView
import com.example.cursobasico.views.InProgressScreen
import com.example.cursobasico.views.LoginLogo
import com.example.cursobasico.views.LoginScreen
import com.example.cursobasico.views.OnHoldDetailView
import com.example.cursobasico.views.PersonalDetailView
import com.example.cursobasico.views.PersonalView
import com.example.cursobasico.views.ScheduledView
import com.example.cursobasico.views.SessionsView
import com.example.cursobasico.views.SurgicalDashboard

@Composable
fun AppNavigation(container: AppContainer, preferences: UserPreferences) {
    val authState by container.sessionManager.state.collectAsStateWithLifecycle()
    if (authState is AuthState.Restoring) {
        RestoringSplash()
        return
    }
    val backStack = rememberNavBackStack(Login)
    fun navigateTo(destination: NavKey) {
        if (backStack.lastOrNull() != destination) {
            backStack.clear()
            backStack.add(destination)
        }
    }
    fun navigateToDetail(destination: NavKey) {
        if (backStack.lastOrNull() != destination) {
            backStack.add(destination)
        }
    }
    fun navigateBack() {
        if (backStack.size > 1) {
            backStack.removeAt(backStack.lastIndex)
        }
    }

    // Protección de rutas: sin sesión válida solo existe Login; con sesión, Login nunca queda en la pila.
    LaunchedEffect(authState) {
        when (authState) {
            is AuthState.LoggedOut -> if (backStack.toList() != listOf(Login)) {
                backStack.clear()
                backStack.add(Login)
            }
            is AuthState.LoggedIn -> if (backStack.contains(Login)) {
                backStack.clear()
                backStack.add(Menu)
            }
            AuthState.Restoring -> Unit
        }
    }

    // Contador de no leídas: se consulta al cambiar de pantalla (sin polling permanente).
    val unread by container.notificationRepository.unread.collectAsStateWithLifecycle()
    val current = backStack.lastOrNull()
    LaunchedEffect(current, preferences.inAppNotifications) {
        if (authState is AuthState.LoggedIn && current != Login && preferences.inAppNotifications) {
            container.notificationRepository.refreshUnreadCount()
        }
    }
    StatusBarIcons(darkIcons = current == Login && !pef.isDark)
    val user = (authState as? AuthState.LoggedIn)?.user
    val topBarState = TopBarState(
        unreadCount = unread,
        showUnreadBadge = preferences.inAppNotifications,
        userInitials = user?.name.orEmpty().split(" ").filter { it.isNotBlank() }.take(2)
            .joinToString("") { it.first().uppercase() },
        onBellClick = { navigateTo(AlertsOnHold) },
        onAvatarClick = { navigateToDetail(Settings) },
    )

    CompositionLocalProvider(LocalTopBarState provides topBarState) {
        NavDisplay(backStack = backStack, onBack = { navigateBack() }, entryProvider = entryProvider {
            entry<Login> {
                LoginScreen(logoutReason = (authState as? AuthState.LoggedOut)?.reason)
            }
            entry<Menu> {
                SurgicalDashboard(onNavigate = { destination ->
                    navigateTo(destination)
                }, onSessionClick = { destination ->
                    navigateToDetail(destination)
                })
            }
            entry<AlertsOnHold> {
                Alerts_OnHoldView(onNavigate = { destination ->
                    navigateTo(destination)
                }, onSessionClick = { destination ->
                    navigateToDetail(destination)
                })
            }
            entry<Sessions> {
                SessionsView(onNavigate = { destination ->
                    navigateTo(destination)
                }, onSessionClick = { destination ->
                    navigateToDetail(destination)
                })
            }
            entry<Personal> {
                PersonalView(onNavigate = { destination ->
                    navigateTo(destination)
                }, onSessionClick = { destination ->
                    navigateToDetail(destination)
                })
            }
            entry<SessionInProgressDetail> { key ->
                InProgressScreen(sessionId = key.sessionId, onBack = {
                    navigateBack()
                }, onNavigate = { destination ->
                    navigateTo(destination)
                })
            }
            entry<OnHoldDetail> { key ->
                OnHoldDetailView(sessionId = key.sessionId, onBack = {
                    navigateBack()
                },
                    onNavigate = { destination ->
                        navigateTo(destination)
                    })
            }
            entry<SessionScheduledDetail> { key ->
                ScheduledView(sessionId = key.sessionId, onBack = {
                    navigateBack()
                },
                    onNavigate = { destination ->
                        navigateTo(destination)
                    })
            }
            entry<PersonalDetail> { key ->
                PersonalDetailView(operatorId = key.operatorId, operatorName = key.operatorName, onBack = {
                    navigateBack()
                }, onNavigate = { destination ->
                    navigateTo(destination)
                }, onSessionClick = { destination ->
                    navigateToDetail(destination)
                })
            }
            entry<Settings> {
                SettingsView(onBack = { navigateBack() }, onNavigate = { destination -> navigateTo(destination) })
            }
        })
    }
}

/** Iconos de la barra de estado legibles: oscuros sobre el Login claro, claros sobre la barra navy del resto. */
@Composable
private fun StatusBarIcons(darkIcons: Boolean) {
    val view = LocalView.current
    if (view.isInEditMode) return
    SideEffect {
        val window = (view.context as? Activity)?.window ?: return@SideEffect
        WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = darkIcons
    }
}

@Composable
private fun RestoringSplash() {
    StatusBarIcons(darkIcons = !pef.isDark)
    Box(Modifier.fillMaxSize().background(pef.background), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            LoginLogo()
            Spacer(Modifier.height(16.dp))
            CircularProgressIndicator(color = pef.blue)
            Spacer(Modifier.height(10.dp))
            Text(stringResource(R.string.restoring_session), fontSize = 11.sp, color = pef.textSecondary)
        }
    }
}
