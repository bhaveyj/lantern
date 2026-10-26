package app.lantern.tv

import fi.iki.elonen.NanoWSD.WebSocketFrame
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.net.Socket
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket

class TlsControlServerTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun pairsOverTlsAndRejectsWrongCertificateAndPlaintext() {
        // Generate an ephemeral test identity; never commit a private key.
        val file = File(temporary.root, "server.p12")
        val tool = File(System.getProperty("java.home"), "bin/keytool.exe")
            .let { if (it.exists()) it else File(System.getProperty("java.home"), "bin/keytool") }
        val process = ProcessBuilder(tool.path, "-genkeypair", "-alias", "test", "-keyalg", "EC", "-groupname", "secp256r1", "-dname", "CN=TLS test", "-validity", "2", "-storetype", "PKCS12", "-keystore", file.path, "-storepass", "test-password").redirectErrorStream(true).start()
        val diagnostic = process.inputStream.bufferedReader().readText()
        assertEquals(diagnostic, 0, process.waitFor())
        val store = KeyStore.getInstance("PKCS12").apply { file.inputStream().use { load(it, "test-password".toCharArray()) } }
        val certificate = store.getCertificate("test") as X509Certificate
        val pin = MessageDigest.getInstance("SHA-256").digest(certificate.encoded).joinToString("") { "%02x".format(it) }
        val trusted = MediaPeer("127.0.0.1", 12345, pin)
        trusted.trustManager().checkServerTrusted(arrayOf(certificate), "EC")
        try {
            MediaPeer("127.0.0.1", 12345, "a".repeat(64)).trustManager().checkServerTrusted(arrayOf(certificate), "EC")
            fail("Wrong pin accepted")
        } catch (_: CertificateException) { }
        val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(store, "test-password".toCharArray()) }
        val tls = SSLContext.getInstance("TLSv1.2").apply { init(managers.keyManagers, null, null) }
        val client = SSLContext.getInstance("TLSv1.2").apply { init(null, arrayOf(trusted.trustManager()), null) }
        val gate = PairingGate()
        val server = ControlServer(gate, { _, _ -> }, {}, 0) { emptyList() }
        server.makeSecure(tls.serverSocketFactory, arrayOf("TLSv1.2"))
        server.start(3000, false)
        try {
            (client.socketFactory.createSocket("127.0.0.1", server.listeningPort) as SSLSocket).use { socket ->
                socket.soTimeout = 3000
                socket.startHandshake()
                val output = socket.outputStream
                val input = socket.inputStream
                output.write(("GET /control HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").toByteArray())
                output.flush()
                val header = StringBuilder()
                while (!header.endsWith("\r\n\r\n")) { val byte = input.read(); check(byte >= 0); header.append(byte.toChar()) }
                assertTrue(header.startsWith("HTTP/1.1 101"))
                val pair = JSONObject().put("type", "pair").put("version", 2).put("pin", gate.pin).put("mediaPort", 12345).put("mediaFingerprint", pin)
                WebSocketFrame(WebSocketFrame.OpCode.Text, true, pair.toString(), byteArrayOf(1, 2, 3, 4)).write(output)
                output.flush()
                assertEquals("pair.ok", JSONObject(WebSocketFrame.read(input).textPayload).getString("type"))
                assertEquals(trusted, server.mediaPeer)
            }
            Socket("127.0.0.1", server.listeningPort).use { socket ->
                socket.soTimeout = 3000
                socket.outputStream.write("GET / HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
                socket.outputStream.flush()
                val prefix = ByteArray(5)
                val count = socket.inputStream.read(prefix)
                assertFalse(count > 0 && String(prefix, 0, count).startsWith("HTTP"))
            }
        } finally { server.shutdown() }
        assertNull(server.mediaPeer)
    }
}
