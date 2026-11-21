package com.example.cursobasico.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.Json
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * AES-256-GCM with a key that lives in Android Keystore (non-exportable, not backed up).
 * replaces EncryptedSharedPreferences (deprecated) without extra deps.
 */
internal class KeystoreCipher(private val alias: String = "pef_session_key") {
    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    fun encrypt(plain: ByteArray): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val iv = cipher.iv
        val sealed = cipher.doFinal(plain)
        return Base64.encodeToString(byteArrayOf(iv.size.toByte()) + iv + sealed, Base64.NO_WRAP)
    }

    fun decrypt(encoded: String): ByteArray {
        val raw = Base64.decode(encoded, Base64.NO_WRAP)
        val ivLength = raw[0].toInt()
        val iv = raw.copyOfRange(1, 1 + ivLength)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        return cipher.doFinal(raw, 1 + ivLength, raw.size - 1 - ivLength)
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}

/** own file, separate from UI prefs, excluded from backups (res/xml) */
private val Context.secureSessionStore: DataStore<Preferences> by preferencesDataStore(name = "pef_secure_session")

class SecureTokenStore(context: Context) : TokenStore {
    private val dataStore = context.applicationContext.secureSessionStore
    private val cipher = KeystoreCipher()
    private val json = Json { ignoreUnknownKeys = true }
    private val blobKey = stringPreferencesKey("session_blob")

    override suspend fun read(): StoredSession? {
        val encoded = dataStore.data.first()[blobKey] ?: return null
        return try {
            json.decodeFromString(StoredSession.serializer(), cipher.decrypt(encoded).decodeToString())
        } catch (_: Exception) {
            // key lost (reinstall, backup restore) or corrupt blob: drop session
            clear()
            null
        }
    }

    override suspend fun save(session: StoredSession) {
        val encoded = cipher.encrypt(json.encodeToString(StoredSession.serializer(), session).encodeToByteArray())
        dataStore.edit { it[blobKey] = encoded }
    }

    override suspend fun clear() {
        dataStore.edit { it.remove(blobKey) }
    }
}
