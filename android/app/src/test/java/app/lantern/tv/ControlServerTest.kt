package app.lantern.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.Socket
import fi.iki.elonen.NanoWSD.WebSocketFrame
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ControlServerTest {
    private class Peer(port: Int) : java.io.Closeable {
        private val socket = Socket("127.0.0.1", port).apply { soTimeout = 3000 }
        private val input = socket.getInputStream()
        private val output = socket.getOutputStream()
        init {
            output.write(("GET /control HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").toByteArray())
            output.flush()
            val header = StringBuilder()
            while (!header.endsWith("\r\n\r\n")) {
                val value = input.read()
                check(value >= 0) { "WebSocket handshake ended early" }
                header.append(value.toChar())
            }
            check(header.startsWith("HTTP/1.1 101"))
        }
        fun send(message: String) {
            WebSocketFrame(WebSocketFrame.OpCode.Text, true, message, byteArrayOf(1, 2, 3, 4)).write(output)
            output.flush()
        }
        fun receive() = JSONObject(WebSocketFrame.read(input).textPayload)
        override fun close() { socket.close() }
    }

    @Test fun invalidCommandReturnsErrorAndConnectionStillAcceptsValidCommands() {
        val gate = PairingGate()
        val applied = CountDownLatch(1)
        val server = ControlServer(gate, { _, isCurrent -> if (isCurrent()) applied.countDown() }, {}, 0) { listOf("audio/mp4a-latm") }
        server.start(3000, false)
        try {
            Peer(server.listeningPort).use { peer ->
                peer.send(JSONObject().put("type", "pair").put("version", 2).put("pin", gate.pin).put("name", "Test PC").put("mediaPort", 12345).put("mediaFingerprint", "a".repeat(64)).toString())
                assertEquals("pair.ok", peer.receive().optString("type"))
                peer.send("""{"type":"playback.command","action":"seek","value":"invalid"}""")
                assertEquals("INVALID_COMMAND", peer.receive().optString("code"))
                assertEquals(1L, applied.count)
                peer.send("""{"type":"playback.command","action":"pause"}""")
                assertTrue(applied.await(3, TimeUnit.SECONDS))
            }
        } finally { server.shutdown() }
    }
}
