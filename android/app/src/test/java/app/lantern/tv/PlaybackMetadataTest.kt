package app.lantern.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PlaybackMetadataTest {
    @Test fun sourceHintsAreOptionalAndHlsAliasesAreCanonical() {
        assertEquals(PlaybackMetadata(null, false, true), PlaybackMetadata.from(JSONObject()))
        assertNull(PlaybackMetadata.from(JSONObject().put("mimeType", "")).mimeType)
        for (mime in listOf("application/vnd.apple.mpegurl", "application/x-mpegURL", "audio/mpegurl")) {
            val metadata = PlaybackMetadata.from(JSONObject().put("mimeType", mime).put("live", true).put("seekable", false))
            assertEquals(PlaybackMetadata.HLS_MIME, metadata.mimeType)
            assertTrue(metadata.live)
            assertFalse(metadata.seekable)
        }
    }

    @Test fun malformedSourceHintsAreRejectedWithoutJsonCoercion() {
        for (item in listOf(
            JSONObject().put("mimeType", "text/html"),
            JSONObject().put("mimeType", "application/dash+xml"),
            JSONObject().put("mimeType", 42),
            JSONObject().put("live", "true"),
            JSONObject().put("seekable", 1),
            JSONObject().put("mimeType", JSONObject.NULL)
        )) {
            try { PlaybackMetadata.from(item); fail("Invalid source hints accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun playlistRefreshPreservesThePlayingLiveSourceWhenHintsAreOmitted() {
        val previous = JSONObject().put("url", "https://paired/stream/old").put("mimeType", PlaybackMetadata.HLS_MIME)
            .put("live", true).put("seekable", false).put("title", "Original")
        val updated = JSONObject().put("url", "https://paired/stream/new").put("title", "Updated")
        val merged = PlaybackMetadata.mergePlaying(previous, updated)
        assertEquals("https://paired/stream/old", merged.getString("url"))
        assertEquals("Updated", merged.getString("title"))
        assertEquals(PlaybackMetadata.HLS_MIME, merged.getString("mimeType"))
        assertTrue(merged.getBoolean("live"))
        assertFalse(merged.getBoolean("seekable"))
        assertFalse(updated.has("mimeType"))
    }

    @Test fun unknownDurationAndNonseekableSourcesCannotBeSought() {
        // Media3 TIME_UNSET and LENGTH_UNSET are negative sentinel values.
        for (duration in listOf(-9223372036854775807L, -1L, 0L)) {
            assertEquals(0, PlaybackTimeline.progress(5000, duration))
            assertNull(PlaybackTimeline.position(5000, duration, true))
            assertNull(PlaybackTimeline.fromProgress(500, duration, true))
        }
        assertNull(PlaybackTimeline.position(5000, 10000, false))
        assertNull(PlaybackTimeline.fromProgress(500, 10000, false))
    }

    @Test fun dvrSeekingClampsToCurrentWindowAndAvoidsLongOverflow() {
        assertEquals(0L, PlaybackTimeline.position(-10000, 60000, true))
        assertEquals(60000L, PlaybackTimeline.position(70000, 60000, true))
        assertEquals(30000L, PlaybackTimeline.fromProgress(500, 60000, true))
        assertEquals(60000L, PlaybackTimeline.fromProgress(1500, 60000, true))
        assertEquals(500, PlaybackTimeline.progress(Long.MAX_VALUE / 2, Long.MAX_VALUE))
        assertEquals(Long.MAX_VALUE, PlaybackTimeline.fromProgress(1000, Long.MAX_VALUE, true))
    }
}
