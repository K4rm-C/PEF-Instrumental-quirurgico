package com.example.cursobasico.preferences

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.example.cursobasico.data.model.UiPreferencesDto
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import okio.Path.Companion.toOkioPath
import okio.FileSystem
import androidx.datastore.core.okio.OkioStorage
import androidx.datastore.preferences.core.PreferencesSerializer
import java.io.File

class PreferencesRepositoryTest {
    @get:Rule
    val tmp = TemporaryFolder()
    private lateinit var file: File
    private val scopes = mutableListOf<CoroutineScope>()

    @Before
    fun setUp() {
        file = File(tmp.root, "prefs.preferences_pb")
    }

    @After
    fun tearDown() {
        scopes.forEach { it.cancel() }
    }

    private fun repository(): PreferencesRepository {
        val scope = CoroutineScope(Dispatchers.IO + SupervisorJob()).also { scopes += it }
        // OkioStorage: movimiento atómico que también funciona en la JVM de Windows (FileStorage usa renameTo,
        // que en Windows falla si el destino existe; en Android/Linux no ocurre).
        val storage = OkioStorage(FileSystem.SYSTEM, PreferencesSerializer, producePath = { file.toOkioPath() })
        return PreferencesRepository(PreferenceDataStoreFactory.create(storage = storage, scope = scope))
    }

    /** Cierra el DataStore actual para poder abrir otro sobre el mismo archivo (simula reiniciar la app). */
    private fun restart(): PreferencesRepository {
        scopes.forEach { it.cancel() }
        scopes.clear()
        Thread.sleep(50)
        return repository()
    }

    @Test
    fun `valores predeterminados`() = runBlocking {
        val prefs = repository().current()
        assertEquals(AppLanguage.SYSTEM, prefs.language)
        assertEquals(ThemeMode.SYSTEM, prefs.theme)
        assertTrue(prefs.inAppNotifications)
        assertFalse(prefs.languageChosen)
        assertEquals(PreferencesRepository.SCHEMA_VERSION, prefs.schemaVersion)
    }

    @Test
    fun `idioma y tema persisten tras reiniciar`() = runBlocking {
        val first = repository()
        first.setLanguage(AppLanguage.SPANISH)
        first.setTheme(ThemeMode.DARK)
        first.setInAppNotifications(false)

        val prefs = restart().current()
        assertEquals(AppLanguage.SPANISH, prefs.language)
        assertEquals(ThemeMode.DARK, prefs.theme)
        assertFalse(prefs.inAppNotifications)
        assertTrue(prefs.languageChosen && prefs.themeChosen)
    }

    @Test
    fun `cambio en tiempo de ejecucion emite el nuevo valor`() = runBlocking {
        val repo = repository()
        repo.current()
        val next = async { withTimeout(5_000) { repo.preferences.drop(1).first() } }
        yield()
        Thread.sleep(50)
        repo.setTheme(ThemeMode.LIGHT)
        assertEquals(ThemeMode.LIGHT, next.await().theme)
    }

    @Test
    fun `preferencias del servidor solo se aplican si el usuario no eligio`() = runBlocking {
        val repo = repository()
        repo.applyServerDefaults(UiPreferencesDto(locale = "es-MX", theme = "dark"))
        assertEquals(AppLanguage.SPANISH, repo.current().language)
        assertEquals(ThemeMode.DARK, repo.current().theme)
        assertFalse(repo.current().languageChosen)

        repo.setLanguage(AppLanguage.ENGLISH)
        repo.applyServerDefaults(UiPreferencesDto(locale = "es-MX", theme = "light"))
        assertEquals(AppLanguage.ENGLISH, repo.current().language) // elección local gana
        assertEquals(ThemeMode.LIGHT, repo.current().theme)        // tema aún no elegido localmente
    }

    @Test
    fun `mapeo de valores del servidor`() {
        assertEquals(AppLanguage.SPANISH, AppLanguage.fromServerLocale("es-MX"))
        assertEquals(AppLanguage.ENGLISH, AppLanguage.fromServerLocale("en"))
        assertEquals(null, AppLanguage.fromServerLocale("fr"))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromServer("system"))
        assertEquals(null, ThemeMode.fromServer("sepia"))
        assertEquals(AppLanguage.SYSTEM, AppLanguage.fromStored("garbage"))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStored(null))
        assertEquals("es", AppLanguage.SPANISH.tag)
        assertEquals(null, AppLanguage.SYSTEM.tag)
    }
}
