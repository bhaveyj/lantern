package app.lantern.tv

import fi.iki.elonen.NanoWSD.WebSocketFrame
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.util.UUID

/** Runs on Android's real KeyStore/Conscrypt, which desktop JVM tests cannot model. */
class TlsIdentityDeviceTest {
    @Test fun rememberedKeysAreEncryptedPersistedAndRevocable() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(context.cacheDir, "pairing-test-${UUID.randomUUID()}.bin")
        val client = PairedClient("b".repeat(32), "Encrypted device test", ByteArray(32) { it.toByte() })
        try {
            val saved = EncryptedPairedClients(file)
            saved.save(client)
            assertFalse(String(file.readBytes(), Charsets.ISO_8859_1).contains(PairingProtocol.hex(client.key)))
            val restored = EncryptedPairedClients(file)
            assertArrayEquals(client.key, restored.get(client.id)!!.key)
            restored.remove(client.id)
            assertTrue(restored.all().isEmpty())
        } finally { file.delete() }
    }
    @Test fun keystoreIdentityPersistsAndSupportsPinnedTlsPairing() {
        val identity = TlsIdentity()
        assertEquals(identity.fingerprint, TlsIdentity().fingerprint)
        val gate = PairingGate()
        val server = ControlServer(gate, { _, _ -> }, {}, 0, receiverFingerprint = identity.fingerprint) { emptyList() }
        server.makeSecure(identity.context.serverSocketFactory, arrayOf("TLSv1.2"))
        server.start(5000, false)
        try {
            val trusted = MediaPeer("127.0.0.1", 12345, identity.fingerprint)
            val client = SSLContext.getInstance("TLSv1.2").apply { init(null, arrayOf(trusted.trustManager()), null) }
            (client.socketFactory.createSocket("127.0.0.1", server.listeningPort) as SSLSocket).use { socket ->
                socket.soTimeout = 5000
                socket.startHandshake()
                val output = socket.outputStream
                val input = socket.inputStream
                output.write(("GET /control HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").toByteArray())
                output.flush()
                val header = StringBuilder()
                while (!header.endsWith("\r\n\r\n")) { val byte = input.read(); check(byte >= 0); header.append(byte.toChar()) }
                assertTrue(header.startsWith("HTTP/1.1 101"))
                val result = PairingTestClient.pair(gate.pin, identity.fingerprint, { message ->
                    WebSocketFrame(WebSocketFrame.OpCode.Text, true, message, byteArrayOf(1, 2, 3, 4)).write(output)
                }, { JSONObject(WebSocketFrame.read(input).textPayload) })
                val response = result.ack
                assertEquals("pair.ok", response.getString("type"))
                assertEquals(3, response.getInt("version"))
            }
        } finally { server.shutdown() }
    }
}
