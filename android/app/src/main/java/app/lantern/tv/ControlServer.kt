package app.lantern.tv

import android.media.MediaCodecList
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoWSD
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

class ControlServer(
    private val gate: PairingGate,
    private val dispatch: (JSONObject, () -> Boolean) -> Unit,
    private val connected: (String?) -> Unit,
    port: Int = 8765,
    private val audioMimes: () -> List<String> = {
        MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.filter { !it.isEncoder }
            .flatMap { it.supportedTypes.toList() }.filter { it.startsWith("audio/") }.distinct()
    }
) : NanoWSD(port) {
    @Volatile private var owner: Peer? = null

    fun publish(message: JSONObject) {
        val peer = owner ?: return
        peer.publish(message.toString(), message.optString("type") == "playback.state")
    }

    fun shutdown() {
        val previous = owner
        owner = null
        previous?.expire()
        super.stop()
    }

    override fun openWebSocket(handshake: NanoHTTPD.IHTTPSession): WebSocket = Peer(handshake)

    override fun serveHttp(session: NanoHTTPD.IHTTPSession): NanoHTTPD.Response =
        NanoHTTPD.newFixedLengthResponse(NanoHTTPD.Response.Status.OK, "application/json", "{\"name\":\"Lantern TV\",\"version\":1}")

    private inner class Peer(private val handshake: NanoHTTPD.IHTTPSession) : WebSocket(handshake) {
        private var paired = false
        private var opened = System.currentTimeMillis()
        private val outbound = OutboundMessages({ payload -> if (paired && isOpen) send(payload) }, { expire() })

        override fun onOpen() = Unit

        fun publish(message: String, state: Boolean) {
            if (paired && isOpen) outbound.publish(message, state)
        }

        fun expire() {
            outbound.close()
            try { handshake.inputStream.close() } catch (_: IOException) { }
        }

        override fun onMessage(frame: WebSocketFrame) {
            try {
                if (frame.binaryPayload.size > 1024 * 1024) {
                    close(WebSocketFrame.CloseCode.MessageTooBig, "Message too large", false)
                    return
                }
                val message = JSONObject(frame.textPayload)
                if (!paired) {
                    ControlLimits.text(message.optString("name", "Windows PC"), 128)
                    val accepted = System.currentTimeMillis() - opened < 15000 &&
                        message.optString("type") == "pair" && message.optInt("version") == 1 &&
                        gate.accept(handshake.remoteIpAddress, message.optString("pin"))
                    if (!accepted) {
                        send(JSONObject().put("type", "error").put("message", "Code incorrect or expired. Read the code on the TV and try again.").toString())
                        close(WebSocketFrame.CloseCode.PolicyViolation, "Pairing rejected", false)
                        return
                    }
                    send(JSONObject().put("type", "pair.ok").put("version", 1).put("audioMimes", JSONArray(audioMimes())).toString())
                    // Publish the acknowledgement before the ticker can send
                    // playback state to this peer during the pairing handshake.
                    paired = true
                    val previous = synchronized(this@ControlServer) {
                        val old = owner
                        owner = this
                        old
                    }
                    previous?.expire()
                    connected(message.optString("name", "Windows PC"))
                    return
                }
                if (owner !== this) return
                ControlMessages.validate(message)
                dispatch(message) { owner === this && isOpen }
            } catch (_: Exception) {
                if (paired && owner === this && isOpen) {
                    publish(JSONObject().put("type", "error").put("code", "INVALID_COMMAND").put("message", "The TV rejected an invalid playback command.").toString(), false)
                } else {
                    try { close(WebSocketFrame.CloseCode.InvalidFramePayloadData, "Invalid control message", false) } catch (_: Exception) { }
                }
            }
        }

        override fun onClose(code: WebSocketFrame.CloseCode?, reason: String?, initiatedByRemote: Boolean) {
            outbound.close()
            val wasOwner = synchronized(this@ControlServer) {
                if (owner === this) { owner = null; true } else false
            }
            if (wasOwner) connected(null)
        }
        override fun onPong(pong: WebSocketFrame) = Unit
        override fun onException(exception: IOException) = Unit
    }
}
