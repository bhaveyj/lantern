package app.lantern.tv

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/** Pairing credentials are encrypted with a non-exportable AndroidKeyStore key. */
class EncryptedPairedClients(private val file: File) : PairedClientStore {
    private val alias = "lantern-paired-clients-v3"
    private val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    init {
        if (!store.containsAlias(alias)) {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            generator.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
            generator.generateKey()
        }
    }
    @Synchronized override fun all(): List<PairedClient> {
        if (!file.exists()) return emptyList()
        require(file.length() in 28..65536) { "Saved pairing data is invalid" }
        val encrypted = file.readBytes()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, store.getKey(alias, null), GCMParameterSpec(128, encrypted.copyOfRange(0, 12)))
        val array = JSONArray(String(cipher.doFinal(encrypted.copyOfRange(12, encrypted.size)), Charsets.UTF_8))
        require(array.length() <= 32)
        return (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            PairingProtocol.unhex(item.getString("id"), 16)
            ControlLimits.text(item.getString("name"), 128)
            PairedClient(item.getString("id"), item.getString("name"), PairingProtocol.unhex(item.getString("key"), 32))
        }
    }
    @Synchronized override fun save(client: PairedClient) {
        val records = all().filter { it.id != client.id } + client
        require(records.size <= 32)
        write(records)
    }
    @Synchronized override fun remove(id: String) { write(all().filter { it.id != id }) }
    @Synchronized fun clear() { write(emptyList()) }
    private fun write(records: List<PairedClient>) {
        val array = JSONArray()
        records.forEach { array.put(JSONObject().put("id", it.id).put("name", it.name).put("key", PairingProtocol.hex(it.key))) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, store.getKey(alias, null))
        val output = cipher.iv + cipher.doFinal(array.toString().toByteArray(Charsets.UTF_8))
        val pending = File(file.parentFile, file.name + ".tmp")
        pending.writeBytes(output)
        check(pending.renameTo(file)) { "Could not save the pairing" }
    }
}
