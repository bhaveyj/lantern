package app.lantern.tv

import android.media.MediaCodecList
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoWSD
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

class ControlServer(
    private val gate: PairingGate,
    private val dispatch: (JSONObject, () -> Boolean) -> Unit,
    private val connected: (String?) -> Unit,
    port: Int = 8765,
    private val pairingTimeoutMs: Long = 30000,
    maxClients: Int = 8,
    private val clients: PairedClientStore = MemoryPairedClients(),
    private val receiverFingerprint: String = "a".repeat(64),
    private val audioMimes: () -> List<String> = {
        MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.filter { !it.isEncoder }
            .flatMap { it.supportedTypes.toList() }.filter { it.startsWith("audio/") }.distinct()
    }
) : NanoWSD(port) {
    @Volatile private var owner: Peer? = null
    @Volatile var mediaPeer: MediaPeer? = null
        private set
    private val deadlines = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "lantern-pairing-deadline").apply { isDaemon = true } }

    init {
        setAsyncRunner(object : NanoHTTPD.DefaultAsyncRunner() {
            @Synchronized override fun exec(handler: NanoHTTPD.ClientHandler) {
                if (running.size >= maxClients) handler.close() else super.exec(handler)
            }
        })
    }

    fun publish(message: JSONObject) {
        val peer = owner ?: return
        peer.publish(message.toString(), message.optString("type") == "playback.state")
    }

    fun shutdown() {
        val previous = owner
        owner = null
        mediaPeer = null
        previous?.expire()
        super.stop()
        deadlines.shutdownNow()
    }

    fun forgetClient(id: String) {
        clients.remove(id)
        val previous = owner
        if (previous?.clientId == id) previous.expire()
    }
    fun disconnectSender() { owner?.expire() }

    override fun openWebSocket(handshake: NanoHTTPD.IHTTPSession): WebSocket = Peer(handshake)

    override fun serveHttp(session: NanoHTTPD.IHTTPSession): NanoHTTPD.Response =
        NanoHTTPD.newFixedLengthResponse(NanoHTTPD.Response.Status.OK, "application/json", "{\"name\":\"Lantern TV\",\"version\":3}")

    private inner class Peer(private val handshake: NanoHTTPD.IHTTPSession) : WebSocket(handshake) {
        @Volatile private var paired = false
        private val opened = System.nanoTime()
        private var deadline: ScheduledFuture<*>? = null
        private var intervalStarted = System.nanoTime()
        private var messages = 0
        private val pairing = PairingSession(gate, clients, receiverFingerprint, handshake.remoteIpAddress)
        var clientId: String? = null
            private set
        private val outbound = OutboundMessages({ payload -> if (paired && isOpen) send(payload) }, { expire() })

        override fun onOpen() {
            deadline = deadlines.schedule({ if (!paired) expire() }, pairingTimeoutMs, TimeUnit.MILLISECONDS)
        }

        fun publish(message: String, state: Boolean) {
            if (paired && isOpen) outbound.publish(message, state)
        }

        fun expire() {
            deadline?.cancel(false)
            outbound.close()
            try { handshake.inputStream.close() } catch (_: IOException) { }
            pairing.close()
        }

        override fun onMessage(frame: WebSocketFrame) {
            try {
                if (frame.opCode != WebSocketFrame.OpCode.Text || frame.binaryPayload.size > 1024 * 1024) {
                    close(WebSocketFrame.CloseCode.MessageTooBig, "Message too large", false)
                    return
                }
                val elapsed = System.nanoTime() - intervalStarted
                if (elapsed > TimeUnit.SECONDS.toNanos(1)) { intervalStarted = System.nanoTime(); messages = 0 }
                if (++messages > 30) { expire(); return }
                ControlLimits.json(frame.textPayload, if (paired) 1024 * 1024 else 8192)
                val message = JSONObject(frame.textPayload)
                if (!paired) {
                    require(System.nanoTime() - opened < TimeUnit.MILLISECONDS.toNanos(pairingTimeoutMs)) { "Pairing timed out. Try again" }
                    val response = pairing.receive(message)
                    if (response.optString("type") != "pair.ok") { send(response.toString()); return }
                    val trustedMedia = pairing.media
                    val acceptedClient = requireNotNull(pairing.client)
                    clientId = acceptedClient.id
                    send(response.put("audioMimes", JSONArray(audioMimes())).toString())
                    // Publish the acknowledgement before the ticker can send
                    // playback state to this peer during the pairing handshake.
                    paired = true
                    deadline?.cancel(false)
                    val previous = synchronized(this@ControlServer) {
                        val old = owner
                        owner = this
                        mediaPeer = trustedMedia
                        old
                    }
                    previous?.expire()
                    connected(acceptedClient.name)
                    return
                }
                if (owner !== this) return
                if (message.optString("type") == "pair.forget") {
                    clients.remove(requireNotNull(clientId))
                    send(JSONObject().put("type", "pair.forgotten").put("version", 3).toString())
                    expire()
                    return
                }
                ControlMessages.validate(message, requireNotNull(mediaPeer))
                dispatch(message) { owner === this && isOpen }
            } catch (error: Exception) {
                if (paired && owner === this && isOpen) {
                    publish(JSONObject().put("type", "error").put("code", "INVALID_COMMAND").put("message", "The TV rejected an invalid playback command.").toString(), false)
                } else {
                    val code = when (error) {
                        is PairingSession.PairingRequired -> "PAIRING_REQUIRED"
                        is PairingSession.RateLimited -> "RATE_LIMITED"
                        is PairingSession.CodeExpired -> "CODE_EXPIRED"
                        else -> "PAIRING_REJECTED"
                    }
                    try { send(JSONObject().put("type", "error").put("version", 3).put("code", code).put("message", "Pairing could not complete").toString()) } catch (_: Exception) { }
                    try { close(WebSocketFrame.CloseCode.InvalidFramePayloadData, "Invalid control message", false) } catch (_: Exception) { }
                }
            }
        }

        override fun onClose(code: WebSocketFrame.CloseCode?, reason: String?, initiatedByRemote: Boolean) {
            pairing.close()
            deadline?.cancel(false)
            outbound.close()
            val wasOwner = synchronized(this@ControlServer) {
                if (owner === this) { owner = null; mediaPeer = null; true } else false
            }
            if (wasOwner) connected(null)
        }
        override fun onPong(pong: WebSocketFrame) = Unit
        override fun onException(exception: IOException) = Unit
    }
}
