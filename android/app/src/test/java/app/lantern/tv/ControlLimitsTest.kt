package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class ControlLimitsTest {
    @Test fun validPlaybackCommandsAndUrlsAreAccepted() {
        ControlLimits.url("http://192.168.1.2:8000/stream/token/movie")
        ControlLimits.command("seek", 42.5)
        ControlLimits.command("volume", .5)
        ControlLimits.command("subtitle", null)
        ControlLimits.command("fit", "fill")
    }

    @Test fun malformedValuesAreRejectedBeforePlayerDispatch() {
        val invalid = listOf<() -> Unit>(
            { ControlLimits.url("file:///private/movie.mp4") },
            { ControlLimits.url("http:///missing-host") },
            { ControlLimits.command("seek", Double.NaN) },
            { ControlLimits.command("seek", -1) },
            { ControlLimits.command("volume", 2) },
            { ControlLimits.command("volume", "loud") },
            { ControlLimits.command("unknown", null) },
            { ControlLimits.count(201, 200) }
        )
        invalid.forEach { action ->
            try { action(); fail("Invalid control value was accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
}
