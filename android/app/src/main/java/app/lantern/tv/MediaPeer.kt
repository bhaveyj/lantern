package app.lantern.tv

import java.net.URI
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager
import org.json.JSONObject

data class MediaPeer(val host: String, val port: Int, val fingerprint: String) {
    init {
        require(port in 1..65535 && Regex("[a-f0-9]{64}").matches(fingerprint)) { "Invalid media server identity" }
    }

    fun checkUrl(value: String): URI {
        val uri = URI(value)
        require(uri.scheme == "https" && uri.host == host && uri.port == port && uri.rawUserInfo == null && uri.rawFragment == null) { "Media URL does not belong to the paired sender" }
        return uri
    }

    fun trustManager(): X509TrustManager = object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<X509Certificate>?, authType: String?) { throw CertificateException("Client certificates unsupported") }
        override fun checkServerTrusted(chain: Array<X509Certificate>?, authType: String?) {
            val certificate = chain?.firstOrNull() ?: throw CertificateException("Missing media server certificate")
            certificate.checkValidity()
            val actual = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
            val expected = fingerprint.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
            if (!MessageDigest.isEqual(actual, expected)) throw CertificateException("Media server identity changed")
        }
    }

    companion object {
        fun fromPair(host: String, message: JSONObject) = MediaPeer(host, message.getInt("mediaPort"), message.getString("mediaFingerprint"))
    }
}
