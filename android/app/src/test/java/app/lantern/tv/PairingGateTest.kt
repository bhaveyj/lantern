package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class PairingGateTest {
    @Test fun displayCountdownRoundsUpUntilTheExactExpiry() {
        var clock = 0L
        val gate = PairingGate { clock }
        val initial = gate.displayState()
        assertEquals(gate.pin, initial.pin)
        assertEquals(300L, initial.remainingSeconds)

        for ((time, seconds) in listOf(1L to 300L, 999L to 300L, 1000L to 299L, 299000L to 1L, 299999L to 1L)) {
            clock = time
            val display = gate.displayState()
            assertEquals(initial.pin, display.pin)
            assertEquals("Countdown at $time ms", seconds, display.remainingSeconds)
        }
    }

    @Test fun displayRefreshesExpiredCodeAndInvalidatesAnExistingAttempt() {
        var clock = 12000L
        val gate = PairingGate { clock }
        val attempt = requireNotNull(gate.begin("peer"))
        clock += PairingGate.PIN_LIFETIME_MS
        val refreshed = gate.displayState()
        assertEquals(gate.pin, refreshed.pin)
        assertEquals(300L, refreshed.remainingSeconds)
        assertFalse(gate.valid(attempt))
        assertFalse(gate.refresh())
        clock += 1000
        assertEquals(299L, gate.displayState().remainingSeconds)
    }

    @Test fun readingCountdownDoesNotExtendTheCodeLifetime() {
        var clock = 0L
        val gate = PairingGate { clock }
        val attempt = requireNotNull(gate.begin("peer"))
        clock = 100000L
        assertEquals(200L, gate.displayState().remainingSeconds)
        assertEquals(200L, gate.displayState().remainingSeconds)
        clock = 250000L
        assertEquals(50L, gate.displayState().remainingSeconds)
        assertTrue(gate.valid(attempt))
        clock = PairingGate.PIN_LIFETIME_MS
        assertEquals(300L, gate.displayState().remainingSeconds)
        assertFalse(gate.valid(attempt))
    }

    @Test fun displaySnapshotReadsTheClockOnceWhenRefreshing() {
        var clock = 0L
        var reads = 0
        val gate = PairingGate { reads++; clock }
        clock = PairingGate.PIN_LIFETIME_MS
        reads = 0
        assertEquals(300L, gate.displayState().remainingSeconds)
        assertEquals(1, reads)
    }

    @Test fun correctPinAndExpiry() {
        var clock = 0L
        val gate = PairingGate { clock }
        assertTrue(gate.accept("peer", gate.pin))
        clock += 300001
        assertTrue(gate.refresh())
        assertTrue(gate.accept("peer", gate.pin))
    }
    @Test fun failedAttemptsAreRateLimited() {
        var clock = 0L
        val gate = PairingGate { clock }
        repeat(5) { assertFalse(gate.accept("peer", "000000")) }
        assertFalse(gate.accept("peer", gate.pin))
        assertTrue(gate.accept("other-peer", gate.pin))
        clock += 60001
        assertTrue(gate.accept("peer", gate.pin))
    }
    @Test fun changingPeerAddressesCannotBypassReceiverWideLimit() {
        var clock = 0L
        val gate = PairingGate { clock }
        repeat(25) { assertFalse(gate.accept("peer-$it", "000000")) }
        assertFalse(gate.accept("new-peer", gate.pin))
        clock += 60001
        assertTrue(gate.accept("new-peer", gate.pin))
    }
}
