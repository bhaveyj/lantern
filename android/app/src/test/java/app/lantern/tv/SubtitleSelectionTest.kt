package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class SubtitleSelectionTest {
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
