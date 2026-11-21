package com.example.cursobasico.preferences

import androidx.appcompat.app.AppCompatDelegate
import androidx.core.os.LocaleListCompat

/**
 * push app language into Android's per-app locale APIs.
 * API 33+: system LocaleManager (settings + locales_config.xml).
 * older: AppCompat recreates Activity (autoStoreLocales in manifest).
 * DataStore stays source of truth; this just syncs the framework.
 */
object LocaleApplier {
    fun apply(language: AppLanguage) {
        val desired = language.tag?.let { LocaleListCompat.forLanguageTags(it) } ?: LocaleListCompat.getEmptyLocaleList()
        if (AppCompatDelegate.getApplicationLocales().toLanguageTags() != desired.toLanguageTags()) {
            AppCompatDelegate.setApplicationLocales(desired)
        }
    }
}
