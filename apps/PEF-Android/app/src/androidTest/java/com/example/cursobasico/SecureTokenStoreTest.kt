package com.example.cursobasico

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.example.cursobasico.auth.SecureTokenStore
import com.example.cursobasico.auth.StoredSession
import com.example.cursobasico.data.model.UserDto
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Requiere emulador o dispositivo: Android Keystore no existe en la JVM local. */
@RunWith(AndroidJUnit4::class)
class SecureTokenStoreTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun tokensSeGuardanCifradosYSeBorran() = runBlocking {
        val store = SecureTokenStore(context)
        val session = StoredSession(
            accessToken = "test-access-token-value",
            refreshToken = "test-refresh-token-value",
            accessExpiresAtMillis = 1L,
            user = UserDto("u", "Test", "t@example.test", "i", roles = listOf("station_operator")),
        )
        store.save(session)
        assertEquals(session, store.read())

        val raw = File(context.filesDir, "datastore/pef_secure_session.preferences_pb").readBytes().decodeToString()
        assertFalse(raw.contains("test-access-token-value"))
        assertFalse(raw.contains("test-refresh-token-value"))

        store.clear()
        assertNull(store.read())
    }
}
