package app.lantern.tv

/** Bounded JNI adapter; all SPAKE2+ arithmetic/confirmation is upstream BoringSSL. */
class NativePake(role: Int, pin: String, context: ByteArray) : AutoCloseable {
    companion object {
        private val registrationLock = Any()
        init { System.loadLibrary("lantern_pake") }
    }
    private external fun create(role: Int, pin: ByteArray, context: ByteArray): Long
    private external fun step(handle: Long, operation: Int, input: ByteArray): ByteArray?
    private external fun destroy(handle: Long)
    private var handle = synchronized(registrationLock) { create(role, pin.toByteArray(Charsets.US_ASCII), context) }
    init { require(handle != 0L) { "Secure pairing could not start" } }
    @Synchronized fun exchange(operation: Int, input: ByteArray = byteArrayOf()): ByteArray {
        check(handle != 0L)
        return step(handle, operation, input) ?: throw IllegalArgumentException("Pairing proof rejected")
    }
    @Synchronized override fun close() { if (handle != 0L) { destroy(handle); handle = 0 } }
}
