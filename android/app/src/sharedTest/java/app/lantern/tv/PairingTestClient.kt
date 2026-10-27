package app.lantern.tv

import org.json.JSONObject
import java.security.MessageDigest

object PairingTestClient {
    data class Result(val hello: JSONObject, val key: ByteArray, val ack: JSONObject)
    fun pair(pin: String, identity: String, send: (String) -> Unit, receive: () -> JSONObject, mediaIdentity: String = "a".repeat(64)): Result {
        val hello = JSONObject().put("type", "pair.start").put("version", 3).put("mode", "code")
            .put("name", "Test PC").put("clientId", "c".repeat(32)).put("clientNonce", PairingProtocol.nonce())
            .put("mediaPort", 12345).put("mediaFingerprint", mediaIdentity)
        send(hello.toString())
        val challenge = receive()
        check(challenge.getString("type") == "pair.challenge")
        val context = PairingProtocol.context(identity, hello, challenge.getString("nonce"))
        NativePake(0, pin, context).use { client ->
            send(JSONObject().put("type", "pair.prove").put("share", PairingProtocol.hex(client.exchange(0))).toString())
            val server = receive()
            val confirm = client.exchange(2, PairingProtocol.unhex(server.getString("share"), 65) + PairingProtocol.unhex(server.getString("proof"), 32))
            val key = PairingProtocol.proof(confirm.copyOfRange(32, 64), "remembered key", context)
            send(JSONObject().put("type", "pair.confirm").put("proof", PairingProtocol.hex(confirm.copyOfRange(0, 32))).toString())
            val ack = receive()
            check(ack.getString("type") == "pair.ok")
            check(MessageDigest.isEqual(PairingProtocol.proof(key, "server accepted", context), PairingProtocol.unhex(ack.getString("proof"), 32)))
            return Result(hello, key, ack)
        }
    }
}
