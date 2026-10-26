package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class MediaPeerTest {
    @Test fun onlyThePairedHttpsOriginIsAccepted() {
        val peer = MediaPeer("192.168.1.2", 4321, "a".repeat(64))
        peer.checkUrl("https://192.168.1.2:4321/stream/token/movie")
        for (url in listOf("http://192.168.1.2:4321/movie", "https://127.0.0.1:4321/movie", "https://192.168.1.2:9999/movie", "https://user@192.168.1.2:4321/movie", "https://192.168.1.2:4321/movie#fragment")) {
            try { peer.checkUrl(url); fail("Untrusted origin accepted") } catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun malformedMediaPinsAreRejected() {
        try { MediaPeer("192.168.1.2", 4321, "short"); fail() } catch (_: IllegalArgumentException) { }
    }
}
