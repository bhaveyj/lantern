package app.lantern.tv

import java.security.SecureRandom

/** The visible PIN expires after five minutes; failed attempts are bounded per peer. */
class PairingGate(private val now: () -> Long = { System.currentTimeMillis() }) {
    private val random = SecureRandom()
    var pin: String = newPin()
        private set
    private var created = now()
    private val failures = mutableMapOf<String, MutableList<Long>>()

    private fun newPin() = (100000 + random.nextInt(900000)).toString()

    @Synchronized fun refresh(): Boolean {
        if (now() - created < 300000) return false
        pin = newPin()
        created = now()
        return true
    }

    @Synchronized fun accept(peer: String, candidate: String): Boolean {
        refresh()
        val attempts = failures.getOrPut(peer) { mutableListOf() }
        attempts.removeAll { now() - it > 60000 }
        if (attempts.size >= 5) return false
        if (candidate != pin) {
            attempts.add(now())
            return false
        }
        failures.remove(peer)
        return true
    }
}
