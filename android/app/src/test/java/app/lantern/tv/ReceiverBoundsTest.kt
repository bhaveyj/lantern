package app.lantern.tv

import fi.iki.elonen.NanoWSD
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.net.Socket

class ReceiverBoundsTest {
    private fun connect(port: Int): Socket {
        val socket = Socket("127.0.0.1", port).apply { soTimeout = 2500 }
        socket.getOutputStream().write(("GET /control HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").toByteArray())
        val headers = StringBuilder()
        while (!headers.endsWith("\r\n\r\n")) { val next = socket.getInputStream().read(); check(next >= 0); headers.append(next.toChar()) }
        return socket
    }

    @Test fun fragmentedMessageCannotExceedAggregateLimit() {
        val server = ControlServer(PairingGate(), { _, _ -> fail("Unpaired message dispatched") }, {}, 0) { emptyList() }
        server.start(3000, true)
        try {
            connect(server.listeningPort).use { socket ->
                val output = socket.getOutputStream()
                NanoWSD.WebSocketFrame(NanoWSD.WebSocketFrame.OpCode.Text, false, "x".repeat(600000), byteArrayOf(1, 2, 3, 4)).write(output)
                NanoWSD.WebSocketFrame(NanoWSD.WebSocketFrame.OpCode.Continuation, true, "x".repeat(600000), byteArrayOf(1, 2, 3, 4)).write(output)
                output.flush()
                assertEquals(-1, socket.getInputStream().read())
            }
        } finally { server.shutdown() }
    }

    @Test fun emptyFragmentsAreBoundedWithoutWaitingForFinalFragment() {
        val server = ControlServer(PairingGate(), { _, _ -> fail() }, {}, 0) { emptyList() }
        server.start(3000, true)
        try {
            connect(server.listeningPort).use { socket ->
                val output = socket.getOutputStream()
                NanoWSD.WebSocketFrame(NanoWSD.WebSocketFrame.OpCode.Text, false, "", byteArrayOf(1, 2, 3, 4)).write(output)
                repeat(256) { NanoWSD.WebSocketFrame(NanoWSD.WebSocketFrame.OpCode.Continuation, false, "", byteArrayOf(1, 2, 3, 4)).write(output) }
                output.flush()
                assertEquals(-1, socket.getInputStream().read())
            }
        } finally { server.shutdown() }
    }

    @Test fun oversizedFrameIsRejectedFromItsHeaderWithoutReadingPayload() {
        val header = ByteBuffer.allocate(10).put(0x81.toByte()).put(127.toByte()).putLong(16L * 1024 * 1024).array()
        try {
            NanoWSD.WebSocketFrame.read(ByteArrayInputStream(header))
            fail("Oversized header accepted")
        } catch (error: NanoWSD.WebSocketException) {
            assertEquals(NanoWSD.WebSocketFrame.CloseCode.MessageTooBig, error.code)
        }
    }

    @Test fun jsonDepthIsBoundedBeforeRecursiveParsing() {
        ControlLimits.json("""{"text":"[[[\\\"literal","ok":true}""")
        try { ControlLimits.json("[".repeat(2000) + "0" + "]".repeat(2000)); fail() }
        catch (_: IllegalArgumentException) { }
        try { ControlLimits.json("x".repeat(8193), 8192); fail() }
        catch (_: IllegalArgumentException) { }
    }

    @Test fun idleUnpairedSocketClosesWithoutApplicationMessages() {
        val server = ControlServer(PairingGate(), { _, _ -> }, {}, 0, pairingTimeoutMs = 100) { emptyList() }
        server.start(3000, true)
        try {
            Socket("127.0.0.1", server.listeningPort).use { socket ->
                socket.soTimeout = 2000
                socket.getOutputStream().write(("GET /control HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").toByteArray())
                val input = socket.getInputStream()
                val headers = StringBuilder()
                while (!headers.endsWith("\r\n\r\n")) { val next = input.read(); assertTrue(next >= 0); headers.append(next.toChar()) }
                assertEquals(-1, input.read())
            }
        } finally { server.shutdown() }
    }

    @Test fun rawConnectionsAreBoundedBeforeUpgrade() {
        val server = ControlServer(PairingGate(), { _, _ -> }, {}, 0, maxClients = 1) { emptyList() }
        server.start(3000, true)
        try {
            Socket("127.0.0.1", server.listeningPort).use {
                Thread.sleep(60)
                Socket("127.0.0.1", server.listeningPort).use { rejected -> rejected.soTimeout = 2000; assertEquals(-1, rejected.getInputStream().read()) }
            }
        } finally { server.shutdown() }
    }
}
