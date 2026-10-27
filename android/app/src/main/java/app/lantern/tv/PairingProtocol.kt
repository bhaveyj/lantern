package app.lantern.tv

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object PairingProtocol {
    const val VERSION = 3
    fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }
    fun unhex(value: String, size: Int): ByteArray {
        require(Regex("[0-9a-f]{${size * 2}}").matches(value)) { "Invalid pairing message" }
        return value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    }
    fun nonce() = hex(ByteArray(32).apply { SecureRandom().nextBytes(this) })
    fun context(receiver: String, hello: JSONObject, nonce: String): ByteArray {
        val name = hello.getString("name").toByteArray(Charsets.UTF_8)
        val port = hello.get("mediaPort")
        require(name.size in 1..128 && port is Int && port in 1..65535)
        val fields = listOf("Lantern Pairing v3 SPAKE2+-P256-SHA256".toByteArray(Charsets.US_ASCII),
            unhex(receiver, 32), unhex(hello.getString("mediaFingerprint"), 32),
            byteArrayOf((port shr 8).toByte(), port.toByte()), unhex(hello.getString("clientId"), 16),
            unhex(hello.getString("clientNonce"), 32), unhex(nonce, 32), name)
        val buffer = ByteArrayOutputStream()
        DataOutputStream(buffer).use { output -> fields.forEach { output.writeShort(it.size); output.write(it) } }
        return buffer.toByteArray()
    }
    fun proof(key: ByteArray, label: String, context: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        mac.update(("Lantern v3 " + label).toByteArray(Charsets.US_ASCII))
        return mac.doFinal(MessageDigest.getInstance("SHA-256").digest(context))
    }
}

data class PairedClient(val id: String, val name: String, val key: ByteArray)

interface PairedClientStore {
    fun all(): List<PairedClient>
    fun save(client: PairedClient)
    fun remove(id: String)
    fun get(id: String) = all().firstOrNull { it.id == id }
}

class MemoryPairedClients : PairedClientStore {
    private val records = linkedMapOf<String, PairedClient>()
    @Synchronized override fun all() = records.values.toList()
    @Synchronized override fun save(client: PairedClient) {
        require(records.containsKey(client.id) || records.size < 32) { "Forget an unused computer before pairing another" }
        records[client.id] = client
    }
    @Synchronized override fun remove(id: String) { records.remove(id) }
}

/** One state machine per socket; protocol v2 plaintext-PIN pairing is rejected. */
class PairingSession(private val gate: PairingGate, private val clients: PairedClientStore, private val receiver: String, private val peer: String) : AutoCloseable {
    private var hello: JSONObject? = null
    private var binding = byteArrayOf()
    private var pake: NativePake? = null
    private var key = byteArrayOf()
    private var attempt: PairingGate.Attempt? = null
    private var stage = 0
    var client: PairedClient? = null
        private set
    val media: MediaPeer get() = MediaPeer.fromPair(peer, requireNotNull(hello))

    @Synchronized fun receive(message: JSONObject): JSONObject {
        when (stage) {
            0 -> {
                require(message.getString("type") == "pair.start" && message.getInt("version") == 3) { "Update Lantern on both your PC and TV" }
                val mode = message.getString("mode")
                require(mode in listOf("code", "resume"))
                val nonce = PairingProtocol.nonce()
                binding = PairingProtocol.context(receiver, message, nonce)
                hello = message
                if (mode == "resume") {
                    val saved = clients.get(message.getString("clientId")) ?: throw PairingRequired()
                    key = saved.key.copyOf()
                    stage = 3
                } else {
                    require(clients.all().size < 32) { "Forget an unused computer on the TV before pairing another" }
                    attempt = gate.begin(peer) ?: throw RateLimited()
                    stage = 1
                }
                return JSONObject().put("type", "pair.challenge").put("version", 3).put("nonce", nonce)
            }
            1 -> {
                require(message.getString("type") == "pair.prove")
                val pending = requireNotNull(attempt)
                if (!gate.valid(pending)) throw CodeExpired()
                val instance = NativePake(1, pending.pin, binding)
                pake = instance
                val output = instance.exchange(1, PairingProtocol.unhex(message.getString("share"), 65))
                key = PairingProtocol.proof(output.copyOfRange(97, 129), "remembered key", binding)
                output.fill(0, 97, 129)
                stage = 2
                return JSONObject().put("type", "pair.proof").put("version", 3).put("share", PairingProtocol.hex(output.copyOfRange(0, 65))).put("proof", PairingProtocol.hex(output.copyOfRange(65, 97)))
            }
            2 -> {
                require(message.getString("type") == "pair.confirm")
                if (!gate.valid(requireNotNull(attempt))) throw CodeExpired()
                requireNotNull(pake).exchange(3, PairingProtocol.unhex(message.getString("proof"), 32))
                val request = requireNotNull(hello)
                client = PairedClient(request.getString("clientId"), request.getString("name"), key.copyOf())
                clients.save(requireNotNull(client))
                gate.succeeded(peer)
                return accepted()
            }
            3 -> {
                require(message.getString("type") == "pair.resume")
                val current = clients.get(requireNotNull(hello).getString("clientId")) ?: throw PairingRequired()
                require(MessageDigest.isEqual(current.key, key)) { "Saved pairing changed. Pair this TV again" }
                require(MessageDigest.isEqual(PairingProtocol.proof(key, "client resume", binding), PairingProtocol.unhex(message.getString("proof"), 32))) { "Pair this TV again using the code on its screen" }
                client = PairedClient(requireNotNull(hello).getString("clientId"), requireNotNull(hello).getString("name"), key.copyOf())
                clients.save(requireNotNull(client))
                return accepted()
            }
            else -> throw IllegalArgumentException("Pairing exchange already completed")
        }
    }

    private fun accepted(): JSONObject {
        val response = JSONObject().put("type", "pair.ok").put("version", 3).put("proof", PairingProtocol.hex(PairingProtocol.proof(key, "server accepted", binding)))
        stage = 4
        close()
        return response
    }
    @Synchronized override fun close() { pake?.close(); pake = null; key.fill(0) }
    class PairingRequired : IllegalArgumentException("Pair this TV again using the code on its screen")
    class RateLimited : IllegalArgumentException("Too many pairing attempts")
    class CodeExpired : IllegalArgumentException("The TV code expired")
}
