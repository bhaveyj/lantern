package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test

class PairingGateTest {
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
