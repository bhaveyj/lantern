package app.lantern.tv

import java.security.SecureRandom

/** Short-lived PIN with per-peer and receiver-wide failed-attempt limits. */
class PairingGate(private val now: () -> Long = { System.nanoTime() / 1000000 }) {
    companion object {
        const val PIN_LIFETIME_MS = 300000L
    }

    data class Attempt(val pin: String, val created: Long)
    data class DisplayState(val pin: String, val remainingSeconds: Long)
    private val random = SecureRandom()
    var pin: String = newPin()
        private set
    private var created = now()
    private val failures = linkedMapOf<String, MutableList<Long>>()
    private val globalFailures = mutableListOf<Long>()

    private fun newPin() = (100000 + random.nextInt(900000)).toString()

    @Synchronized fun begin(peer: String): Attempt? {
        refresh()
        globalFailures.removeAll { now() - it > 60000 }
        failures.entries.removeAll { entry -> entry.value.removeAll { now() - it > 60000 }; entry.value.isEmpty() }
        if (globalFailures.size >= 25 || (peer !in failures && failures.size >= 256)) return null
        val attempts = failures.getOrPut(peer) { mutableListOf() }
        if (attempts.size >= 5) return null
        attempts.add(now())
        globalFailures.add(now())
        return Attempt(pin, created)
    }
    @Synchronized fun valid(attempt: Attempt): Boolean {
        refresh()
        return attempt.pin == pin && attempt.created == created
    }
    @Synchronized fun succeeded(peer: String) { failures.remove(peer) }

    @Synchronized fun refresh(): Boolean = refreshAt(now())

    @Synchronized fun displayState(): DisplayState {
        val current = now()
        refreshAt(current)
        val remainingMs = (PIN_LIFETIME_MS - (current - created)).coerceIn(0L, PIN_LIFETIME_MS)
        return DisplayState(pin, (remainingMs + 999) / 1000)
    }

    private fun refreshAt(current: Long): Boolean {
        if (current - created < PIN_LIFETIME_MS) return false
        pin = newPin()
        created = current
        return true
    }

    @Synchronized fun accept(peer: String, candidate: String): Boolean {
        refresh()
        globalFailures.removeAll { now() - it > 60000 }
        if (globalFailures.size >= 25) return false
        failures.entries.removeAll { entry -> entry.value.removeAll { now() - it > 60000 }; entry.value.isEmpty() }
        if (peer !in failures && failures.size >= 256) return false
        val attempts = failures.getOrPut(peer) { mutableListOf() }
        attempts.removeAll { now() - it > 60000 }
        if (attempts.size >= 5) return false
        if (candidate != pin) {
            attempts.add(now())
            globalFailures.add(now())
            return false
        }
        failures.remove(peer)
        return true
    }
}
