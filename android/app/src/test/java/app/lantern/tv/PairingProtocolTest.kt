package app.lantern.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PairingProtocolTest {
    private fun paired(gate: PairingGate, records: PairedClientStore): PairingTestClient.Result {
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            var response = JSONObject()
            return PairingTestClient.pair(gate.pin, "a".repeat(64), { response = server.receive(JSONObject(it)) }, { response })
        }
    }
    @Test fun codePairingCreatesRememberedCredentialAndResumeIsFresh() {
        val gate = PairingGate()
        val records = MemoryPairedClients()
        val result = paired(gate, records)
        assertArrayEquals(result.key, records.get(result.hello.getString("clientId"))!!.key)
        val hello = JSONObject(result.hello.toString()).put("mode", "resume")
        var captured = JSONObject()
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            val challenge = server.receive(hello)
            val binding = PairingProtocol.context("a".repeat(64), hello, challenge.getString("nonce"))
            captured = JSONObject().put("type", "pair.resume").put("proof", PairingProtocol.hex(PairingProtocol.proof(result.key, "client resume", binding)))
            assertEquals("pair.ok", server.receive(captured).getString("type"))
        }
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            server.receive(hello)
            try { server.receive(captured); fail("Replayed reconnect accepted") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun expiredCodesAndAlteredBindingsCannotPair() {
        var clock = 0L
        val gate = PairingGate { clock }
        val records = MemoryPairedClients()
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            var response = JSONObject()
            try {
                PairingTestClient.pair(gate.pin, "b".repeat(64), { response = server.receive(JSONObject(it)) }, { response })
                fail("Different certificate accepted")
            } catch (_: IllegalArgumentException) { }
            assertTrue(records.all().isEmpty())
        }
        val hello = JSONObject().put("type", "pair.start").put("version", 3).put("mode", "code").put("name", "PC")
            .put("clientId", "a".repeat(32)).put("clientNonce", "b".repeat(64)).put("mediaFingerprint", "c".repeat(64)).put("mediaPort", 12345)
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            val challenge = server.receive(hello)
            val binding = PairingProtocol.context("a".repeat(64), hello, challenge.getString("nonce"))
            NativePake(0, gate.pin, binding).use { client ->
                clock += 300001
                try { server.receive(JSONObject().put("type", "pair.prove").put("share", PairingProtocol.hex(client.exchange(0)))); fail("Expired code accepted") } catch (_: IllegalArgumentException) { }
            }
        }
    }
    @Test fun revokedClientAndOldProtocolAreRejected() {
        val gate = PairingGate()
        val records = MemoryPairedClients()
        val saved = paired(gate, records)
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            val hello = JSONObject(saved.hello.toString()).put("mode", "resume")
            val challenge = server.receive(hello)
            records.remove(hello.getString("clientId"))
            val binding = PairingProtocol.context("a".repeat(64), hello, challenge.getString("nonce"))
            try { server.receive(JSONObject().put("type", "pair.resume").put("proof", PairingProtocol.hex(PairingProtocol.proof(saved.key, "client resume", binding)))); fail("Revoked client accepted") } catch (_: PairingSession.PairingRequired) { }
        }
        PairingSession(gate, records, "a".repeat(64), "127.0.0.1").use { server ->
            try { server.receive(JSONObject().put("type", "pair").put("version", 2).put("pin", gate.pin)); fail("Legacy PIN accepted") } catch (_: IllegalArgumentException) { }
        }
    }
}
