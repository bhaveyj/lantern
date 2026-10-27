package app.lantern.tv

import fi.iki.elonen.NanoWSD.WebSocketFrame
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket

/** Runs on Android's real KeyStore/Conscrypt, which desktop JVM tests cannot model. */
class TlsIdentityDeviceTest {
    @Test fun keystoreIdentityPersistsAndSupportsPinnedTlsPairing() {
        val identity = TlsIdentity()
        assertEquals(identity.fingerprint, TlsIdentity().fingerprint)
        val gate = PairingGate()
        val server = ControlServer(gate, { _, _ -> }, {}, 0) { emptyList() }
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
                val pair = JSONObject().put("type", "pair").put("version", 2).put("pin", gate.pin).put("mediaPort", 12345).put("mediaFingerprint", identity.fingerprint)
                WebSocketFrame(WebSocketFrame.OpCode.Text, true, pair.toString(), byteArrayOf(1, 2, 3, 4)).write(output)
                output.flush()
                val response = JSONObject(WebSocketFrame.read(input).textPayload)
                assertEquals("pair.ok", response.getString("type"))
                assertEquals(2, response.getInt("version"))
            }
        } finally { server.shutdown() }
    }
}
