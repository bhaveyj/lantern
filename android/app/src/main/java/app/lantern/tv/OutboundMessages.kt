package app.lantern.tv

import java.io.Closeable
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Per-peer bounded delivery; superseded playback state never fills the queue. */
class OutboundMessages(
    private val send: (String) -> Unit,
    private val failed: () -> Unit,
    capacity: Int = 32
) : Closeable {
    private val closed = AtomicBoolean(false)
    private val latestState = AtomicReference<String?>(null)
    private val stateScheduled = AtomicBoolean(false)
    private val executor = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue<Runnable>(capacity)) { task ->
        Thread(task, "lantern-control-writer").apply { isDaemon = true }
    }

    fun publish(payload: String, state: Boolean = false) {
        if (closed.get()) return
        if (state) {
            latestState.set(payload)
            scheduleState()
        } else submit { send(payload) }
    }

    private fun scheduleState() {
        if (!closed.get() && stateScheduled.compareAndSet(false, true)) submit {
            try { latestState.getAndSet(null)?.let(send) }
            finally {
                stateScheduled.set(false)
                if (latestState.get() != null) scheduleState()
            }
        }
    }

    private fun submit(action: () -> Unit) {
        try {
            executor.execute {
                if (!closed.get()) try { action() } catch (_: Exception) { fail() }
            }
        } catch (_: RejectedExecutionException) { if (!closed.get()) fail() }
    }

    private fun fail() {
        if (closed.compareAndSet(false, true)) {
            latestState.set(null)
            executor.shutdownNow()
            failed()
        }
    }

    override fun close() {
        closed.set(true)
        latestState.set(null)
        executor.shutdownNow()
    }
}
