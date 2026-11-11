package com.example.cursobasico.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation3.runtime.NavKey
import com.example.cursobasico.Background
import com.example.cursobasico.BuildConfig
import com.example.cursobasico.DashboardBottomBarE
import com.example.cursobasico.DashboardTopBar
import com.example.cursobasico.R
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.auth.AuthState
import com.example.cursobasico.core.Menu
import com.example.cursobasico.preferences.AppLanguage
import com.example.cursobasico.preferences.ThemeMode
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.PrimaryButton
import com.example.cursobasico.ui.components.pef
import com.example.cursobasico.ui.components.roleLabelRes
import com.example.cursobasico.ui.viewmodel.SettingsViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel
import com.example.cursobasico.views.DetailField
import com.example.cursobasico.views.DetailSectionCard
import com.example.cursobasico.views.SessionDetailHeader

/** ANDROID-01: preferencias del usuario (DataStore) y cuenta. Mismo lenguaje visual que los detalles. */
@Composable
fun SettingsView(onBack: () -> Unit, onNavigate: (NavKey) -> Unit) {
    val viewModel = pefViewModel { SettingsViewModel(it.preferencesRepository, it.sessionManager) }
    val prefs by viewModel.prefs.collectAsStateWithLifecycle()
    val auth by viewModel.auth.collectAsStateWithLifecycle()
    val loggingOut by viewModel.loggingOut.collectAsStateWithLifecycle()
    var confirmLogout by rememberSaveable { mutableStateOf(false) }
    val user = (auth as? AuthState.LoggedIn)?.user

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Background,
        topBar = { DashboardTopBar() },
        bottomBar = { DashboardBottomBarE(currentRoute = Menu, onNavigate = onNavigate) }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Spacer(modifier = Modifier.height(2.dp))
            SessionDetailHeader(
                onBack = onBack,
                title = stringResource(R.string.settings_title),
                subtitle = stringResource(R.string.settings_subtitle)
            )

            DetailSectionCard(title = stringResource(R.string.settings_account)) {
                DetailField(stringResource(R.string.settings_name), user?.name ?: "—")
                DetailField(stringResource(R.string.settings_email), user?.email ?: "—")
                DetailField(
                    stringResource(R.string.settings_roles),
                    user?.roles?.map { stringResource(roleLabelRes(it)) }?.joinToString(", ")?.ifBlank { null } ?: "—"
                )
                if ((auth as? AuthState.LoggedIn)?.offline == true) {
                    Text(stringResource(R.string.settings_offline_session), fontSize = 10.sp, color = pef.orange)
                }
            }

            DetailSectionCard(title = stringResource(R.string.settings_language)) {
                Column(Modifier.selectableGroup()) {
                    ChoiceRow(stringResource(R.string.settings_language_system), prefs.language == AppLanguage.SYSTEM) {
                        viewModel.setLanguage(AppLanguage.SYSTEM)
                    }
                    ChoiceRow(stringResource(R.string.settings_language_es), prefs.language == AppLanguage.SPANISH) {
                        viewModel.setLanguage(AppLanguage.SPANISH)
                    }
                    ChoiceRow(stringResource(R.string.settings_language_en), prefs.language == AppLanguage.ENGLISH) {
                        viewModel.setLanguage(AppLanguage.ENGLISH)
                    }
                }
            }

            DetailSectionCard(title = stringResource(R.string.settings_theme)) {
                Column(Modifier.selectableGroup()) {
                    ChoiceRow(stringResource(R.string.settings_theme_system), prefs.theme == ThemeMode.SYSTEM) {
                        viewModel.setTheme(ThemeMode.SYSTEM)
                    }
                    ChoiceRow(stringResource(R.string.settings_theme_light), prefs.theme == ThemeMode.LIGHT) {
                        viewModel.setTheme(ThemeMode.LIGHT)
                    }
                    ChoiceRow(stringResource(R.string.settings_theme_dark), prefs.theme == ThemeMode.DARK) {
                        viewModel.setTheme(ThemeMode.DARK)
                    }
                }
            }

            DetailSectionCard(title = stringResource(R.string.settings_notifications)) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 40.dp)
                        .toggleable(
                            value = prefs.inAppNotifications,
                            role = Role.Switch,
                            onValueChange = viewModel::setInAppNotifications
                        ),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        stringResource(R.string.settings_in_app_notifications),
                        fontSize = 11.sp,
                        color = TextPrimary,
                        modifier = Modifier.weight(1f)
                    )
                    Switch(
                        checked = prefs.inAppNotifications,
                        onCheckedChange = null,
                        colors = SwitchDefaults.colors(checkedTrackColor = pef.action)
                    )
                }
                Text(stringResource(R.string.settings_push_note), fontSize = 9.sp, color = TextSecondary)
            }

            InfoBanner(stringResource(R.string.settings_sync_note))

            DetailSectionCard(title = stringResource(R.string.settings_about)) {
                DetailField(stringResource(R.string.settings_gateway), BuildConfig.API_BASE_URL)
                DetailField(stringResource(R.string.settings_version), BuildConfig.VERSION_NAME)
            }

            PrimaryButton(
                text = stringResource(R.string.settings_logout),
                onClick = { confirmLogout = true },
                loading = loggingOut,
                modifier = Modifier.fillMaxWidth().height(44.dp)
            )
            Spacer(modifier = Modifier.height(24.dp))
        }
    }

    if (confirmLogout) {
        AlertDialog(
            onDismissRequest = { confirmLogout = false },
            title = { Text(stringResource(R.string.settings_logout_confirm_title)) },
            text = { Text(stringResource(R.string.settings_logout_confirm_text)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmLogout = false
                    // Al terminar, AuthState pasa a LoggedOut y AppNavigation vuelve a Login.
                    viewModel.logout { }
                }) { Text(stringResource(R.string.settings_logout)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmLogout = false }) { Text(stringResource(R.string.action_cancel)) }
            }
        )
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 40.dp)
            .selectable(selected = selected, role = Role.RadioButton, onClick = onSelect),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(
            selected = selected,
            onClick = null,
            colors = RadioButtonDefaults.colors(selectedColor = pef.blue, unselectedColor = TextSecondary)
        )
        Spacer(Modifier.width(8.dp))
        Text(label, fontSize = 11.sp, color = TextPrimary, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
    }
}
