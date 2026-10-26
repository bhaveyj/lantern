package app.lantern.tv

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSpec
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext

/** HTTPS media with certificate pins supplied only by the authenticated sender. */
@androidx.media3.common.util.UnstableApi
class SecureMediaDataSource(private val peer: () -> MediaPeer?) : BaseDataSource(true) {
    private var connection: HttpsURLConnection? = null
    private var input: InputStream? = null
    private var uri: Uri? = null
    private var remaining = C.LENGTH_UNSET.toLong()
    private var started = false

    override fun open(dataSpec: DataSpec): Long {
        transferInitializing(dataSpec)
        val trusted = peer() ?: throw IOException("No authenticated media sender")
        val target = try { trusted.checkUrl(dataSpec.uri.toString()) } catch (_: Exception) { throw IOException("Untrusted media URL") }
        val manager = trusted.trustManager()
        val context = SSLContext.getInstance("TLSv1.2").apply { init(null, arrayOf(manager), null) }
        val opened = target.toURL().openConnection() as HttpsURLConnection
        connection = opened
        uri = dataSpec.uri
        opened.sslSocketFactory = context.socketFactory
        opened.hostnameVerifier = javax.net.ssl.HostnameVerifier { host, _ -> host == trusted.host }
        opened.instanceFollowRedirects = false
        opened.connectTimeout = 15000
        opened.readTimeout = 30000
        opened.setRequestProperty("Accept-Encoding", "identity")
        if (dataSpec.position != 0L || dataSpec.length != C.LENGTH_UNSET.toLong()) {
            val end = if (dataSpec.length == C.LENGTH_UNSET.toLong()) "" else (dataSpec.position + dataSpec.length - 1).toString()
            opened.setRequestProperty("Range", "bytes=${dataSpec.position}-$end")
        }
        try {
            val code = opened.responseCode
            if (code !in listOf(200, 206) || (dataSpec.position > 0 && code != 206)) throw IOException("Media request rejected (HTTP $code)")
            remaining = if (dataSpec.length != C.LENGTH_UNSET.toLong()) dataSpec.length else opened.contentLengthLong
            input = opened.inputStream
            started = true
            transferStarted(dataSpec)
            return remaining
        } catch (error: Exception) {
            close()
            throw error
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (remaining == 0L) return C.RESULT_END_OF_INPUT
        val count = input?.read(buffer, offset, if (remaining < 0) length else minOf(length.toLong(), remaining).toInt()) ?: return C.RESULT_END_OF_INPUT
        if (count < 0) {
            if (remaining > 0) throw EOFException("Media response ended early")
            return C.RESULT_END_OF_INPUT
        }
        if (remaining > 0) remaining -= count
        bytesTransferred(count)
        return count
    }

    override fun getUri(): Uri? = uri

    override fun getResponseHeaders(): Map<String, List<String>> = connection?.headerFields?.filterKeys { it != null } ?: emptyMap()

    override fun close() {
        try { input?.close() } finally {
            input = null
            connection?.disconnect()
            connection = null
            uri = null
            if (started) { started = false; transferEnded() }
        }
    }
}
