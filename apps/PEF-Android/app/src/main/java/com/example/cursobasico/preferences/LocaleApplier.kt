package com.example.cursobasico.preferences

import androidx.appcompat.app.AppCompatDelegate
import androidx.core.os.LocaleListCompat

/**
 * Aplica el idioma con el mecanismo de idioma por aplicación de Android:
 * - API 33+: LocaleManager del sistema (también visible en Ajustes > Idioma de la app; res/xml/locales_config.xml).
 * - API < 33: AppCompat recrea la Activity y guarda la selección (autoStoreLocales en el manifiesto).
 * DataStore sigue siendo la fuente de verdad de la app; esto solo sincroniza el framework con ella.
 */
object LocaleApplier {
    fun apply(language: AppLanguage) {
        val desired = language.tag?.let { LocaleListCompat.forLanguageTags(it) } ?: LocaleListCompat.getEmptyLocaleList()
        if (AppCompatDelegate.getApplicationLocales().toLanguageTags() != desired.toLanguageTags()) {
            AppCompatDelegate.setApplicationLocales(desired)
        }
    }
}
