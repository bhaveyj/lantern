package app.lantern.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ControlMessagesTest {
    @Test fun currentAndLegacySenderMessagesAreAccepted() {
        ControlMessages.validate(JSONObject("""{"type":"playback.load","item":{"id":"one","title":"Movie","url":"http://192.168.1.2/stream/token/one","subtitles":[]}}"""))
        ControlMessages.validate(JSONObject("""{"type":"playback.command","action":"subtitle","value":null,"revision":2}"""))
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
            try { ControlMessages.validate(JSONObject(value)) } catch (_: Exception) { rejected = true }
            assertTrue("Malformed message was accepted", rejected)
        }
    }
}
