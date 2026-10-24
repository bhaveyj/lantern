package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class SubtitleSelectionTest {
    @Test fun externalTrackIdsMatchMedia3MergedSourcePrefixes() {
        assertTrue(matchesSubtitleTrack("1:generated-en", "generated-en"))
        assertTrue(matchesSubtitleTrack("2:generated-hi", "generated-hi"))
        assertTrue(matchesSubtitleTrack("1:custom:track", "custom:track"))
        assertTrue(matchesSubtitleTrack("generated-en", "generated-en"))
    }

    @Test fun otherTracksCannotMatchSelectedExternalCaption() {
        assertFalse(matchesSubtitleTrack(null, "generated-en"))
        assertFalse(matchesSubtitleTrack("0:generated-en", "generated-en"))
        assertFalse(matchesSubtitleTrack("1:other-generated-en", "generated-en"))
        assertFalse(matchesSubtitleTrack("invalid:generated-en", "generated-en"))
        assertFalse(matchesSubtitleTrack("-1:generated-en", "generated-en"))
    }

    @Test fun delayedSnapshotsCannotUndoRemoteSelection() {
        val state = SubtitleSelection()
        state.reset("english", 4)
        assertFalse(state.accept("french", 3))
        assertEquals("english", state.id)
        assertTrue(state.accept("french", 5))
        assertEquals("french", state.id)
    }

    @Test fun localSelectionAdvancesRevisionAndNewMediaResetsIt() {
        val state = SubtitleSelection()
        state.reset("english", 4)
        state.select(null)
        assertEquals(5L, state.revision)
        assertFalse(state.accept("english", 4))
        state.select(null)
        assertEquals(5L, state.revision)
        state.reset("french", 0)
        assertEquals("french", state.id)
        assertEquals(0L, state.revision)
    }
}
