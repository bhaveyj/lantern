package app.lantern.tv

import org.junit.Assert.*
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class OutboundMessagesTest {
    @Test fun slowPeerKeepsOnlyLatestStateAndDeliversReliableMessages() {
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val completed = CountDownLatch(2)
        val delivered = Collections.synchronizedList(mutableListOf<String>())
        val writer = OutboundMessages({ message ->
            if (message == "initial") { started.countDown(); release.await(3, TimeUnit.SECONDS) }
            delivered.add(message)
            if (message == "999" || message == "reliable") completed.countDown()
        }, { fail("Queue should remain bounded without overflowing") })
        try {
            writer.publish("initial", true)
            assertTrue(started.await(1, TimeUnit.SECONDS))
            repeat(1000) { writer.publish(it.toString(), true) }
            writer.publish("reliable")
            release.countDown()
            assertTrue(completed.await(2, TimeUnit.SECONDS))
            assertEquals(listOf("initial", "reliable", "999"), delivered.toList())
        } finally { release.countDown(); writer.close() }
    }

    @Test fun reliableQueueOverflowDisconnectsInsteadOfGrowing() {
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val failed = CountDownLatch(1)
        val writer = OutboundMessages({ started.countDown(); release.await(3, TimeUnit.SECONDS) }, { failed.countDown() }, 2)
        try {
            writer.publish("blocked")
            assertTrue(started.await(1, TimeUnit.SECONDS))
            writer.publish("one")
            writer.publish("two")
            writer.publish("overflow")
            assertTrue(failed.await(1, TimeUnit.SECONDS))
        } finally { release.countDown(); writer.close() }
    }
}
