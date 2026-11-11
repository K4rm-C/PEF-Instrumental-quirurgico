package com.example.cursobasico.preferences

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.example.cursobasico.data.model.UiPreferencesDto
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.io.IOException

/** Idioma de la interfaz. SYSTEM = idioma del dispositivo (valor inicial). */
enum class AppLanguage(val tag: String?) {
    SYSTEM(null), SPANISH("es"), ENGLISH("en");

    companion object {
        fun fromStored(value: String?): AppLanguage = entries.firstOrNull { it.name == value } ?: SYSTEM

        /** ui_preferences.locale de AuthService: "en" | "es-MX". */
        fun fromServerLocale(locale: String?): AppLanguage? = when {
            locale == null -> null
            locale.startsWith("es") -> SPANISH
            locale.startsWith("en") -> ENGLISH
            else -> null
        }
    }
}

enum class ThemeMode {
    SYSTEM, LIGHT, DARK;

    companion object {
        fun fromStored(value: String?): ThemeMode = entries.firstOrNull { it.name == value } ?: SYSTEM

        /** ui_preferences.theme de AuthService: "light" | "dark" | "system". */
        fun fromServer(theme: String?): ThemeMode? = when (theme) {
            "light" -> LIGHT
            "dark" -> DARK
            "system" -> SYSTEM
            else -> null
        }
    }
}

/**
 * Preferencias NO sensibles del usuario (nunca tokens: esos van en SecureTokenStore).
 *
 * [inAppNotifications] es una preferencia de interfaz (mostrar la insignia de no leídas y consultar el contador al
 * abrir pantallas). No es un permiso de notificaciones push: la app no tiene push (ver README).
 */
data class UserPreferences(
    val language: AppLanguage = AppLanguage.SYSTEM,
    val theme: ThemeMode = ThemeMode.SYSTEM,
    val inAppNotifications: Boolean = true,
    /** El usuario eligió idioma/tema en esta app; entonces ui_preferences del servidor ya no lo sobrescribe. */
    val languageChosen: Boolean = false,
    val themeChosen: Boolean = false,
    /** Versión del esquema de preferencias, para migraciones futuras. */
    val schemaVersion: Int = PreferencesRepository.SCHEMA_VERSION,
)

class PreferencesRepository(private val dataStore: DataStore<Preferences>) {

    val preferences: Flow<UserPreferences> = dataStore.data
        .catch { e -> if (e is IOException) emit(emptyPreferences()) else throw e }
        .map { it.toUserPreferences() }

    suspend fun current(): UserPreferences = preferences.first()

    suspend fun setLanguage(language: AppLanguage) {
        dataStore.edit {
            it[Keys.LANGUAGE] = language.name
            it[Keys.LANGUAGE_CHOSEN] = true
        }
    }

    suspend fun setTheme(theme: ThemeMode) {
        dataStore.edit {
            it[Keys.THEME] = theme.name
            it[Keys.THEME_CHOSEN] = true
        }
    }

    suspend fun setInAppNotifications(enabled: Boolean) {
        dataStore.edit { it[Keys.IN_APP_NOTIFICATIONS] = enabled }
    }

    /**
     * Sincronización de solo lectura con ui_preferences de GET /me (o del login): se aplica únicamente a lo que el
     * usuario aún no eligió en el teléfono. La escritura al servidor no está publicada por el Gateway (README).
     */
    suspend fun applyServerDefaults(server: UiPreferencesDto?) {
        if (server == null) return
        dataStore.edit { prefs ->
            if (prefs[Keys.LANGUAGE_CHOSEN] != true) {
                AppLanguage.fromServerLocale(server.locale)?.let { prefs[Keys.LANGUAGE] = it.name }
            }
            if (prefs[Keys.THEME_CHOSEN] != true) {
                ThemeMode.fromServer(server.theme)?.let { prefs[Keys.THEME] = it.name }
            }
        }
    }

    private fun Preferences.toUserPreferences() = UserPreferences(
        language = AppLanguage.fromStored(this[Keys.LANGUAGE]),
        theme = ThemeMode.fromStored(this[Keys.THEME]),
        inAppNotifications = this[Keys.IN_APP_NOTIFICATIONS] ?: true,
        languageChosen = this[Keys.LANGUAGE_CHOSEN] ?: false,
        themeChosen = this[Keys.THEME_CHOSEN] ?: false,
        schemaVersion = this[Keys.SCHEMA] ?: SCHEMA_VERSION,
    )

    private object Keys {
        val LANGUAGE = stringPreferencesKey("language")
        val THEME = stringPreferencesKey("theme")
        val IN_APP_NOTIFICATIONS = booleanPreferencesKey("in_app_notifications")
        val LANGUAGE_CHOSEN = booleanPreferencesKey("language_chosen")
        val THEME_CHOSEN = booleanPreferencesKey("theme_chosen")
        val SCHEMA = intPreferencesKey("schema_version")
    }

    companion object {
        const val SCHEMA_VERSION = 1
    }
}
