package app.lantern.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ControlMessagesTest {
    private val peer = MediaPeer("192.168.1.2", 12345, "a".repeat(64))
    @Test fun authenticatedSenderMessagesAreAccepted() {
        ControlMessages.validate(JSONObject("""{"type":"playback.load","item":{"id":"one","title":"Movie","url":"https://192.168.1.2:12345/stream/${"a".repeat(43)}/one","subtitles":[]}}"""), peer)
        ControlMessages.validate(JSONObject("""{"type":"playback.command","action":"subtitle","value":null,"revision":2}"""), peer)
    }

    @Test fun malformedMessagesAreRejected() {
        val messages = listOf(
            """{"type":"playback.load"}""",
            """{"type":"playback.command","action":"seek","value":"invalid"}""",
            """{"type":"playback.command","action":"subtitle","value":null,"revision":-1}""",
            """{"type":"playback.load","item":{"id":"one","title":"Movie","url":"file:///private","subtitles":[]}}"""
        )
        messages.forEach { value ->
            var rejected = false
            try { ControlMessages.validate(JSONObject(value), peer) } catch (_: Exception) { rejected = true }
            assertTrue("Malformed message was accepted", rejected)
        }
    }

    @Test fun validLookingItemsCannotRequestAnotherOriginOrRoute() {
        for (url in listOf("https://127.0.0.1:12345/stream/${"a".repeat(43)}/one", "https://192.168.1.2:12345/private", "https://192.168.1.2:12345/stream/${"a".repeat(43)}/other", "https://192.168.1.2:12345/subtitle/${"a".repeat(43)}/one")) {
            val item = JSONObject().put("id", "one").put("title", "Movie").put("url", url)
            try { ControlMessages.validate(JSONObject().put("type", "playback.load").put("item", item), peer); fail("Untrusted route accepted") } catch (_: IllegalArgumentException) { }
        }
    }
}
