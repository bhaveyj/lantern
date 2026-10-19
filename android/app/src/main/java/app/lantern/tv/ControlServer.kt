package app.lantern.tv

import android.media.MediaCodecList
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoWSD
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.Executors

class ControlServer(
    private val gate: PairingGate,
    private val dispatch: (JSONObject) -> Unit,
    private val connected: (String?) -> Unit
) : NanoWSD(8765) {
    @Volatile private var owner: Peer? = null
    private val writer = Executors.newSingleThreadExecutor()

    fun publish(message: JSONObject) {
        val peer = owner ?: return
        val payload = message.toString()
        if (!writer.isShutdown) writer.execute { peer.publish(payload) }
    }

    fun shutdown() { super.stop(); writer.shutdownNow() }

    override fun openWebSocket(handshake: NanoHTTPD.IHTTPSession): WebSocket = Peer(handshake)

    override fun serveHttp(session: NanoHTTPD.IHTTPSession): NanoHTTPD.Response =
        NanoHTTPD.newFixedLengthResponse(NanoHTTPD.Response.Status.OK, "application/json", "{\"name\":\"Lantern TV\",\"version\":1}")

    private inner class Peer(private val handshake: NanoHTTPD.IHTTPSession) : WebSocket(handshake) {
        private var paired = false
        private var opened = System.currentTimeMillis()

        override fun onOpen() = Unit

        @Synchronized fun publish(message: String) {
            if (paired && isOpen) {
                try { send(message) } catch (_: IOException) { }
            }
        }

        override fun onMessage(frame: WebSocketFrame) {
            try {
                if (frame.textPayload.length > 1024 * 1024) {
                    close(WebSocketFrame.CloseCode.MessageTooBig, "Message too large", false)
                    return
                }
                val message = JSONObject(frame.textPayload)
                if (!paired) {
                    val accepted = System.currentTimeMillis() - opened < 15000 &&
                        message.optString("type") == "pair" && message.optInt("version") == 1 &&
                        gate.accept(handshake.remoteIpAddress, message.optString("pin"))
                    if (!accepted) {
                        send(JSONObject().put("type", "error").put("message", "Code incorrect or expired. Read the code on the TV and try again.").toString())
                        close(WebSocketFrame.CloseCode.PolicyViolation, "Pairing rejected", false)
                        return
                    }
                    val audio = MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.filter { !it.isEncoder }
                        .flatMap { it.supportedTypes.toList() }.filter { it.startsWith("audio/") }.distinct()
                    send(JSONObject().put("type", "pair.ok").put("version", 1).put("audioMimes", JSONArray(audio)).toString())
                    // Publish the acknowledgement before the ticker can send
                    // playback state to this peer during the pairing handshake.
                    paired = true
                    val previous = owner
                    owner = this
                    previous?.close(WebSocketFrame.CloseCode.NormalClosure, "Replaced by a new sender", false)
                    connected(message.optString("name", "Windows PC"))
                    return
                }
                dispatch(message)
            } catch (_: Exception) {
                try { close(WebSocketFrame.CloseCode.InvalidFramePayloadData, "Invalid control message", false) } catch (_: Exception) { }
            }
        }

        override fun onClose(code: WebSocketFrame.CloseCode?, reason: String?, initiatedByRemote: Boolean) {
            if (owner === this) {
                owner = null
                connected(null)
            }
        }
        override fun onPong(pong: WebSocketFrame) = Unit
        override fun onException(exception: IOException) = Unit
    }
}
